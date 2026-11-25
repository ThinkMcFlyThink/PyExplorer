"""
BrowserTab - one tab: navigation row (back/forward/up, breadcrumbs, a Search button),
the instant-filter bar, and the file view.

It is the "controller" for its folder: it asks the scanner for entries,
keeps them in a FolderModel (core, plain Python: sorting + filtering), and
hands the finished list to a Qt FileModel that the current view displays.
Views never load files themselves, so view modes can be swapped any time.

Loading is streamed and never blocks:
  * entries arrive in batches from a worker thread;
  * batches are merged at most every ~50 ms (adaptive), so a 100k-file folder
    shows its first files instantly and stays scrollable while loading;
  * filtering huge lists happens in a worker thread too.
"""
from __future__ import annotations

import time
from operator import attrgetter
from typing import List, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from ..core.events import Topics
from ..core.fileinfo import FileEntry
from ..core.folder_model import FolderModel, filter_entries
from ..core.formatting import human_size, items_label
from ..core.history import History
from ..core.platform_info import home
from ..core.query import safe_compile
from ..core.sorting import key_func
from .breadcrumb import BreadcrumbBar
from .models.file_model import FileModel
from .views.registry import view_class

ASYNC_FILTER_THRESHOLD = 25_000
def _tool_button(icon: QIcon, tip: str, callback, parent) -> QToolButton:
    b = QToolButton(parent)
    b.setIcon(icon)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.clicked.connect(callback)
    return b


class _KeyFilter(QObject):
    """Small helper: route Escape / Down in the search and filter boxes."""

    def __init__(self, on_escape, on_down, parent) -> None:
        super().__init__(parent)
        self.on_escape, self.on_down = on_escape, on_down

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.on_escape()
                return True
            if event.key() == Qt.Key.Key_Down:
                self.on_down()
                return True
        return False


class BrowserTab(QWidget):
    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        super().__init__()
        state = dict(state or {})
        self.ctx = ctx
        self.panel = panel
        self.path: str = state.get("path") or home()
        self.history = History.from_dict(state.get("history"))
        if self.history.current is None:
            self.history.current = self.path
        s = ctx.settings
        self.listing = FolderModel(s.get("view.sort_key"), s.get("view.sort_reverse"),
                                   s.get("view.folders_first"), s.get("view.show_hidden"))
        self.qmodel = FileModel(ctx, self)
        self.qmodel.loader = self.load_children
        self.qmodel.prepare = self.prepare_children
        self.qmodel.rename_handler = lambda entry, name: ctx.commands.rename_to(self, entry.path, name)
        self.filter_text = ""
        self.status_note = ""
        self.view = None
        self.view_mode: Optional[str] = None
        self._view_state: dict = state.get("view_state") or {}
        self._restore: Optional[dict] = {"selection": state.get("selection") or [], "top": state.get("top")}
        self._select_after_load: Optional[List[str]] = None
        self._scan_token = None
        self._incoming: List[FileEntry] = []
        self._first_batch = True
        self._soft = False
        self._loading = False
        self._flush_pending = False
        self._flush_cost = 0.0
        self._scan_id = 0
        self._refresh_again = False

        self._build()
        bus = ctx.bus
        self._unsubs = [
            bus.subscribe(Topics.VIEW_MODE_CHANGED, lambda mode: self.ensure_view()),
            bus.subscribe(Topics.ZOOM_CHANGED, self._on_zoom_changed),
            bus.subscribe(Topics.SHOW_HIDDEN_CHANGED, self._on_hidden_changed),
            bus.subscribe(Topics.SORT_CHANGED, self._on_sort_changed),
            bus.subscribe(Topics.SETTINGS_CHANGED, self._on_setting_changed),
            bus.subscribe(Topics.THEME_CHANGED, lambda **_: self._refresh_look()),
            bus.subscribe(Topics.FOLDER_COLORS_CHANGED, lambda **_: self._refresh_look()),
            bus.subscribe(Topics.CLIPBOARD_CHANGED, lambda **_: self._refresh_look()),
            bus.subscribe(Topics.DEVICES_CHANGED, self._on_devices_changed),
            bus.subscribe(Topics.DRIVE_USAGE_CHANGED, self._on_drive_usage),
            bus.subscribe(Topics.FORMAT_CHANGED, lambda **_: (self._refresh_look(), self._publish_selection())),
        ]
        self.breadcrumb.set_path(self.path)
        self.ensure_view()
        self._start_scan()

    # ======================================================================
    # Widgets
    # ======================================================================
    def _build(self) -> None:
        icons = self.ctx.icons
        SP = QStyle.StandardPixmap
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Two rows on one grid: the filter field sits exactly under the address bar,
        # and its close button under the search button.
        #   [← → ↑ ↻] [ / › home › me ›          ] [⌕]
        #      Filter [ type to show only…       ] [×]
        nav = QFrame(self, objectName="NavBar")
        row = QHBoxLayout(nav)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self.nav_buttons = QWidget(nav)
        brow = QHBoxLayout(self.nav_buttons)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(2)
        self.btn_back = _tool_button(icons.symbol("back", "go-previous", SP.SP_ArrowBack), "Back (Alt+Left)", self.go_back, nav)
        self.btn_fwd = _tool_button(icons.symbol("forward", "go-next", SP.SP_ArrowForward), "Forward (Alt+Right)", self.go_forward, nav)
        self.btn_up = _tool_button(icons.symbol("up", "go-up", SP.SP_ArrowUp), "Up (Alt+Up)", self.go_up, nav)
        btn_refresh = _tool_button(icons.symbol("refresh", "view-refresh", SP.SP_BrowserReload), "Refresh (F5)",
                                   lambda: self.refresh(), nav)
        for b in (self.btn_back, self.btn_fwd, self.btn_up, btn_refresh):
            brow.addWidget(b)
        row.addWidget(self.nav_buttons)
        self.breadcrumb = BreadcrumbBar(self.ctx, self._on_crumb, nav)
        row.addWidget(self.breadcrumb, 1)
        # Searching happens in the Search tab (ui/utility/search_tab.py).
        row.addWidget(_tool_button(icons.symbol("search", "edit-find", SP.SP_FileDialogContentsView),
                                   "Search in this folder (Ctrl+F)",
                                   lambda: self.ctx.commands.open_search([self.path]) if self.current_folder
                                   else self.ctx.commands.open_search(), nav))
        outer.addWidget(nav)

        self.filter_bar = QFrame(self, objectName="FilterBar")
        frow = QHBoxLayout(self.filter_bar)
        frow.setContentsMargins(4, 0, 4, 4)
        frow.setSpacing(2)
        self.filter_label = QLabel("Filter", self.filter_bar, objectName="Dim")
        self.filter_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.filter_label.setContentsMargins(0, 0, 8, 0)
        frow.addWidget(self.filter_label)
        self.filter_edit = QLineEdit(self.filter_bar)
        self.filter_edit.setPlaceholderText("Type to show only matching items (same syntax as search)")
        self.filter_edit.textChanged.connect(self._on_filter_typed)
        self.filter_edit.returnPressed.connect(self.focus_view)
        self.filter_edit.installEventFilter(_KeyFilter(self.hide_filter, self.focus_view, self))
        frow.addWidget(self.filter_edit, 1)
        frow.addWidget(_tool_button(icons.symbol("close", "window-close", SP.SP_TitleBarCloseButton),
                                    "Close filter (Esc)", self.hide_filter, self.filter_bar))
        self.filter_bar.hide()
        outer.addWidget(self.filter_bar)

        self.view_slot = QVBoxLayout()
        self.view_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.view_slot, 1)

    # ======================================================================
    # View switching
    # ======================================================================
    def ensure_view(self) -> None:
        """Create the view for the global view mode (or swap it if the mode changed)."""
        mode = self.ctx.settings.get("view.mode", "details")
        if self.view is not None and self.view_mode == mode:
            return
        selected, top, had_focus = [], None, False
        old = self.view
        if old is not None:
            selected = [e.path for e in old.selected_entries()]
            top = old.top_path()
            had_focus = old.hasFocus()
            self._view_state = old.get_state() or self._view_state
            old.detach_model()
            self.view_slot.removeWidget(old)
            old.hide()
            old.deleteLater()
        zoom = self.ctx.settings.get(f"view.zoom.{mode}", 1)
        self.view = view_class(mode)(self.ctx, self, zoom, self)
        self.view_mode = mode
        self.view_slot.addWidget(self.view)
        self._update_columns()
        self.view.attach_model(self.qmodel)
        if self._view_state:
            self.view.set_state(self._view_state)
        if selected:
            self.view.select_paths(selected, scroll=False)
        if top:
            self.view.scroll_to_path(top)
        self._show_message_if_empty()
        if had_focus:
            self.view.setFocus()

    def _update_columns(self) -> None:
        cols = ["name"] + [c for c in self.ctx.settings.get("view.visible_columns", ["name", "size", "type", "modified"])
                           if c in ("size", "type", "modified", "status")]
        self.qmodel.set_columns(cols)

    def focus_view(self) -> None:
        if self.view is not None:
            self.view.setFocus()

    def _refresh_look(self) -> None:
        if self.view is not None:
            self.view.refresh_look()

    # ======================================================================
    # Properties used by the panel / tab bar / commands
    # ======================================================================
    @property
    def title(self) -> str:
        try:
            return self.ctx.vfs.resolve(self.path).basename(self.path)
        except OSError:
            return self.path

    @property
    def icon(self) -> QIcon:
        if self.path.startswith("computer://"):
            return self.ctx.icons.special("computer")
        if self.ctx.vfs.is_remote(self.path) and self.ctx.vfs.resolve(self.path).parent(self.path) is None:
            return self.ctx.icons.special("network")
        root = self.ctx.cloud.root_at(self.path)
        if root is not None:
            return self.ctx.icons.cloud_app(root)
        return self.ctx.icons.folder_for(self.path)

    @property
    def current_folder(self) -> Optional[str]:
        """Where pasting / dropping / "new folder" go. None for Computer."""
        if self.path.startswith("computer://"):
            return None
        return self.path

    search_mode = False       # True only in the Search tab (it shows a Location column)
    search_text = None

    def selected_entries(self) -> List[FileEntry]:
        return self.view.selected_entries() if self.view else []

    def summary(self) -> str:
        if self._loading and self.listing.total == 0:
            return "Loading…"
        count = self.listing.total if self.listing.needs_filter else len(self.listing.visible)
        text = items_label(count)
        if self.filter_text:
            text += f" (filtered from {self.listing.total:,})"
        sel = self.selected_entries()
        if sel:
            size = sum(e.size for e in sel if not e.is_dir)
            text += f"  ·  {len(sel):,} selected" + (f" ({human_size(size)})" if size else "")
        if self._loading:
            text += "  ·  loading…"
        if self.status_note:
            text += "  ·  " + self.status_note
        return text

    # ======================================================================
    # Navigation
    # ======================================================================
    def navigate(self, path: str, record: bool = True, select: Optional[List[str]] = None) -> None:
        if path.startswith("device://"):                  # a drive that isn't mounted: mount it, then come here
            self.ctx.commands.open_unmounted(path, tab=self)
            return
        try:
            path = self.ctx.vfs.from_user_input(path)
            path = self.ctx.vfs.resolve(path).normalize(path)
        except OSError as exc:
            self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=str(exc), level="error")
            return
        if record:
            self.history.visit(path)
        else:
            self.history.current = path
        changed = path != self.path
        was_computer = self.path.startswith("computer://")
        self.path = path
        if changed:
            if self.filter_text:
                self.hide_filter(focus=False)
            self.qmodel.clear_children()
            if hasattr(self.view, "reset_tree"):
                self.view.reset_tree()
            self._view_state = {}
            if self.view is not None and was_computer != path.startswith("computer://"):
                self.view.apply_zoom()          # the Computer view's rows have room for usage bars
        self.breadcrumb.set_path(path)
        self._select_after_load = select
        self._start_scan(new_folder=changed)
        self.panel.tab_updated(self)

    def _on_crumb(self, path: str, new_tab: bool) -> None:
        if new_tab:
            self.panel.open_tab(path, activate=False)
        else:
            self.navigate(path)
            self.focus_view()

    def go_back(self) -> None:
        path = self.history.back()
        if path:
            self.navigate(path, record=False)

    def go_forward(self) -> None:
        path = self.history.forward()
        if path:
            self.navigate(path, record=False)

    def go_up(self) -> None:
        parent = self.ctx.vfs.resolve(self.path).parent(self.path)
        if parent:
            self.navigate(parent, select=[self.path])     # re-select the folder we came from

    def _on_drive_usage(self, path: str = "", **_) -> None:
        """The Computer view shows how full each drive is: repaint when a number arrives."""
        if self.path.startswith("computer://") and self.view is not None:
            if not path:                        # bars switched on or off: the rows change height
                self.view.apply_zoom()
            self.view.viewport().update()

    def _on_devices_changed(self, **_) -> None:
        """A drive/mount/cloud folder came or went: the Computer view lists them."""
        if self.path.startswith("computer://"):
            self.refresh()
        index = self.panel.tab_widget.indexOf(self)    # the tab's icon may now be a cloud app's
        if index >= 0:
            self.panel.tab_widget.setTabIcon(index, self.icon)

    def refresh(self, soft: bool = True) -> None:
        self._start_scan(soft=soft)
        self.qmodel.reload_children()

    def on_folder_changed(self, folder: str) -> None:
        """Something changed inside ``folder`` (file operation or watcher)."""
        if folder == self.path:
            if self._loading:
                self._refresh_again = True       # re-check once the current load finishes
            else:
                self.refresh(soft=True)
        elif folder in self.qmodel.loaded_folders():
            self.qmodel.reload_children(folder)

    def _update_nav_buttons(self) -> None:
        self.btn_back.setEnabled(self.history.can_back())
        self.btn_fwd.setEnabled(self.history.can_forward())
        try:
            self.btn_up.setEnabled(self.ctx.vfs.resolve(self.path).parent(self.path) is not None)
        except OSError:
            self.btn_up.setEnabled(False)

    # ======================================================================
    # Loading (streaming)
    # ======================================================================
    def _start_scan(self, soft: bool = False, new_folder: bool = False) -> None:
        if self._scan_token:
            self._scan_token.cancel()
        self._update_nav_buttons()
        self._scan_id += 1
        scan_id = self._scan_id
        self._soft = soft
        self._first_batch = True
        self._incoming = []
        self._loading = True
        self._new_folder = new_folder
        if not soft:
            # Keep showing the old folder for a moment; if the new one is slow,
            # clear it and say "Loading…" (avoids flicker on fast folders).
            def clear_if_slow() -> None:
                if scan_id == self._scan_id and self._first_batch and self._loading:
                    self.listing.reset()
                    self._set_message("Loading…")
                    self.qmodel.set_entries([])
                    self._publish_selection()
            QTimer.singleShot(150, clear_if_slow)
        self._scan_token = self.ctx.scanner.scan(self.path, self._on_batch, self._on_scan_done, self._on_scan_error)
        self._publish_selection()

    def _on_batch(self, batch: List[FileEntry]) -> None:
        self._incoming.extend(batch)
        if self._soft:
            return                              # soft refresh: swap everything in at the end
        if self._first_batch:
            self._first_batch = False
            self.listing.reset()
            self._set_message("")
            if self._new_folder and self.view is not None:
                self.qmodel.set_entries([])     # new folder: start at the top, no old selection
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if not self._flush_pending:
            self._flush_pending = True
            # Back off when merging is slow, so the window stays responsive
            # (only really happens in Tree view with 100,000+ items).
            delay = max(40, min(3000, int(self._flush_cost * 3000)))
            QTimer.singleShot(delay, self._flush)

    def _flush(self) -> None:
        self._flush_pending = False
        if not self._incoming:
            return
        started = time.perf_counter()
        batch, self._incoming = self._incoming, []
        self.listing.add(batch)
        self._push_to_view()
        if self.view_mode == "tree" and self.view is not None:
            self.view.doItemsLayout()          # tree layout cost is per row: include it in the measurement
        self._flush_cost = time.perf_counter() - started

    def _on_scan_done(self, count: int) -> None:
        self._loading = False
        batch, self._incoming = self._incoming, []
        if self._soft or self._first_batch:
            self.listing.reset(batch)
        elif batch:
            self.listing.add(batch)
        self._first_batch = False
        if self.listing.generation != getattr(self, "_pushed_gen", None):     # skip a pointless refresh
            self._push_to_view(sync=self.listing.total <= ASYNC_FILTER_THRESHOLD)
        self._set_message(self._empty_message())
        restore, self._restore = self._restore, None
        if restore and self.view is not None:
            if restore.get("selection"):
                self.view.select_paths(restore["selection"], scroll=False)
            if restore.get("top"):
                self.view.scroll_to_path(restore["top"])
        if self._select_after_load and self.view is not None:
            self.view.select_paths(self._select_after_load)
            self._select_after_load = None
        self.ctx.watcher.forget(self.path)
        self._publish_selection()
        if self._refresh_again:
            self._refresh_again = False
            self.refresh(soft=True)

    def _on_scan_error(self, exc: BaseException) -> None:
        self._loading = False
        self.listing.reset()
        self.qmodel.set_entries([])
        self._set_message(f"Cannot open this location\n\n{exc}")
        self._publish_selection()

    def _push_to_view(self, sync: bool = False) -> None:
        if not sync and self.listing.needs_filter and self.listing.total > ASYNC_FILTER_THRESHOLD:
            gen = self.listing.generation
            self.ctx.runner.submit(filter_entries, *self.listing.snapshot(), pool="misc",
                                   on_done=lambda visible: self._filtered(visible, gen))
            return
        self.qmodel.set_entries(self.listing.visible)
        self._pushed_gen = self.listing.generation
        self._show_message_if_empty()
        self._publish_selection()

    def _filtered(self, visible: List[FileEntry], gen: int) -> None:
        if self.listing.set_visible(visible, gen):
            self.qmodel.set_entries(visible)
            self._pushed_gen = gen
            self._show_message_if_empty()
            self._publish_selection()

    def _empty_message(self) -> str:
        """What to say when nothing is shown."""
        if self.listing.total == 0:
            return "This folder is empty"
        if self.filter_text.strip():
            return "No items match the filter"
        return "Only hidden items here\n(press Ctrl+Shift+H to show them)"

    def _set_message(self, text: str) -> None:
        self._message = text
        self._show_message_if_empty()

    def _show_message_if_empty(self) -> None:
        if self.view is not None:
            self.view.set_message(getattr(self, "_message", "") if self.qmodel.rowCount() == 0 else "")

    def _publish_selection(self) -> None:
        self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=self)

    # ======================================================================
    # Global setting changes
    # ======================================================================
    def _on_zoom_changed(self, mode: str, level: int) -> None:
        if self.view is not None and self.view_mode == mode:
            self.view.set_zoom(level)

    def _on_hidden_changed(self, value: bool) -> None:
        self.listing.set_show_hidden(value)
        self._push_to_view()
        if not self._loading:
            self._set_message(self._empty_message())

    def _on_sort_changed(self, key: str, reverse: bool, folders_first: bool) -> None:
        self.listing.set_sort(key, reverse, folders_first)
        self._push_to_view()

    def _on_setting_changed(self, key: str, value=None) -> None:
        if key == "view.visible_columns":
            self._update_columns()
        elif key in ("view.thumbnails", "view.list_cell_width") and self.view is not None:
            self.view.apply_zoom()
        elif key == "view.alternate_rows" and self.view is not None and self.view_mode in ("details", "tree"):
            self.view.setAlternatingRowColors(bool(value))
        elif key == "appearance.system_icons":
            self._refresh_look()

    # ======================================================================
    # Instant filter
    # ======================================================================
    def show_filter(self, initial: str = "") -> None:
        # The label is as wide as the nav buttons above it, so the field starts where the address bar does.
        self.filter_label.setFixedWidth(self.nav_buttons.sizeHint().width())
        self.filter_bar.show()
        self.filter_edit.setFocus()
        if initial:
            self.filter_edit.setText(self.filter_edit.text() + initial)
        self.filter_edit.end(False)

    def hide_filter(self, focus: bool = True) -> None:
        self.filter_edit.clear()
        self.filter_bar.hide()
        if focus:
            self.focus_view()

    def toggle_filter(self) -> None:
        if self.filter_bar.isVisible():
            self.hide_filter()
        else:
            self.show_filter()

    def _on_filter_typed(self, text: str) -> None:
        self.filter_text = text
        text = text.strip()
        self.listing.set_query(safe_compile(text) if text else None)
        self._push_to_view()
        if not self._loading:
            self._set_message(self._empty_message())

    # ======================================================================
    # View host callbacks (see ui/views/base.py)
    # ======================================================================
    def on_view_activate(self, entries: List[FileEntry], new_tab: bool) -> None:
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        if dirs:
            if new_tab or len(dirs) > 1:
                for d in dirs:
                    self.panel.open_tab(d.path, activate=False)
            else:
                self.navigate(dirs[0].path)
        for f in files[:20]:                   # guard against opening 5000 files by accident
            self.ctx.commands.open_file(f.path)

    def on_view_context_menu(self, global_pos, entries: List[FileEntry]) -> None:
        self.ctx.commands.show_context_menu(self, global_pos, entries)

    def on_view_selection_changed(self) -> None:
        self._publish_selection()

    def on_view_zoom(self, delta: int) -> None:
        self.ctx.commands.zoom(delta)

    def on_view_type(self, text: str) -> None:
        self.show_filter(text)

    def on_view_focus(self) -> None:
        self.panel.set_active_panel()

    def on_view_sort(self, column: str) -> None:
        self.ctx.commands.set_sort(column)

    def on_view_drop(self, paths: List[str], dest: str, op: str) -> None:
        self.ctx.commands.drop_files(paths, dest, op)

    def load_children(self, path: str, callback) -> None:
        self.ctx.scanner.list_all(path, callback, lambda exc: callback([]))

    def prepare_children(self, entries: List[FileEntry]) -> List[FileEntry]:
        """Sort + filter a sub-folder's entries the same way as the main list (tree view)."""
        m = self.listing
        keyf = key_func(m.sort_key)
        for e in entries:
            e.sk = keyf(e)
        sk = attrgetter("sk")
        dirs = sorted((e for e in entries if e.is_dir), key=sk, reverse=m.reverse)
        files = sorted((e for e in entries if not e.is_dir), key=sk, reverse=m.reverse)
        return filter_entries(dirs, files, m.query, m.show_hidden, m.folders_first, m.reverse)

    # ======================================================================
    # Session state
    # ======================================================================
    def get_state(self) -> dict:
        view_state = (self.view.get_state() if self.view is not None else None) or self._view_state
        return {
            "path": self.path,
            "history": self.history.to_dict(),
            "selection": [e.path for e in self.selected_entries()][:500],
            "top": self.view.top_path() if self.view is not None else None,
            "view_state": view_state,
        }

    def shutdown(self) -> None:
        """Called before the tab is closed: stop background work and listeners."""
        if self._scan_token:
            self._scan_token.cancel()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self.view is not None:
            self.view.detach_model()

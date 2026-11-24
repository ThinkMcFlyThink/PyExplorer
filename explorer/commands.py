"""
Commands - every user-level operation, in one place.

Menus, keyboard shortcuts, toolbar buttons, context menus and drag & drop
all end up calling a method here. Most methods work on the ACTIVE tab
unless told otherwise. Commands never draw anything themselves - they call
services (file operations, search...) and publish events; widgets react.

If you want to add a feature, this is usually the file to start in.
"""
from __future__ import annotations

from typing import Callable, Iterable, List, Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog

from . import APP_NAME, APP_VERSION
from .core.events import Topics
from .core.fileinfo import FileEntry
from .core.platform_info import IS_LINUX, home
from .core.settings import DEFAULTS
from .services import platform_ops
from .ui import dialogs, system_clipboard
from .ui.context_menu import build_background_menu, build_item_menu
from .ui.views.registry import view_class


class Commands:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    # ======================================================================
    # Helpers
    # ======================================================================
    @property
    def area(self):
        return self.ctx.main.panel_area

    def active_tab(self):
        main = self.ctx.main
        if main is None or self.area.active_panel is None:
            return None
        return self.area.active_panel.active_tab

    def all_tabs(self):
        for panel in self.area.panels:
            yield from panel.tabs

    def focus_in_files(self) -> bool:
        """Is the keyboard focus in the file panels (not the sidebar/terminal)?"""
        focus = QApplication.focusWidget()
        return focus is None or focus is self.ctx.main or self.area.isAncestorOf(focus)

    def _selection(self) -> List[FileEntry]:
        tab = self.active_tab()
        return tab.selected_entries() if tab else []

    def _selected_paths(self, paths: Optional[Iterable[str]] = None) -> List[str]:
        return list(paths) if paths is not None else [e.path for e in self._selection()]

    def status(self, text: str, level: str = "info") -> None:
        self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=text, level=level)

    def _window(self):
        return self.ctx.main

    # ======================================================================
    # Tabs, panels, window
    # ======================================================================
    def new_tab(self, path: Optional[str] = None) -> None:
        if self.area.active_panel:
            tab = self.area.active_panel.open_tab(path, activate=True)
            tab.focus_view()

    def close_tab(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.panel.close_tab(tab)

    def reopen_closed_tab(self) -> None:
        if self.ctx.closed_tabs and self.area.active_panel:
            self.area.active_panel.open_tab(state=self.ctx.closed_tabs.pop(), activate=True)

    def duplicate_tab(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.panel.open_tab(state=tab.get_state(), activate=True)

    def next_tab(self, delta: int) -> None:
        if self.area.active_panel:
            self.area.active_panel.cycle_tab(delta)

    def move_tab_to_other_panel(self, tab=None) -> None:
        tab = tab or self.active_tab()
        if tab is None:
            return
        if not self.area.is_split:
            if tab is not self.active_tab():
                tab.panel.activate(tab.panel.tab_widget.indexOf(tab))
            self.area.split()           # splitting moves the current tab to the right
        else:
            tab.panel.move_tab_to_panel(tab, self.area.other_panel(tab.panel))
        self.ctx.actions.sync_checks()

    def toggle_split(self) -> None:
        self.area.toggle_split()
        self.ctx.actions.sync_checks()

    def focus_other_panel(self) -> None:
        other = self.area.other_panel()
        if other and other.active_tab:
            other.set_active_panel(force_event=True)
            other.active_tab.focus_view()

    def open_in_other_panel(self, path: str) -> None:
        if not self.area.is_split:
            self.area.split()
        other = self.area.other_panel()
        if other:
            other.open_tab(path, activate=True)

    def toggle_terminal(self) -> None:
        self.ctx.main.set_terminal_visible(not self.ctx.main.terminal_visible)

    def toggle_sidebar(self) -> None:
        self.ctx.main.set_sidebar_visible(not self.ctx.main.sidebar_visible)

    def toggle_toolbar(self) -> None:
        visible = not self.ctx.settings.get("appearance.show_toolbar", True)
        self.ctx.settings.set("appearance.show_toolbar", visible)
        self.ctx.main.toolbar.setVisible(visible)

    def toggle_statusbar(self) -> None:
        visible = not self.ctx.settings.get("appearance.show_statusbar", True)
        self.ctx.settings.set("appearance.show_statusbar", visible)
        self.ctx.main.statusBar().setVisible(visible)

    def toggle_fullscreen(self) -> None:
        main = self.ctx.main
        main.showNormal() if main.isFullScreen() else main.showFullScreen()

    def quit(self) -> None:
        self.ctx.main.close()

    # ======================================================================
    # Navigation
    # ======================================================================
    def _tab_call(self, method: str, *args) -> None:
        tab = self.active_tab()
        if tab:
            getattr(tab, method)(*args)

    def go_back(self) -> None:
        self._tab_call("go_back")

    def go_forward(self) -> None:
        self._tab_call("go_forward")

    def go_up(self) -> None:
        self._tab_call("go_up")

    def go_home(self) -> None:
        self._tab_call("navigate", home())

    def go_computer(self) -> None:
        self._tab_call("navigate", "computer://")

    def refresh(self) -> None:
        tab = self.active_tab()
        if tab:
            if tab.current_folder:
                self.ctx.search.folder_changed(tab.current_folder)   # keep the search index current
            tab.refresh(soft=True)

    def edit_location(self) -> None:
        tab = self.active_tab()
        if tab and hasattr(tab, "breadcrumb"):
            tab.breadcrumb.start_edit()

    # ======================================================================
    # Utility tabs (ui/utility/)
    # ======================================================================
    def _utility_here(self, kind: str):
        """The current tab if it is already this utility."""
        tab = self.active_tab()
        return tab if getattr(tab, "KIND", None) == kind else None

    def open_search(self, locations: Optional[List[str]] = None, name: str = "") -> None:
        """Ctrl+F: the Search tab, set to search the current folder (or focus the
        Search tab if that is the current tab already)."""
        tab = self._utility_here("search")
        if tab is not None and locations is None:
            tab.focus_query()
            return
        current = self.active_tab()
        whole = False
        if locations is None:
            folder = current.current_folder if current else None
            if current is not None and current.path.startswith("computer://"):
                whole = True                     # searching from "Computer": everything
            locations = [folder] if folder else []
        spec = {"locations": locations, "whole_computer": whole or not locations, "name": name}
        self.area.active_panel.open_utility("search", {"spec": spec})

    def open_rename(self, paths: Optional[List[str]] = None) -> None:
        """Ctrl+F2: the Rename tab for the current folder - or for exactly the
        selected items when several are selected (also from search results)."""
        tab = self._utility_here("rename")
        if tab is not None and paths is None:
            tab.focus_view()
            return
        current = self.active_tab()
        if paths is None:
            paths = [e.path for e in self._selection()]
        folder = current.current_folder if current else None
        if len(paths) > 1 or not folder:
            if not paths:
                self.status("Select some files to rename, or open a folder first")
                return
            state = {"files": paths}
        else:
            state = {"folder": folder, "checked": paths}
        self.area.active_panel.open_utility("rename", state)

    def start_transfer(self, paths: List[str], dest: str, move: bool) -> None:
        """Every copy/move (paste, drop, to the other panel). PyExplorer's own engine with the
        transfer window, unless Preferences -> Transfers -> engine is "simple"."""
        if self.ctx.transfers is not None and self.ctx.settings.get("transfers.engine", "pyexplorer") != "simple":
            self.ctx.transfers.start(paths, dest, move)
        else:
            self.ctx.fileops.transfer(paths, dest, move=move)

    def open_transfers(self) -> None:
        """Ctrl+J: show the transfer window / tab (with the list of transfers so far)."""
        if self.ctx.transfer_ui is not None:
            self.ctx.transfer_ui.show(activate=True)

    def open_history(self) -> None:
        """Ctrl+H: the History tab. There is only one - if it is open somewhere, go to it."""
        for tab in self.all_tabs():
            if getattr(tab, "KIND", None) == "history":
                tab.panel.activate(tab.panel.tab_widget.indexOf(tab))
                return
        self.area.active_panel.open_utility("history")

    def undo_history(self, record_ids: List[str], indexes: Optional[dict] = None,
                     on_done: Optional[Callable[[], None]] = None) -> None:
        """Undo History records, newest first, one after the other (a later change may
        depend on an earlier one: rename a->b then move b). ``indexes`` = {record id:
        [item indexes]} to undo only some items of a record."""
        history = self.ctx.history
        records = sorted((r for r in (history.get(i) for i in record_ids) if r is not None),
                         key=lambda r: r.time, reverse=True)
        queue = []
        for rec in records:
            wanted = set((indexes or {}).get(rec.id) or rec.pending())
            todo = [i for i in rec.pending() if i in wanted]
            if todo:
                queue.append((rec, todo))
        if not queue:
            self.status("Nothing here can be undone")
            if on_done:
                on_done()
            return
        totals = {"ok": 0, "failed": []}

        def next_one() -> None:
            if not queue:
                failed = totals["failed"]
                text = f"Undone {totals['ok']:,} item{'s' if totals['ok'] != 1 else ''}"
                if failed:
                    text += f" - {len(failed)} could not be: " + "; ".join(failed[:3])
                self.status(text, "error" if failed else "info")
                if on_done:
                    on_done()
                return
            rec, todo = queue.pop(0)
            items = [rec.items[i] for i in todo]

            def finished(results: dict, rec=rec, todo=todo, items=items) -> None:
                ok = [todo[n] for n, err in results.items() if not err]
                history.mark_undone(rec.id, ok)
                if rec.kind in ("rename", "move"):
                    for n, err in results.items():
                        if not err:
                            self.ctx.folder_colors.rename(items[n]["dst"], items[n]["src"])
                totals["ok"] += len(ok)
                totals["failed"].extend(err for err in results.values() if err)
                next_one()
            self.ctx.fileops.undo(rec.kind, items, on_done=finished)
        next_one()

    def update_search_index(self) -> None:
        """Rebuild the search index of the current location (or the whole computer)."""
        tab = self.active_tab()
        folder = tab.current_folder if tab else None
        roots = [self.ctx.search.indexes.index_for(folder).root] if folder and self.ctx.search.indexes.index_for(folder) \
            else self.ctx.search.indexes.computer_roots()
        for root in roots:
            self.ctx.search.indexes.refresh_in_background(root, self.ctx.runner)
        self.status("Updating the search index in the background…")

    def toggle_filter(self) -> None:
        self._tab_call("toggle_filter")

    def open_location(self, path: str, new_tab: bool = False) -> None:
        tab = self.active_tab()
        if new_tab or tab is None:
            self.new_tab(path)
        else:
            tab.navigate(path)
            tab.focus_view()

    # ======================================================================
    # View settings (global for all tabs)
    # ======================================================================
    def set_view_mode(self, mode: str) -> None:
        self.ctx.settings.set("view.mode", mode)
        self.ctx.bus.publish(Topics.VIEW_MODE_CHANGED, mode=mode)
        self.ctx.actions.sync_checks()

    def set_zoom(self, level: int) -> None:
        mode = self.ctx.settings.get("view.mode", "details")
        level = max(0, min(len(view_class(mode).ZOOM_SIZES) - 1, int(level)))
        if level != self.ctx.settings.get(f"view.zoom.{mode}", 1):
            self.ctx.settings.set(f"view.zoom.{mode}", level)
            self.ctx.bus.publish(Topics.ZOOM_CHANGED, mode=mode, level=level)

    def zoom(self, delta: int) -> None:
        mode = self.ctx.settings.get("view.mode", "details")
        self.set_zoom(self.ctx.settings.get(f"view.zoom.{mode}", 1) + delta)

    def zoom_reset(self) -> None:
        mode = self.ctx.settings.get("view.mode", "details")
        self.set_zoom(DEFAULTS["view"]["zoom"].get(mode, 1))

    def toggle_drive_usage(self) -> None:
        s = self.ctx.settings
        s.set("appearance.drive_usage", not s.get("appearance.drive_usage", True))

    def toggle_hidden(self) -> None:
        value = not self.ctx.settings.get("view.show_hidden", False)
        self.ctx.settings.set("view.show_hidden", value)
        self.ctx.bus.publish(Topics.SHOW_HIDDEN_CHANGED, value=value)
        self.ctx.actions.sync_checks()

    def _publish_sort(self) -> None:
        s = self.ctx.settings
        self.ctx.bus.publish(Topics.SORT_CHANGED, key=s.get("view.sort_key"), reverse=s.get("view.sort_reverse"),
                             folders_first=s.get("view.folders_first"))
        self.ctx.actions.sync_checks()

    def set_sort(self, key: str, toggle: bool = True) -> None:
        s = self.ctx.settings
        if toggle and s.get("view.sort_key") == key:
            s.set("view.sort_reverse", not s.get("view.sort_reverse"))
        else:
            s.set("view.sort_key", key)
            if toggle:
                s.set("view.sort_reverse", key in ("size", "modified"))   # biggest/newest first feels natural
        self._publish_sort()

    def toggle_sort_reverse(self) -> None:
        self.ctx.settings.set("view.sort_reverse", not self.ctx.settings.get("view.sort_reverse"))
        self._publish_sort()

    def toggle_folders_first(self) -> None:
        self.ctx.settings.set("view.folders_first", not self.ctx.settings.get("view.folders_first"))
        self._publish_sort()

    def toggle_column(self, column: str) -> None:
        cols = list(self.ctx.settings.get("view.visible_columns", ["name", "size", "type", "modified"]))
        if column in cols:
            cols.remove(column)
        else:
            order = ["name", "size", "type", "modified", "status"]
            cols.append(column)
            cols.sort(key=lambda c: order.index(c) if c in order else 99)
        self.ctx.settings.set("view.visible_columns", cols)

    def set_theme(self, name: str) -> None:
        self.ctx.icons.clear()
        self.ctx.theme.apply(name)
        self.ctx.main.on_theme_changed()
        self.ctx.actions.sync_checks()

    # ======================================================================
    # Opening things
    # ======================================================================
    @staticmethod
    def mime_types(name: str) -> List[str]:
        """'movie.mkv' -> ['video/x-matroska', ...aliases and parent types]."""
        from PySide6.QtCore import QMimeDatabase
        mime = QMimeDatabase().mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
        return [mime.name()] + list(mime.aliases()) + list(mime.allAncestors())

    def apps_for(self, name: str):
        """Installed apps that can open this kind of file (Linux), default first."""
        if self.ctx.desktop_apps is None:
            return []
        return self.ctx.desktop_apps.apps_for(self.mime_types(name))

    def open_file(self, path: str, app=None) -> None:
        """Open a file with its default app (or ``app``, a DesktopApp).
        Network files are handed over as a real path or a stream when possible,
        so the app can start right away (see services/remote_open.py)."""
        fs = self.ctx.vfs.resolve(path)
        if fs.is_local and app is None:
            try:
                platform_ops.open_path(path)
            except OSError as exc:
                self.status(f"Cannot open: {exc}", "error")
            return
        name = fs.basename(path)
        mimes = self.mime_types(name)
        apps = self.ctx.desktop_apps

        def work():                                           # worker thread: may mount / talk to the NAS
            chosen = app or (apps.default_for(mimes) if apps is not None else None)
            accepts_urls = bool(chosen and chosen.accepts_urls)
            return chosen, self.ctx.remote_opener.resolve(path, accepts_urls)

        def launch(chosen, target: str) -> None:
            try:
                if chosen is not None:
                    platform_ops.launch(chosen.command([target]))
                else:
                    platform_ops.open_path(target)
            except OSError as exc:
                self.status(f"Cannot open {name}: {exc}", "error")

        def done(result) -> None:
            chosen, target = result
            who = chosen.name if chosen is not None else "its app"
            if target.kind == "download":
                self.status(f"Downloading {name} first…")
                self.ctx.fileops.download_to_temp(path, on_done=lambda local: launch(chosen, local))
                return
            self.status(f"{'Streaming' if target.kind == 'url' else 'Opening'} {name} with {who}")
            launch(chosen, target.value)

        self.status(f"Opening {name}…")
        self.ctx.runner.submit(work, pool="misc", on_done=done,
                               on_error=lambda exc: self.status(f"Cannot open {name}: {exc}", "error"))

    def open_with_selected(self) -> None:
        files = [e for e in self._selection() if not e.is_dir]
        if files:
            self.open_with(files[0].path)

    def open_with(self, path: str) -> None:
        """Open with a program the user types (e.g. 'mpv --fs' or 'gimp')."""
        cmd = dialogs.ask_string(self._window(), "Open with",
                                 "Program to open the file with (e.g. mpv, gimp, code):",
                                 self.ctx.settings.get("behaviour.last_open_with", ""))
        if not cmd:
            return
        self.ctx.settings.set("behaviour.last_open_with", cmd)
        if self.ctx.vfs.resolve(path).is_local and not IS_LINUX:
            try:
                platform_ops.open_with(path, cmd)          # macOS "open -a", Windows quirks
            except OSError as exc:
                self.status(f"Cannot run {cmd}: {exc}", "error")
            return
        from .services.desktop_apps import DesktopApp
        self.open_file(path, DesktopApp("custom", cmd, cmd + " %U"))   # treat it like an app that takes URLs

    def open_external_terminal(self, path: Optional[str] = None) -> None:
        tab = self.active_tab()
        path = path or (tab.current_folder if tab else None) or home()
        if not self.ctx.vfs.resolve(path).is_local:
            self.status("The terminal can only open local folders", "error")
            return
        try:
            platform_ops.open_external_terminal(path, self.ctx.settings.get("terminal.external", ""))
        except OSError as exc:
            self.status(str(exc), "error")

    # ======================================================================
    # Clipboard (shared with other file managers - see ui/system_clipboard.py)
    # ======================================================================
    def copy_text(self, text: str) -> None:
        system_clipboard.put_text(text)

    def cut(self, paths: Optional[List[str]] = None) -> None:
        paths = self._selected_paths(paths)
        if paths:
            self.ctx.clipboard.set(paths, cut=True)
            system_clipboard.put_files(paths, cut=True)
            self.status(f"Cut {len(paths)} item(s) — paste to move")

    def copy(self, paths: Optional[List[str]] = None) -> None:
        paths = self._selected_paths(paths)
        if paths:
            self.ctx.clipboard.set(paths, cut=False)
            system_clipboard.put_files(paths, cut=False)
            self.status(f"Copied {len(paths)} item(s)")

    def copy_path(self, paths: Optional[List[str]] = None) -> None:
        paths = self._selected_paths(paths)
        if not paths:
            tab = self.active_tab()
            paths = [tab.path] if tab else []
        if paths:
            system_clipboard.put_text("\n".join(paths))
            self.status("Path copied to clipboard")

    def _clipboard_files(self):
        paths, cut = system_clipboard.get_files()
        if not paths:
            paths, cut = self.ctx.clipboard.get()
        return paths, cut

    def can_paste(self) -> bool:
        return bool(self._clipboard_files()[0])

    def paste(self, dest: Optional[str] = None) -> None:
        tab = self.active_tab()
        dest = dest or (tab.current_folder if tab else None)
        if not dest:
            self.status("Can't paste here", "error")
            return
        paths, cut = self._clipboard_files()
        if not paths:
            return
        if cut:                                 # a cut can only be pasted once
            self.ctx.clipboard.clear()
            system_clipboard.put_text("")
        self.start_transfer(paths, dest, move=cut)

    # ======================================================================
    # File operations
    # ======================================================================
    def rename(self, tab=None, entry: Optional[FileEntry] = None) -> None:
        """Start an inline rename (the view's editor calls rename_to when done)."""
        tab = tab or self.active_tab()
        if tab is None or tab.view is None:
            return
        if entry is None:
            sel = tab.selected_entries()
            if len(sel) > 1:                    # several items: the Rename tab does them all at once
                self.open_rename([e.path for e in sel])
                return
            if len(sel) != 1:
                return
            entry = sel[0]
        tab.view.begin_rename(entry.path)

    def rename_to(self, tab, old: str, new_name: str) -> None:
        def done(new_path: str) -> None:
            self.ctx.folder_colors.rename(old, new_path)
            tab._select_after_load = [new_path]
            tab.on_folder_changed(tab.path)
        self.ctx.fileops.rename(old, new_name, on_done=done,
                                on_error=lambda exc: self.status(f"Rename failed: {exc}", "error"))

    def delete(self, paths: Optional[List[str]] = None, permanent: bool = False) -> None:
        paths = self._selected_paths(paths)
        if not paths:
            return
        remote = any(not self.ctx.vfs.resolve(p).is_local for p in paths)
        use_trash = self.ctx.settings.get("behaviour.use_trash", True)
        permanent = permanent or remote or not use_trash
        if permanent or self.ctx.settings.get("behaviour.confirm_delete", True):
            what = self.ctx.vfs.resolve(paths[0]).basename(paths[0]) if len(paths) == 1 else f"{len(paths)} items"
            if permanent:
                ok = dialogs.confirm(self._window(), "Delete permanently",
                                     f"Permanently delete {what}?\nThis cannot be undone.", "Delete", danger=True)
            else:
                ok = dialogs.confirm(self._window(), "Move to trash", f"Move {what} to the trash?", "Move to trash")
            if not ok:
                return
        self.ctx.fileops.delete(paths, permanent)

    def new_folder(self, tab=None) -> None:
        tab = tab or self.active_tab()
        folder = tab.current_folder if tab else None
        if not folder:
            return
        name = dialogs.ask_string(self._window(), "New folder", "Folder name:", "New folder")
        if name and name.strip():
            self.ctx.fileops.make_folder(folder, name.strip(),
                                         on_done=lambda p: setattr(tab, "_select_after_load", [p]),
                                         on_error=lambda exc: self.status(f"Cannot create folder: {exc}", "error"))

    def new_file(self, tab=None) -> None:
        tab = tab or self.active_tab()
        folder = tab.current_folder if tab else None
        if not folder:
            return
        name = dialogs.ask_string(self._window(), "New file", "File name:", "New file.txt", select_stem=True)
        if name and name.strip():
            self.ctx.fileops.make_file(folder, name.strip(),
                                       on_done=lambda p: setattr(tab, "_select_after_load", [p]),
                                       on_error=lambda exc: self.status(f"Cannot create file: {exc}", "error"))

    def select_all(self) -> None:
        tab = self.active_tab()
        if tab and tab.view:
            tab.view.select_all()

    def invert_selection(self) -> None:
        tab = self.active_tab()
        if tab and tab.view:
            tab.view.invert_selection()

    def drop_files(self, paths: List[str], dest: str, op: str = "auto") -> None:
        """Drag & drop. op: copy | move | auto (move on the same disk, copy otherwise)."""
        vfs = self.ctx.vfs
        dest_fs = vfs.resolve(dest)
        paths = [p for p in paths if p != dest]
        if not paths:
            return
        if op == "auto":
            move = all(vfs.resolve(p) is dest_fs and dest_fs.same_volume(p, dest) for p in paths)
        else:
            move = op == "move"
        if all(dest_fs.parent(p) == dest for p in paths) and move:
            return                                 # dropped where they already are
        self.start_transfer(paths, dest, move=move)

    def transfer_to_other_panel(self, paths: Optional[List[str]] = None, move: bool = False) -> None:
        other = self.area.other_panel()
        target = other.active_tab.current_folder if (other and other.active_tab) else None
        paths = self._selected_paths(paths)
        if not target:
            self.status("Open split view (F3) and a folder on the other side first", "error")
            return
        if paths:
            self.start_transfer(paths, target, move=move)

    def properties(self, entries: Optional[List[FileEntry]] = None) -> None:
        entries = entries if entries is not None else self._selection()
        if not entries:
            tab = self.active_tab()
            if tab:
                self.properties_of_path(tab.path)
            return
        dialogs.PropertiesDialog(self._window(), self.ctx, entries).show()

    def properties_of_path(self, path: str) -> None:
        if path.startswith("computer://"):
            return
        self.ctx.runner.submit(lambda: self.ctx.vfs.resolve(path).stat(path), pool="misc",
                               on_done=lambda e: self.properties([e]),
                               on_error=lambda exc: self.status(str(exc), "error"))

    def reveal_in_system(self, path: str) -> None:
        """Show the item in Explorer / Finder / the desktop's file manager
        (handy for things only they offer, like OneDrive's Share or Version history)."""
        try:
            platform_ops.reveal_in_system(path)
        except OSError as exc:
            self.status(str(exc), "error")

    # ======================================================================
    # Cloud files (OneDrive, Dropbox...): keep on device / free up space
    # ======================================================================
    def set_keep_on_device(self, entries: List[FileEntry], keep: bool) -> None:
        from PySide6.QtCore import QTimer
        from .services.cloud_providers import set_keep_on_device
        title = "Keeping on this device" if keep else "Freeing up space"
        job = self.ctx.jobs.start(f"{title}: {len(entries)} item(s)")
        folders = sorted({e.parent for e in entries if e.parent})

        def work() -> int:
            errors = []
            for i, e in enumerate(entries):
                if job.token.cancelled:
                    break
                self.ctx.jobs.update(job, i / max(1, len(entries)), e.name)
                try:
                    set_keep_on_device(e.path, keep, e.is_dir)
                except OSError as exc:
                    errors.append(f"{e.name}: {exc}")
            self.ctx.jobs.finish(job, errors[0] if errors else None)
            return len(entries) - len(errors)

        def refresh() -> None:
            self.ctx.bus.publish(Topics.FS_CHANGED, paths=folders + [e.path for e in entries if e.is_dir])

        def done(count: int) -> None:
            # The sync app downloads/frees files in the background and that does not
            # change the folder, so re-check the states a few times as it works.
            for delay in (500, 3000, 10000, 30000):
                QTimer.singleShot(delay, refresh)
        self.ctx.runner.submit(work, pool="ops", on_done=done,
                               on_error=lambda exc: self.status(f"{title} failed: {exc}", "error"))

    # ======================================================================
    # Folder colours & sidebar categories
    # ======================================================================
    def set_folder_color(self, paths: List[str], color: Optional[str]) -> None:
        for p in paths:
            self.ctx.folder_colors.set(p, color)
        for panel in self.area.panels:
            panel.refresh_tab_icons()

    def choose_folder_color(self, paths: List[str]) -> None:
        initial = QColor(self.ctx.folder_colors.get(paths[0]) or self.ctx.theme.palette["folder"])
        color = QColorDialog.getColor(initial, self._window(), "Folder colour")
        if color.isValid():
            self.set_folder_color(paths, color.name())

    def add_paths_to_category(self, paths: List[str], cat_id: str) -> None:
        for p in paths:
            fs = self.ctx.vfs.resolve(p)
            kind = "network" if self.ctx.vfs.is_remote(p) else ("computer" if p.startswith("computer://") else "folder")
            if kind == "folder" and self.ctx.cloud.root_at(p) is not None:
                kind = "cloud"                    # shows the sync app's icon
            self.ctx.categories.add_item(cat_id, p, fs.basename(p), kind)

    def add_paths_to_new_category(self, paths: List[str]) -> None:
        name = dialogs.ask_string(self._window(), "New category", "Category name:", "New category")
        if name:
            self.add_paths_to_category(paths, self.ctx.categories.add_category(name))

    # ======================================================================
    # Network
    # ======================================================================
    def new_connection(self, cat_id: Optional[str] = None, conn: Optional[dict] = None) -> None:
        data = dialogs.ConnectionDialog(self._window(), conn, self.ctx.credentials.where()).run()
        if not data:
            return
        from .services.credential_store import login_key
        password = data.get("password", "")
        saved = self.ctx.connections.save_connection(data)
        if password:
            self.ctx.connections.remember_session_password(saved["id"], password)
            if data.get("remember_password"):
                self.ctx.credentials.set(login_key(saved), saved.get("user") or "", password)
        elif not data.get("remember_password"):
            self.ctx.credentials.delete(login_key(saved))
        uri = self.ctx.connections.root_uri(saved)
        self.ctx.vfs.disconnect(self.ctx.connections.prefix_of(saved))    # pick up edited settings
        if conn is None:
            if cat_id is None:
                cats = self.ctx.categories.all()
                net = next((c for c in cats if c["name"].lower() == "network"), None)
                cat_id = net["id"] if net else self.ctx.categories.add_category("Network")
            self.ctx.categories.add_item(cat_id, uri, saved["name"], "network")
        self.open_location(uri, new_tab=True)

    def edit_connection_for(self, path: str) -> None:
        conn = self.ctx.connections.find_for_uri(path)
        if conn:
            self.new_connection(conn=conn)

    def saved_logins(self) -> None:
        dialogs.SavedLoginsDialog(self._window(), self.ctx.credentials).exec()

    def forget_login_for(self, path: str) -> None:
        from .core.connections import split_uri
        from .services.credential_store import login_key
        protocol, user, host, port, _ = split_uri(path)
        self.ctx.credentials.delete(login_key({"protocol": protocol, "host": host, "port": port}))
        self.ctx.vfs.disconnect(path)
        self.status(f"Forgot the saved login for {host}")

    def disconnect(self, path: str) -> None:
        self.ctx.vfs.disconnect(path)
        self.status("Disconnected")

    # ======================================================================
    # Search index
    # ======================================================================
    # ======================================================================
    # Menus & dialogs
    # ======================================================================
    def show_context_menu(self, tab, global_pos, entries: List[FileEntry]) -> None:
        menu = build_item_menu(self.ctx, tab, entries) if entries else build_background_menu(self.ctx, tab)
        menu.exec(global_pos)
        menu.deleteLater()

    def preferences(self) -> None:
        dialogs.PreferencesDialog(self._window(), self.ctx).exec()

    def shortcuts(self) -> None:
        dialogs.ShortcutsDialog(self._window(), self.ctx).exec()

    def about(self) -> None:
        from PySide6 import __version__ as pyside_version
        from .core.paths import config_dir
        dialogs.info(self._window(), "About",
                     f"{APP_NAME} {APP_VERSION}\n\nA modular, cross-platform file explorer in Python + Qt "
                     f"(PySide6 {pyside_version}).\n\nSettings folder:\n{config_dir()}")

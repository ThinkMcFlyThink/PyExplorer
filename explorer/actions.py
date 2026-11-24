"""
Actions - every user command has an id, a label, keyboard shortcut(s), an
optional icon and a callback. Each becomes a QAction on the main window, so
the menus, the toolbar and the keyboard all share the same object.

Shortcuts are written the human way ("Ctrl+Shift+N", "F4", "Alt+Left").
On macOS Qt turns "Ctrl" into Cmd automatically. Users can change them in
Help > Keyboard shortcuts (saved under "shortcuts" in settings.json).

Adding a feature = write a method in commands.py + one ``add(...)`` line in
``register_all`` below (+ the id in ui/menubar.py to show it in a menu).

``files_only=True`` actions (Delete, Ctrl+C...) only act while the keyboard
focus is in the file panels - so pressing Delete in the sidebar never deletes
the files selected in the view.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QStyle

ShortcutSpec = Union[str, List[str]]


@dataclass
class Action:
    id: str
    label: str
    callback: Callable[[], None]
    defaults: List[str] = field(default_factory=list)
    check: Optional[Callable[[], bool]] = None     # makes it a checkable action
    files_only: bool = False
    qaction: Optional[QAction] = None

    @property
    def shortcut(self) -> str:
        """The current main shortcut, as text."""
        seqs = self.qaction.shortcuts() if self.qaction else []
        return seqs[0].toString(QKeySequence.SequenceFormat.NativeText) if seqs else ""

    @property
    def accelerator(self) -> str:
        return self.shortcut


class ActionRegistry:
    def __init__(self, window, settings, can_use_files: Callable[[], bool]) -> None:
        self.window = window
        self.settings = settings
        self._can_use_files = can_use_files
        self._actions: Dict[str, Action] = {}

    def add(self, id: str, label: str, callback: Callable[[], None], shortcut: ShortcutSpec = "",
            icon: Optional[QIcon] = None, check: Optional[Callable[[], bool]] = None,
            files_only: bool = False, repeat: bool = True) -> Action:
        defaults = [shortcut] if isinstance(shortcut, str) and shortcut else list(shortcut or [])
        action = Action(id, label, callback, defaults, check, files_only)
        qa = QAction(label, self.window)
        if icon is not None:
            qa.setIcon(icon)
        qa.setCheckable(check is not None)
        qa.setAutoRepeat(repeat)
        qa.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        qa.triggered.connect(lambda checked=False, a=action: self._run(a))
        self.window.addAction(qa)                 # shortcuts work even when not in a menu
        action.qaction = qa
        self._actions[id] = action
        self._apply_shortcut(action)
        return action

    def _apply_shortcut(self, action: Action) -> None:
        override = (self.settings.get("shortcuts") or {}).get(action.id)
        keys = [override] if override is not None else action.defaults
        action.qaction.setShortcuts([QKeySequence(k) for k in keys if k])

    def apply_overrides(self) -> None:
        for action in self._actions.values():
            self._apply_shortcut(action)

    def default_shortcut(self, id: str) -> str:
        action = self._actions.get(id)
        return action.defaults[0] if action and action.defaults else ""

    def _run(self, action: Action) -> None:
        if action.files_only and not self._can_use_files():
            return
        action.callback()
        self.sync_checks()

    def sync_checks(self) -> None:
        """Update every checkable action (menus, toolbar) to the current state."""
        for action in self._actions.values():
            if action.check is not None:
                action.qaction.blockSignals(True)
                action.qaction.setChecked(bool(action.check()))
                action.qaction.blockSignals(False)

    def get(self, id: str) -> Optional[Action]:
        return self._actions.get(id)

    def qaction(self, id: str) -> Optional[QAction]:
        action = self._actions.get(id)
        return action.qaction if action else None

    def all(self) -> List[Action]:
        return list(self._actions.values())

    def accel(self, id: str) -> str:
        action = self._actions.get(id)
        return action.accelerator if action else ""

    def trigger(self, id: str) -> None:
        action = self._actions.get(id)
        if action:
            self._run(action)


def register_all(reg: ActionRegistry, ctx) -> None:
    """The table of all commands. ``c`` = ctx.commands."""
    c = ctx.commands
    s = ctx.settings
    i = ctx.icons.action
    SP = QStyle.StandardPixmap
    from .ui.theme import THEME_NAMES, display_name
    from .ui.views.registry import VIEW_TYPES

    # tabs / panels / window
    reg.add("tab.new", "New tab", lambda: c.new_tab(), "Ctrl+T", i("tab-new", SP.SP_FileDialogNewFolder))
    reg.add("tab.close", "Close tab", c.close_tab, "Ctrl+W", i("tab-close", SP.SP_DialogCloseButton))
    reg.add("tab.reopen", "Reopen closed tab", c.reopen_closed_tab, "Ctrl+Shift+T")
    reg.add("tab.duplicate", "Duplicate tab", c.duplicate_tab)
    reg.add("tab.next", "Next tab", lambda: c.next_tab(1), ["Ctrl+Tab", "Ctrl+PgDown"])
    reg.add("tab.prev", "Previous tab", lambda: c.next_tab(-1), ["Ctrl+Shift+Tab", "Ctrl+PgUp"])
    reg.add("tab.to_other", "Move tab to other panel", lambda: c.move_tab_to_other_panel())
    reg.add("window.split", "Split view", c.toggle_split, "F3", i("view-split-left-right"),
            check=lambda: ctx.main.panel_area.is_split, repeat=False)
    reg.add("window.terminal", "Terminal", c.toggle_terminal, "F4", i("utilities-terminal", SP.SP_ComputerIcon),
            check=lambda: ctx.main.terminal_visible, repeat=False)
    reg.add("panel.switch", "Switch panel", c.focus_other_panel, "F6")
    reg.add("window.sidebar", "Sidebar", c.toggle_sidebar, "F9", check=lambda: ctx.main.sidebar_visible, repeat=False)
    reg.add("window.toolbar", "Toolbar", c.toggle_toolbar, check=lambda: s.get("appearance.show_toolbar", True))
    reg.add("window.statusbar", "Status bar", c.toggle_statusbar, check=lambda: s.get("appearance.show_statusbar", True))
    reg.add("window.fullscreen", "Full screen", c.toggle_fullscreen, "F11", check=lambda: ctx.main.isFullScreen())
    reg.add("app.quit", "Quit", c.quit, "Ctrl+Q", i("application-exit", SP.SP_DialogCloseButton))

    # navigation
    reg.add("go.back", "Back", c.go_back, "Alt+Left", i("go-previous", SP.SP_ArrowBack))
    reg.add("go.forward", "Forward", c.go_forward, "Alt+Right", i("go-next", SP.SP_ArrowForward))
    reg.add("go.up", "Up", c.go_up, ["Alt+Up", "Backspace"], i("go-up", SP.SP_ArrowUp), files_only=True)
    reg.add("go.home", "Home folder", c.go_home, "Alt+Home", i("go-home", SP.SP_DirHomeIcon))
    reg.add("go.computer", "Computer / drives", c.go_computer, "", i("computer", SP.SP_ComputerIcon))
    reg.add("go.location", "Edit location", c.edit_location, ["Ctrl+L", "Alt+D"])
    reg.add("view.refresh", "Refresh", c.refresh, ["F5", "Ctrl+R"], i("view-refresh", SP.SP_BrowserReload))

    # files
    reg.add("file.new_folder", "New folder…", lambda: c.new_folder(), "Ctrl+Shift+N",
            i("folder-new", SP.SP_FileDialogNewFolder))
    reg.add("file.new_file", "New empty file…", lambda: c.new_file(), "", i("document-new", SP.SP_FileIcon))
    reg.add("file.properties", "Properties", lambda: c.properties(), "Alt+Return",
            i("document-properties", SP.SP_FileDialogInfoView))
    reg.add("file.open_with", "Open with…", lambda: c.open_with_selected())
    reg.add("net.new", "Connect to server…", lambda: c.new_connection(), "", i("network-server", SP.SP_DriveNetIcon))
    reg.add("edit.cut", "Cut", lambda: c.cut(), "Ctrl+X", i("edit-cut"), files_only=True)
    reg.add("edit.copy", "Copy", lambda: c.copy(), "Ctrl+C", i("edit-copy"), files_only=True)
    reg.add("edit.paste", "Paste", lambda: c.paste(), "Ctrl+V", i("edit-paste"),
            files_only=True)
    reg.add("edit.copy_path", "Copy path", lambda: c.copy_path(), "Ctrl+Shift+C", files_only=True)
    reg.add("edit.rename", "Rename", lambda: c.rename(), "F2", i("edit-rename"),
            files_only=True)
    reg.add("edit.trash", "Move to trash", lambda: c.delete(), "Delete", i("user-trash", SP.SP_TrashIcon),
            files_only=True)
    reg.add("edit.delete", "Delete permanently", lambda: c.delete(permanent=True), "Shift+Delete",
            i("edit-delete", SP.SP_TrashIcon), files_only=True)
    reg.add("edit.select_all", "Select all", c.select_all, "Ctrl+A", files_only=True)
    reg.add("edit.invert", "Invert selection", c.invert_selection, "Ctrl+Shift+I", files_only=True)
    reg.add("edit.filter", "Filter this folder", c.toggle_filter, "Ctrl+I", i("view-filter"))
    reg.add("edit.search", "Search…", lambda: c.open_search(), "Ctrl+F", ctx.icons.symbol("search", force=True))
    reg.add("tools.rename", "Bulk rename…", lambda: c.open_rename(), "Ctrl+F2", ctx.icons.symbol("rename", force=True))
    reg.add("tools.history", "History…", c.open_history, "Ctrl+H", ctx.icons.symbol("history", force=True))
    reg.add("edit.copy_other", "Copy to other panel", lambda: c.transfer_to_other_panel(move=False), "Shift+F5",
            files_only=True)
    reg.add("edit.move_other", "Move to other panel", lambda: c.transfer_to_other_panel(move=True), "Shift+F6",
            files_only=True)

    # view
    for n, (mode, (label, _cls, icon_name)) in enumerate(VIEW_TYPES.items(), start=1):
        reg.add(f"view.{mode}", f"{label} view", lambda m=mode: c.set_view_mode(m), f"Ctrl+{n}",
                ctx.icons.view_mode(mode), check=lambda m=mode: s.get("view.mode") == m)
    reg.add("view.zoom_in", "Zoom in", lambda: c.zoom(1), ["Ctrl++", "Ctrl+="], i("zoom-in"))
    reg.add("view.zoom_out", "Zoom out", lambda: c.zoom(-1), "Ctrl+-", i("zoom-out"))
    reg.add("view.zoom_reset", "Reset zoom", c.zoom_reset, "Ctrl+0", i("zoom-original", SP.SP_DialogResetButton))
    # (Ctrl+H is the History tab; hidden files moved to Ctrl+Shift+H / Alt+.)
    reg.add("view.hidden", "Show hidden files", c.toggle_hidden, ["Ctrl+Shift+H", "Alt+."],
            check=lambda: s.get("view.show_hidden"))
    reg.add("view.drive_usage", "Drive usage bars", c.toggle_drive_usage,
            check=lambda: s.get("appearance.drive_usage", True))
    reg.add("view.folders_first", "Folders first", c.toggle_folders_first, check=lambda: s.get("view.folders_first"))
    reg.add("view.sort_desc", "Descending", c.toggle_sort_reverse, check=lambda: s.get("view.sort_reverse"))
    for key, label in (("name", "Name"), ("size", "Size"), ("type", "Type"), ("modified", "Date modified")):
        reg.add(f"sort.{key}", label, lambda k=key: c.set_sort(k, toggle=False),
                check=lambda k=key: s.get("view.sort_key") == k)
    for name in THEME_NAMES:
        reg.add(f"theme.{name}", display_name(name), lambda n=name: c.set_theme(n),
                check=lambda n=name: ctx.theme.name == n)

    # tools / help
    reg.add("tools.terminal_external", "Open external terminal here", lambda: c.open_external_terminal(),
            "Ctrl+Alt+T", i("utilities-terminal"))
    reg.add("tools.update_index", "Update search index", c.update_search_index)
    reg.add("net.logins", "Saved network logins…", c.saved_logins)
    reg.add("tools.preferences", "Preferences…", c.preferences, "Ctrl+,", i("configure"))
    reg.add("help.shortcuts", "Keyboard shortcuts…", c.shortcuts, "F1", i("preferences-desktop-keyboard-shortcuts", SP.SP_DialogHelpButton))
    reg.add("help.about", "About", c.about, "", i("help-about", SP.SP_MessageBoxInformation))

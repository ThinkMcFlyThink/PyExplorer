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

Icons are PyExplorer's own line icons (ui/symbols.py), the same on every OS.
Checkable actions show a check mark in menus instead of their icon, so their
state is always visible; their toolbar buttons keep the icon. Tooltips say
the current shortcut ("Split view (F3)").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QStyle

ShortcutSpec = Union[str, List[str]]

# macOS moves these into the application menu; everything else stays where we put it.
MENU_ROLES = {"tools.preferences": QAction.MenuRole.PreferencesRole, "help.about": QAction.MenuRole.AboutRole,
              "app.quit": QAction.MenuRole.QuitRole}


def plain_label(label: str) -> str:
    """'&New folder…' -> 'New folder' (for tooltips and lists)."""
    return label.replace("&&", "\0").replace("&", "").replace("\0", "&").rstrip("…").rstrip(".").strip()


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
        if check is not None:
            qa.setIconVisibleInMenu(False)        # menus show the check mark; the toolbar keeps the icon
        qa.setMenuRole(MENU_ROLES.get(id, QAction.MenuRole.NoRole))
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
        tip = plain_label(action.label)
        if action.shortcut:
            tip += f" ({action.shortcut})"
        action.qaction.setToolTip(tip)

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
    """The table of all commands. ``c`` = ctx.commands, ``i(shape, desktop icon name)`` = a line icon
    (ui/symbols.py) - or the desktop's icon of that name if Settings -> Appearance -> Icons asks for it."""
    c = ctx.commands
    s = ctx.settings
    i = ctx.icons.symbol
    SP = QStyle.StandardPixmap
    from .ui.theme import THEME_NAMES, display_name
    from .ui.views.registry import VIEW_TYPES

    # tabs / panels / window
    reg.add("tab.new", "New tab", lambda: c.new_tab(), "Ctrl+T", i("tab-new", "tab-new"))
    reg.add("tab.close", "Close tab", c.close_tab, "Ctrl+W", i("close", "tab-close"))
    reg.add("tab.reopen", "Reopen closed tab", c.reopen_closed_tab, "Ctrl+Shift+T", i("undo", "edit-undo"))
    reg.add("tab.duplicate", "Duplicate tab", c.duplicate_tab, "", i("duplicate", "tab-duplicate"))
    reg.add("tab.next", "Next tab", lambda: c.next_tab(1), ["Ctrl+Tab", "Ctrl+PgDown"], i("chevron-right", "go-next"))
    reg.add("tab.prev", "Previous tab", lambda: c.next_tab(-1), ["Ctrl+Shift+Tab", "Ctrl+PgUp"],
            i("chevron-left", "go-previous"))
    reg.add("tab.to_other", "Move tab to other panel", lambda: c.move_tab_to_other_panel(), "", i("transfer"))
    reg.add("window.split", "Split view", c.toggle_split, "F3", i("split", "view-split-left-right"),
            check=lambda: ctx.main.panel_area.is_split, repeat=False)
    reg.add("window.terminal", "Terminal", c.toggle_terminal, "F4", i("terminal", "utilities-terminal"),
            check=lambda: ctx.main.terminal_visible, repeat=False)
    reg.add("panel.switch", "Switch panel", c.focus_other_panel, "F6", i("split"))
    reg.add("window.sidebar", "Sidebar", c.toggle_sidebar, "F9", i("sidebar", "sidebar-show"),
            check=lambda: ctx.main.sidebar_visible, repeat=False)
    reg.add("window.toolbar", "Toolbar", c.toggle_toolbar, "", i("toolbar"),
            check=lambda: s.get("appearance.show_toolbar", True))
    reg.add("window.statusbar", "Status bar", c.toggle_statusbar, "", i("statusbar"),
            check=lambda: s.get("appearance.show_statusbar", True))
    reg.add("window.fullscreen", "Full screen", c.toggle_fullscreen, "F11", i("fullscreen", "view-fullscreen"),
            check=lambda: ctx.main.isFullScreen())
    reg.add("app.quit", "Quit", c.quit, "Ctrl+Q", i("power", "application-exit", SP.SP_DialogCloseButton))

    # navigation
    reg.add("go.back", "Back", c.go_back, "Alt+Left", i("back", "go-previous", SP.SP_ArrowBack))
    reg.add("go.forward", "Forward", c.go_forward, "Alt+Right", i("forward", "go-next", SP.SP_ArrowForward))
    reg.add("go.up", "Up", c.go_up, ["Alt+Up", "Backspace"], i("up", "go-up", SP.SP_ArrowUp), files_only=True)
    reg.add("go.home", "Home folder", c.go_home, "Alt+Home", i("home", "go-home", SP.SP_DirHomeIcon))
    reg.add("go.computer", "Computer / drives", c.go_computer, "", i("computer", "computer", SP.SP_ComputerIcon))
    reg.add("go.location", "Edit location", c.edit_location, ["Ctrl+L", "Alt+D"], i("link"))
    reg.add("view.refresh", "Refresh", c.refresh, ["F5", "Ctrl+R"], i("refresh", "view-refresh", SP.SP_BrowserReload))

    # files
    reg.add("file.new_folder", "New folder…", lambda: c.new_folder(), "Ctrl+Shift+N",
            i("folder-new", "folder-new", SP.SP_FileDialogNewFolder))
    reg.add("file.new_file", "New empty file…", lambda: c.new_file(), "", i("file-new", "document-new", SP.SP_FileIcon))
    reg.add("file.properties", "Properties", lambda: c.properties(), "Alt+Return",
            i("info", "document-properties", SP.SP_FileDialogInfoView))
    reg.add("file.open_with", "Open with…", lambda: c.open_with_selected(), "", i("external", "document-open"))
    reg.add("net.new", "Connect to server…", lambda: c.new_connection(), "",
            i("server", "network-server", SP.SP_DriveNetIcon))
    reg.add("edit.cut", "Cut", lambda: c.cut(), "Ctrl+X", i("cut", "edit-cut"), files_only=True)
    reg.add("edit.copy", "Copy", lambda: c.copy(), "Ctrl+C", i("copy", "edit-copy"), files_only=True)
    reg.add("edit.paste", "Paste", lambda: c.paste(), "Ctrl+V", i("paste", "edit-paste"), files_only=True)
    reg.add("edit.copy_path", "Copy path", lambda: c.copy_path(), "Ctrl+Shift+C", i("link", "edit-copy-path"),
            files_only=True)
    reg.add("edit.rename", "Rename", lambda: c.rename(), "F2", i("rename", "edit-rename"), files_only=True)
    reg.add("edit.trash", "Move to trash", lambda: c.delete(), "Delete", i("trash", "user-trash", SP.SP_TrashIcon),
            files_only=True)
    reg.add("edit.delete", "Delete permanently", lambda: c.delete(permanent=True), "Shift+Delete",
            i("delete", "edit-delete", SP.SP_TrashIcon), files_only=True)
    reg.add("edit.select_all", "Select all", c.select_all, "Ctrl+A", i("select-all", "edit-select-all"),
            files_only=True)
    reg.add("edit.invert", "Invert selection", c.invert_selection, "Ctrl+Shift+I",
            i("invert-selection", "edit-select-invert"), files_only=True)
    reg.add("edit.filter", "Filter this folder", c.toggle_filter, "Ctrl+I", i("filter", "view-filter"))
    reg.add("edit.search", "Search…", lambda: c.open_search(), "Ctrl+F", i("search", force=True))
    reg.add("tools.rename", "Bulk rename…", lambda: c.open_rename(), "Ctrl+F2", i("rename", force=True))
    reg.add("tools.transfers", "Transfers…", c.open_transfers, "Ctrl+J", i("transfer", force=True))
    reg.add("tools.history", "History…", c.open_history, "Ctrl+H", i("history", force=True))
    reg.add("edit.copy_other", "Copy to other panel", lambda: c.transfer_to_other_panel(move=False), "Shift+F5",
            i("copy"), files_only=True)
    reg.add("edit.move_other", "Move to other panel", lambda: c.transfer_to_other_panel(move=True), "Shift+F6",
            i("transfer"), files_only=True)

    # view
    for n, (mode, (label, _cls, icon_name)) in enumerate(VIEW_TYPES.items(), start=1):
        reg.add(f"view.{mode}", f"{label} view", lambda m=mode: c.set_view_mode(m), f"Ctrl+{n}",
                ctx.icons.view_mode(mode), check=lambda m=mode: s.get("view.mode") == m)
    reg.add("view.zoom_in", "Zoom in", lambda: c.zoom(1), ["Ctrl++", "Ctrl+="], i("zoom-in", "zoom-in"))
    reg.add("view.zoom_out", "Zoom out", lambda: c.zoom(-1), "Ctrl+-", i("zoom-out", "zoom-out"))
    reg.add("view.zoom_reset", "Reset zoom", c.zoom_reset, "Ctrl+0", i("zoom-reset", "zoom-original"))
    # (Ctrl+H is the History tab; hidden files moved to Ctrl+Shift+H / Alt+.)
    reg.add("view.hidden", "Show hidden files", c.toggle_hidden, ["Ctrl+Shift+H", "Alt+."], i("eye", "view-hidden"),
            check=lambda: s.get("view.show_hidden"))
    reg.add("view.drive_usage", "Drive usage bars", c.toggle_drive_usage, "", i("drive"),
            check=lambda: s.get("appearance.drive_usage", True))
    reg.add("view.folders_first", "Folders first", c.toggle_folders_first, "", i("folder"),
            check=lambda: s.get("view.folders_first"))
    reg.add("view.sort_desc", "Descending", c.toggle_sort_reverse, "", i("sort"), check=lambda: s.get("view.sort_reverse"))
    for key, label in (("name", "Name"), ("size", "Size"), ("type", "Type"), ("modified", "Date modified")):
        reg.add(f"sort.{key}", label, lambda k=key: c.set_sort(k, toggle=False),
                check=lambda k=key: s.get("view.sort_key") == k)
    for name in THEME_NAMES:
        reg.add(f"theme.{name}", display_name(name), lambda n=name: c.set_theme(n),
                check=lambda n=name: ctx.theme.name == n)

    # tools / help
    reg.add("tools.terminal_external", "Open external terminal here", lambda: c.open_external_terminal(),
            "Ctrl+Alt+T", i("external", "utilities-terminal"))
    reg.add("tools.update_index", "Update search index", c.update_search_index, "", i("refresh"))
    reg.add("net.logins", "Saved network logins…", c.saved_logins, "", i("key", "dialog-password"))
    reg.add("tools.preferences", "Settings…", lambda: c.open_settings(), "Ctrl+,",
            i("settings", "configure", SP.SP_FileDialogDetailedView))
    reg.add("toolbar.customize", "Customize toolbar…", lambda: c.open_settings("toolbar"), "", i("toolbar"))
    reg.add("help.shortcuts", "Keyboard shortcuts…", lambda: c.open_settings("keyboard"), "F1",
            i("keyboard", "preferences-desktop-keyboard-shortcuts"))
    reg.add("help.about", "About PyExplorer", c.about, "", i("info", "help-about", SP.SP_MessageBoxInformation))

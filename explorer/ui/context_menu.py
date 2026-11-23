"""
The right-click menu for files/folders and for the empty background.

It only BUILDS the menu; every entry calls a method on ``ctx.commands`` or
re-uses a QAction from the action registry (so shortcuts show up too).
To add an entry: add one line here (+ one method in commands.py if new).
"""
from __future__ import annotations

from typing import List

from PySide6.QtWidgets import QMenu

from ..core.fileinfo import FileEntry
from ..core.folder_colors import PALETTE
from .views.registry import VIEW_TYPES

SORT_LABELS = [("name", "Name"), ("size", "Size"), ("type", "Type"), ("modified", "Date modified")]


def _add(menu: QMenu, label: str, callback, icon=None, shortcut_of: str = "", ctx=None, enabled: bool = True):
    act = menu.addAction(label) if icon is None else menu.addAction(icon, label)
    act.triggered.connect(lambda _=False: callback())
    act.setEnabled(enabled)
    if shortcut_of and ctx is not None:
        qa = ctx.actions.qaction(shortcut_of)
        if qa is not None and qa.shortcuts():
            act.setShortcut(qa.shortcuts()[0])          # display only; the real shortcut lives on the window
            act.setShortcutVisibleInContextMenu(True)
    return act


def _color_menu(parent: QMenu, ctx, paths: List[str]) -> QMenu:
    menu = parent.addMenu(ctx.icons.folder("#e5534b"), "Folder colour")
    for label, color in PALETTE:
        _add(menu, label, lambda c=color: ctx.commands.set_folder_color(paths, c), ctx.icons.folder(color))
    menu.addSeparator()
    _add(menu, "Custom colour…", lambda: ctx.commands.choose_folder_color(paths))
    _add(menu, "Default colour", lambda: ctx.commands.set_folder_color(paths, None), ctx.icons.folder())
    return menu


def _category_menu(parent: QMenu, ctx, paths: List[str], label: str = "Add to sidebar") -> QMenu:
    menu = parent.addMenu(label)
    for cat in ctx.categories.all():
        _add(menu, cat["name"], lambda c=cat["id"]: ctx.commands.add_paths_to_category(paths, c))
    menu.addSeparator()
    _add(menu, "New category…", lambda: ctx.commands.add_paths_to_new_category(paths))
    return menu


def _app_icon(name: str):
    from PySide6.QtGui import QIcon
    if not name:
        return QIcon()
    return QIcon(name) if name.startswith("/") else QIcon.fromTheme(name)


def _open_with_menu(menu: QMenu, ctx, entry: FileEntry) -> None:
    """Linux: "Open With ▸" listing the installed apps for this file type (like Nemo).
    Elsewhere: a single "Open with…" that asks for a program."""
    apps = ctx.commands.apps_for(entry.name)
    if not apps:
        _add(menu, "Open with…", lambda: ctx.commands.open_with(entry.path))
        return
    sub = menu.addMenu("Open With")
    for i, app in enumerate(apps):
        label = app.name + ("  (default)" if i == 0 and ctx.desktop_apps.default_for(
            ctx.commands.mime_types(entry.name)) is app else "")
        _add(sub, label, lambda a=app: ctx.commands.open_file(entry.path, a), _app_icon(app.icon))
    sub.addSeparator()
    _add(sub, "Other application…", lambda: ctx.commands.open_with(entry.path))


def _cloud_items(menu: QMenu, ctx, entries: List[FileEntry]) -> None:
    """ "Always keep on this device" / "Free up space" for files in a cloud folder."""
    roots = [ctx.cloud.find(e.path) for e in entries]
    if not roots or not all(r is not None and r.can_pin for r in roots):
        return
    menu.addSeparator()
    badge = ctx.icons.cloud_app(roots[0])
    _add(menu, "Always keep on this device", lambda: ctx.commands.set_keep_on_device(entries, True), badge)
    _add(menu, "Free up space", lambda: ctx.commands.set_keep_on_device(entries, False))


def build_item_menu(ctx, tab, entries: List[FileEntry]) -> QMenu:
    cmds = ctx.commands
    a = ctx.actions.qaction
    menu = QMenu(tab)
    dirs = [e for e in entries if e.is_dir]
    single = entries[0] if len(entries) == 1 else None
    paths = [e.path for e in entries]
    local = all(ctx.vfs.resolve(p).is_local for p in paths)
    split = ctx.main.panel_area.is_split

    _add(menu, "Open", lambda: tab.on_view_activate(entries, False))
    if dirs:
        _add(menu, "Open in new tab", lambda: tab.on_view_activate(dirs, True), a("tab.new").icon())
        if split:
            _add(menu, "Open in other panel", lambda: cmds.open_in_other_panel(dirs[0].path))
    if single and not single.is_dir:
        _open_with_menu(menu, ctx, single)
    if tab.search_mode and single:
        _add(menu, "Open containing folder", lambda: tab.navigate(single.parent, select=[single.path]),
             ctx.icons.folder())
    if single and single.is_dir and local:
        _add(menu, "Open terminal here", lambda: cmds.open_external_terminal(single.path),
             a("tools.terminal_external").icon())
    menu.addSeparator()
    _add(menu, "Cut", lambda: cmds.cut(paths), a("edit.cut").icon(), "edit.cut", ctx)
    _add(menu, "Copy", lambda: cmds.copy(paths), a("edit.copy").icon(), "edit.copy", ctx)
    if single and single.is_dir:
        _add(menu, "Paste into folder", lambda: cmds.paste(single.path), a("edit.paste").icon(),
             enabled=cmds.can_paste())
    _add(menu, "Copy path", lambda: cmds.copy_path(paths), None, "edit.copy_path", ctx)
    if split:
        _add(menu, "Copy to other panel", lambda: cmds.transfer_to_other_panel(paths, move=False), None,
             "edit.copy_other", ctx)
        _add(menu, "Move to other panel", lambda: cmds.transfer_to_other_panel(paths, move=True), None,
             "edit.move_other", ctx)
    menu.addSeparator()
    if single:
        _add(menu, "Rename", lambda: cmds.rename(tab, single), a("edit.rename").icon(), "edit.rename", ctx)
    else:
        _add(menu, f"Bulk rename {len(entries)} items…", lambda: cmds.open_rename(paths), a("tools.rename").icon(),
             "tools.rename", ctx)
    if local:
        _add(menu, "Move to trash", lambda: cmds.delete(paths), a("edit.trash").icon(), "edit.trash", ctx)
    _add(menu, "Delete permanently", lambda: cmds.delete(paths, permanent=True), a("edit.delete").icon(),
         "edit.delete", ctx)
    _cloud_items(menu, ctx, entries)
    if local and single:
        _add(menu, "Show in system file manager", lambda: cmds.reveal_in_system(single.path))
    if dirs:
        menu.addSeparator()
        dir_paths = [d.path for d in dirs]
        _color_menu(menu, ctx, dir_paths)
        _category_menu(menu, ctx, dir_paths)
        _add(menu, "Search in " + ("this folder…" if single else "these folders…"),
             lambda: cmds.open_search(dir_paths), a("edit.search").icon())
    menu.addSeparator()
    _add(menu, "Properties", lambda: cmds.properties(entries), a("file.properties").icon(), "file.properties", ctx)
    return menu


def build_background_menu(ctx, tab) -> QMenu:
    cmds = ctx.commands
    a = ctx.actions.qaction
    menu = QMenu(tab)
    folder = tab.current_folder
    if folder:
        new = menu.addMenu(a("file.new_folder").icon(), "Create new")
        _add(new, "Folder…", lambda: cmds.new_folder(tab), a("file.new_folder").icon(), "file.new_folder", ctx)
        _add(new, "Empty file…", lambda: cmds.new_file(tab), a("file.new_file").icon())
        _add(menu, "Paste", lambda: cmds.paste(folder), a("edit.paste").icon(), "edit.paste", ctx,
             enabled=cmds.can_paste())
        menu.addSeparator()
    _add(menu, "Select all", lambda: tab.view.select_all(), None, "edit.select_all", ctx)
    _add(menu, "Invert selection", lambda: tab.view.invert_selection(), None, "edit.invert", ctx)
    menu.addSeparator()
    view = menu.addMenu("View")
    for mode in VIEW_TYPES:
        view.addAction(a(f"view.{mode}"))
    sort = menu.addMenu("Sort by")
    for key, _label in SORT_LABELS:
        sort.addAction(a(f"sort.{key}"))
    sort.addSeparator()
    sort.addAction(a("view.sort_desc"))
    sort.addAction(a("view.folders_first"))
    menu.addAction(a("view.hidden"))
    menu.addAction(a("view.refresh"))
    if folder:
        menu.addSeparator()
        if ctx.vfs.resolve(folder).is_local:
            _add(menu, "Open terminal here", lambda: cmds.open_external_terminal(folder),
                 a("tools.terminal_external").icon())
        _category_menu(menu, ctx, [folder], "Add this folder to sidebar")
        _add(menu, "Search in this folder…", lambda: cmds.open_search([folder]), a("edit.search").icon(),
             "edit.search", ctx)
        _add(menu, "Bulk rename in this folder…", lambda: cmds.open_rename([]), a("tools.rename").icon())
        _add(menu, "Properties", lambda: cmds.properties_of_path(folder), a("file.properties").icon())
    ctx.actions.sync_checks()
    return menu

"""
The window's menu bar, described as data. Items are action ids (see
actions.py), "-" for a separator, (label, [items]) for a sub-menu, or a
"prefix.*" pattern meaning "every action whose id starts with prefix.", or
"@themes": System, then the themes grouped into Dark / Light sub-menus.

Check marks are refreshed every time a menu opens.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu, QMenuBar

MENUS = [
    ("&File", ["tab.new", "tab.duplicate", "tab.reopen", "tab.close", "-", "file.new_folder", "file.new_file",
               "-", "net.new", "-", "file.properties", "-", "app.quit"]),
    ("&Edit", ["edit.cut", "edit.copy", "edit.paste", "edit.copy_path", "-", "edit.rename", "edit.trash",
               "edit.delete", "-", "edit.select_all", "edit.invert", "-", "edit.filter", "edit.search", "-",
               "edit.copy_other", "edit.move_other"]),
    ("&View", ["view.icons", "view.list", "view.details", "view.tree", "-", "view.zoom_in", "view.zoom_out",
               "view.zoom_reset", "-", "view.hidden",
               ("Sort by", ["sort.name", "sort.size", "sort.type", "sort.modified", "-", "view.sort_desc",
                            "view.folders_first"]),
               ("Theme", "@themes"),
               "-", "window.sidebar", "view.drive_usage", "window.terminal", "window.split", "window.toolbar", "window.statusbar",
               "window.fullscreen", "-", "view.refresh"]),
    ("&Go", ["go.back", "go.forward", "go.up", "go.home", "go.computer", "go.location", "-",
             "tab.next", "tab.prev", "tab.to_other", "panel.switch"]),
    ("&Tools", ["edit.search", "tools.rename", "tools.transfers", "tools.history", "tools.update_index", "-", "tools.terminal_external", "-",
                "net.new", "net.logins", "-", "tools.preferences"]),
    ("&Help", ["help.shortcuts", "help.about"]),
]


def _theme_items():
    from .theme import grouped
    items = ["theme.system", "-"]
    for group, keys in grouped():
        items.append((f"{group} themes", [f"theme.{k}" for k in keys]))
    return items


def _fill(menu: QMenu, items, registry) -> None:
    if items == "@themes":
        items = _theme_items()
    if isinstance(items, str) and items.endswith(".*"):
        prefix = items[:-1]
        items = [a.id for a in registry.all() if a.id.startswith(prefix)]
    for item in items:
        if item == "-":
            menu.addSeparator()
        elif isinstance(item, tuple):
            label, sub_items = item
            _fill(menu.addMenu(label), sub_items, registry)
        else:
            qa = registry.qaction(item)
            if qa is not None:
                menu.addAction(qa)


def build_menubar(window, ctx) -> QMenuBar:
    bar = window.menuBar()
    bar.clear()
    for title, items in MENUS:
        menu = bar.addMenu(title)
        _fill(menu, items, ctx.actions)
        menu.aboutToShow.connect(ctx.actions.sync_checks)
    return bar

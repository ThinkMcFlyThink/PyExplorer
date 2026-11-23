"""
The list of view modes. Add a new one here and it automatically appears in
the toolbar, the View menu, the context menu and gets a Ctrl+<number> key.
"""
from __future__ import annotations

from .details import DetailsView, TreeView
from .grids import IconView, ListView

# mode id -> (label, class, toolbar icon theme name). Order = order in menus/toolbar.
VIEW_TYPES = {
    "icons": ("Icons", IconView, "view-list-icons"),
    "list": ("List", ListView, "view-list-text"),
    "details": ("Details", DetailsView, "view-list-details"),
    "tree": ("Tree", TreeView, "view-list-tree"),
}


def view_class(mode: str):
    return VIEW_TYPES.get(mode, VIEW_TYPES["details"])[1]

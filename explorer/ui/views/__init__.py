"""
File views: Details, Tree, Icons and List.

They are thin wrappers around Qt's own QTreeView / QListView, which already
draw only the visible rows (fast for 100,000+ items) and handle selection,
rubber-band, keyboard navigation and inline editing.

``base.ViewBehaviour`` adds what is common to all of ours: zoom, opening
items, context menus, drag & drop, type-to-filter, and keeping selection +
scroll position when the list refreshes.

To add a view mode: subclass one of the views (or write a new one using
ViewBehaviour) and add it to ``VIEW_TYPES`` in registry.py.
"""

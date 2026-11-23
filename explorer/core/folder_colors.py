"""
FolderColors - remembers a custom colour for any folder (local or remote).

Stored as {"/path/to/folder": "#e57373", ...} in folder_colors.json.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from .events import EventBus, Topics
from .storage import JsonStore

# The quick-pick palette shown in the context menu: (label, colour)
PALETTE = [
    ("Red", "#e5534b"), ("Orange", "#f0883e"), ("Yellow", "#e3b341"), ("Green", "#57ab5a"),
    ("Teal", "#39c5bb"), ("Blue", "#539bf5"), ("Purple", "#986ee2"), ("Pink", "#e275ad"),
    ("Brown", "#a1785c"), ("Grey", "#8b949e"),
]


def _key(path: str) -> str:
    if "://" in path:
        return path.rstrip("/") or path
    return os.path.normcase(os.path.normpath(path))


class FolderColors:
    def __init__(self, store: JsonStore, bus: EventBus) -> None:
        self._store = store
        self._bus = bus
        self._colors: Dict[str, str] = dict(store.load(default={}) or {})

    def get(self, path: str) -> Optional[str]:
        if not self._colors:        # fast path: most people colour few folders
            return None
        return self._colors.get(_key(path))

    def set(self, path: str, color: Optional[str]) -> None:
        key = _key(path)
        if color:
            self._colors[key] = color
        else:
            self._colors.pop(key, None)
        self._store.save(self._colors)
        self._bus.publish(Topics.FOLDER_COLORS_CHANGED, path=path)

    def rename(self, old: str, new: str) -> None:
        """Keep the colour when a folder is renamed/moved inside the app."""
        color = self._colors.pop(_key(old), None)
        if color:
            self._colors[_key(new)] = color
            self._store.save(self._colors)

"""
FileClipboard - remembers which files were Cut or Copied INSIDE the app.

The real file list also goes on the system clipboard (ui/system_clipboard.py)
so other file managers can paste it. This copy lets views draw "cut" items
dimmed without asking the system clipboard on every repaint.
"""
from __future__ import annotations

from typing import List, Tuple

from .events import EventBus, Topics


class FileClipboard:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._paths: List[str] = []
        self._cut = False
        self._cut_set: frozenset = frozenset()

    def set(self, paths: List[str], cut: bool) -> None:
        self._paths = list(paths)
        self._cut = cut
        self._cut_set = frozenset(paths) if cut else frozenset()
        self._bus.publish(Topics.CLIPBOARD_CHANGED)

    def get(self) -> Tuple[List[str], bool]:
        return list(self._paths), self._cut

    def clear(self) -> None:
        self.set([], False)

    @property
    def has_content(self) -> bool:
        return bool(self._paths)

    def is_cut(self, path: str) -> bool:
        """Views draw cut items dimmed."""
        return path in self._cut_set

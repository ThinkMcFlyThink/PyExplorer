"""Back/forward navigation history for one tab (like a web browser)."""
from __future__ import annotations

from typing import List, Optional

MAX_HISTORY = 60


class History:
    def __init__(self) -> None:
        self.back_stack: List[str] = []
        self.forward_stack: List[str] = []
        self.current: Optional[str] = None

    def visit(self, path: str) -> None:
        """Record a normal navigation. Clears the 'forward' list, like browsers do."""
        if path == self.current:
            return
        if self.current is not None:
            self.back_stack.append(self.current)
            del self.back_stack[:-MAX_HISTORY]
        self.current = path
        self.forward_stack.clear()

    def can_back(self) -> bool:
        return bool(self.back_stack)

    def can_forward(self) -> bool:
        return bool(self.forward_stack)

    def back(self) -> Optional[str]:
        if not self.back_stack:
            return None
        if self.current is not None:
            self.forward_stack.append(self.current)
        self.current = self.back_stack.pop()
        return self.current

    def forward(self) -> Optional[str]:
        if not self.forward_stack:
            return None
        if self.current is not None:
            self.back_stack.append(self.current)
        self.current = self.forward_stack.pop()
        return self.current

    def to_dict(self) -> dict:
        return {"back": self.back_stack[-MAX_HISTORY:], "forward": self.forward_stack[-MAX_HISTORY:],
                "current": self.current}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "History":
        h = cls()
        if data:
            h.back_stack = list(data.get("back") or [])
            h.forward_stack = list(data.get("forward") or [])
            h.current = data.get("current")
        return h

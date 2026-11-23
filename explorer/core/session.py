"""
Session - the open panels and tabs, so closing and reopening the app puts
you exactly where you were.

Shape of session.json::

    {
      "split": true,                # commander-style two panels?
      "active_panel": 0,
      "split_ratio": 0.5,
      "panels": [
        {"active": 1, "tabs": [<tab state>, <tab state>]},
        {"active": 0, "tabs": [<tab state>]}
      ],
      "closed_tabs": [<tab state>, ...]   # for "Reopen closed tab"
    }

A <tab state> is produced by ``BrowserTab.get_state()`` - path, history,
selection, scroll position, search, filter and the view's own state (for
example which folders are expanded in Tree view). The session code never
looks inside it, so tabs can add new fields without touching this file.
"""
from __future__ import annotations

from typing import Optional

from .storage import JsonStore


class Session:
    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def load(self) -> Optional[dict]:
        data = self._store.load(default=None)
        if not isinstance(data, dict) or not data.get("panels"):
            return None
        return data

    def save(self, data: dict) -> None:
        self._store.save(data)

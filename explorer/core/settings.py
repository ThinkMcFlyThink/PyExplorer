"""
Settings - every user preference in one dictionary, saved to settings.json.

Keys use dots to reach into nested dictionaries, e.g.::

    settings.get("view.mode")            -> "details"
    settings.set("view.show_hidden", True)

``DEFAULTS`` below is the complete list of settings. To add a new setting
just add it here - old settings files automatically pick up the default
because saved values are merged *on top of* the defaults.

Every ``set`` publishes ``Topics.SETTINGS_CHANGED`` so other parts of the
app can react without this class knowing about them.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from .events import EventBus, Topics
from .storage import JsonStore

DEFAULTS: Dict[str, Any] = {
    "appearance": {
        "theme": "dark",          # see ui/theme.py for the list ("system" = your OS look)
        "font_family": "",        # "" = the system's interface font; else a font name, e.g. "Ubuntu"
        "terminal_font": "",      # "" = the system's monospace font; else a monospace font name
        "font_size": 0,           # 0 = use the system font size
        "system_icons": True,     # file icons from your OS / icon theme (folders are always ours)
        "show_sidebar": True,
        "show_statusbar": True,
        "show_toolbar": True,
        "drive_usage": True,      # bars showing how full each drive is (sidebar + Computer view)
    },
    "view": {
        "mode": "details",        # icons | list | details | tree  (global for all tabs)
        # Zoom level per view mode - an index into each view's list of sizes.
        "zoom": {"icons": 2, "list": 1, "details": 1, "tree": 1},
        "show_hidden": False,
        "folders_first": True,
        "sort_key": "name",       # name | size | type | modified
        "sort_reverse": False,
        # Column widths (pixels) for details/tree views.
        "columns": {"name": 360, "size": 90, "type": 140, "modified": 150, "status": 170, "location": 320,
                    "match": 380},
        "visible_columns": ["name", "size", "type", "modified"],
        "thumbnails": True,
        "list_cell_width": 240,
    },
    "behaviour": {
        "single_click_open": False,
        "confirm_delete": True,
        "use_trash": True,
        "watch_folders": True,
        "watch_interval_ms": 1500,
        "type_to_filter": True,   # typing in a view opens the instant filter
        "last_open_with": "",
    },
    "terminal": {
        "visible": False,
        "shell": "",              # empty = automatic ($SHELL / cmd.exe)
        "external": "",           # external terminal command; empty = automatic
        "sync_cd": True,          # cd the terminal when you navigate
        "scrollback": 5000,
    },
    "network": {
        "open_files": "stream",   # stream: hand apps a path/stream | download: copy to temp first
        "mount_shares": True,     # mount SMB shares like Nemo does, so every app can open them
    },
    "search": {
        # The Search tab's index (services/index_manager.py). Saved in ~/.cache/pyexplorer/index.
        "refresh_minutes": 30,    # an older index still answers instantly, and is rebuilt in the background
        "index_network": False,   # include network / cloud drives in "whole computer"
        "skip_folders": [],       # never index these folders (full paths)
        "max_index_entries": 8_000_000,   # memory cap (about 75 bytes per entry)
        "workers": 0,             # parallel readers for NETWORK folders (0 = automatic)
        "live": True,             # search as you type
    },
    "transfers": {
        # PyExplorer's own copy/move (services/transfer_engine.py + the transfer window).
        "engine": "pyexplorer",   # pyexplorer | simple (the older copier without verify / window)
        "verify": True,           # checksum every file while copying, then read the copy back and compare
        "algorithm": "blake2b",   # blake2b | xxh3 | md5 | sha1 | sha256
        "verify_from_disk": True, # flush + drop the OS cache before reading back (local drives)
        "show_in": "window",      # window | tab
        "close_when_done": False, # hide the window when everything finished fine
        "keep_log": True,         # keep the list of transfers between sessions (transfers.json)
        "show_files": True,       # the per-file list under the progress
        "window_geometry": "",
    },
    "history": {
        # The History tab (Ctrl+H): renames, moves, copies, deletes... Saved in history.json
        # within a size budget (see core/file_history.py); False = forget it all on close.
        "keep": True,
    },
    "window": {
        # Qt's own saved window geometry and dock/toolbar layout (base64 text).
        "geometry": "",
        "state": "",
        "split_sizes": [],
    },
    # Keyboard shortcut overrides: {"action.id": "Ctrl+Shift+X"}. See actions.py.
    "shortcuts": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Return ``base`` with ``override`` merged in (recursively for dicts)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Settings:
    def __init__(self, store: JsonStore, bus: Optional[EventBus] = None) -> None:
        self._store = store
        self._bus = bus
        self._data = _deep_merge(DEFAULTS, store.load(default={}) or {})
        self.dirty = False   # True when there are unsaved changes

    # -- reading -----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- writing -----------------------------------------------------------
    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if node.get(parts[-1]) == value:
            return                       # nothing changed - don't spam events
        node[parts[-1]] = copy.deepcopy(value)
        self.dirty = True
        if self._bus:
            self._bus.publish(Topics.SETTINGS_CHANGED, key=key, value=value)

    def save(self) -> None:
        self._store.save(self._data)
        self.dirty = False

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

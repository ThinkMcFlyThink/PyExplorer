"""
Settings - every user preference in one dictionary, saved to settings.json.

Keys use dots to reach into nested dictionaries, e.g.::

    settings.get("view.mode")            -> "details"
    settings.set("view.show_hidden", True)

``DEFAULTS`` below is the complete list of settings. To add a new setting
just add it here - old settings files automatically pick up the default
because saved values are merged *on top of* the defaults. To show it in the
Settings tab, add one line to ``core/settings_schema.py``.

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
        "icon_style": "line",     # toolbar/menu icons: line (PyExplorer's own, every OS) | system (icon theme)
        "show_sidebar": True,
        "show_statusbar": True,
        "show_toolbar": True,
        "drive_usage": True,      # bars showing how full each drive is (sidebar + Computer view)
    },
    "toolbar": {
        # The main toolbar (ui/toolbar.py). items: [] = the default layout.
        "items": [],
        "style": "auto",          # auto | icons | text_beside | text_under
        "icon_size": 18,          # 16 | 18 | 22
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
        "alternate_rows": True,   # striped rows in Details / Tree view
        "date_format": "relative",  # relative ("Today 14:03") or a strftime pattern, e.g. "%d/%m/%Y %H:%M"
        "size_units": "binary",   # binary (1 KB = 1024 B) | decimal (1 kB = 1000 B) | iec (1 KiB = 1024 B)
    },
    "behaviour": {
        "single_click_open": False,
        "confirm_delete": True,
        "use_trash": True,
        "watch_folders": True,
        "watch_interval_ms": 1500,
        "type_to_filter": True,   # typing in a view opens the instant filter
        "new_tab_position": "next",   # next (to the current tab) | end
        "new_tab_folder": "current",  # a new tab opens: current (folder) | home
        "restore_session": True,  # reopen last time's tabs at start-up (False: start in the home folder)
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
        "max_results": 250_000,   # a search stops listing after this many results
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

    def default(self, key: str, fallback: Any = None) -> Any:
        """The built-in default of ``key`` (``fallback`` if it has none)."""
        node: Any = DEFAULTS
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return fallback
            node = node[part]
        return copy.deepcopy(node)

    def reset_key(self, key: str) -> None:
        self.set(key, self.default(key))

    # -- the whole file (Settings -> Advanced) -------------------------------------------
    def replace(self, data: dict, keep: tuple = ()) -> None:
        """Swap in a whole new set of values (merged onto the defaults); keys under
        ``keep`` stay as they are. Announces every key that changed."""
        old = self._data
        new = _deep_merge(DEFAULTS, data or {})
        for section in keep:
            if section in old:
                new[section] = copy.deepcopy(old[section])
        self._data = new
        self.dirty = True
        if self._bus:
            for key in sorted(_changed_keys(old, new)):
                self._bus.publish(Topics.SETTINGS_CHANGED, key=key, value=self.get(key))

    def reset(self) -> None:
        """Everything back to the defaults (keyboard shortcuts too), except the window layout."""
        self.replace({}, keep=("window",))

    def export_to(self, path: str) -> None:
        data = self.as_dict()
        data.pop("window", None)                    # screen-specific: not worth carrying over
        JsonStore(path).save(data)

    def import_from(self, path: str) -> None:
        data = JsonStore(path).load(default=None)
        if not isinstance(data, dict):
            raise ValueError("That file doesn't contain PyExplorer settings.")
        self.replace(data, keep=("window",))


def _changed_keys(old: dict, new: dict, prefix: str = "") -> set:
    """Dotted keys whose value differs between two nested dicts."""
    out = set()
    for key in set(old) | set(new):
        a, b = old.get(key), new.get(key)
        dotted = prefix + key
        if isinstance(a, dict) and isinstance(b, dict) and key != "shortcuts":
            out |= _changed_keys(a, b, dotted + ".")
        elif a != b:
            out.add(dotted)
    return out

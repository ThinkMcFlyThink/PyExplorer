"""
Categories - the user-defined groups shown in the left sidebar.

Unlike most file managers, NOTHING here is fixed. The user creates
categories ("Work", "Media", "Servers"...), names them, and drops any
folder, drive or network location into any of them.

Data shape (categories.json)::

    {"version": 2, "categories": [
      {"id": "c1", "name": "Places", "collapsed": false,
       "items": [{"id": "i1", "label": "Home", "path": "/home/me", "kind": "folder"}]},
      {"id": "c2", "name": "Drives", "auto": "drives", "hidden": ["/mnt/backup"],
       "items": [{"id": "i2", "label": "Computer", "path": "computer://", "kind": "computer"}]}
    ]}

``kind`` is only a hint for the icon: folder | drive | network | computer | cloud.

Live sections: a category with ``"auto": "drives" | "network" | "cloud"``
ALSO shows whatever is plugged in / mounted / synced right now (see
services/devices.py), after its own items. Those live entries are never
saved - they come and go with the hardware. The user can still rename, move
or delete such a category, add their own items to it, and hide single live
entries (their paths go into ``"hidden"``).

Version 1 files (a plain list, from before live sections existed) had a
snapshot of the drives copied in on first run. ``migrate_v1`` turns those
categories into live ones once, keeping everything the user added.
"""
from __future__ import annotations

import copy
import uuid
from typing import Callable, Dict, List, Optional

from .events import EventBus, Topics
from .storage import JsonStore


VERSION = 2
AUTO_SECTIONS = {"drives": "Drives", "network": "Network", "cloud": "Cloud"}   # auto value -> default name
SNAPSHOT_KINDS = {"drive", "removable", "network", "cloud"}   # what v1 copied in on first run


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _is_remote(path: str) -> bool:
    return "://" in path and not path.startswith("computer://")


def migrate_v1(cats: List[dict]) -> List[dict]:
    """Turn the first-run snapshot categories of version 1 into live sections.

    * the category holding "Computer" (or called Drives) becomes the live Drives
      section; the drive/mount entries copied in at first run are dropped
      (they now appear live), everything else the user put there stays;
    * "Network" becomes the live Network section (saved connections stay);
    * "Cloud" becomes the live Cloud section, or one is added after Places.
    """
    by_name = {c.get("name", "").casefold(): c for c in cats}
    drives = next((c for c in cats if any(i.get("path") == "computer://" for i in c.get("items", []))),
                  by_name.get("drives"))
    if drives is not None:
        drives["auto"] = "drives"
        drives["items"] = [i for i in drives["items"]
                           if i.get("kind") not in SNAPSHOT_KINDS or _is_remote(i.get("path", ""))]
    else:
        cats.append({"name": "Drives", "auto": "drives", "items": []})
    network = by_name.get("network")
    if network is not None and not network.get("auto"):
        network["auto"] = "network"
        network["items"] = [i for i in network["items"]
                            if i.get("kind") != "network" or _is_remote(i.get("path", ""))]
    elif network is None:
        cats.append({"name": "Network", "auto": "network", "items": []})
    cloud = by_name.get("cloud")
    if cloud is not None and not cloud.get("auto"):
        cloud["auto"] = "cloud"
        cloud["items"] = [i for i in cloud["items"] if i.get("kind") != "cloud"]
    elif cloud is None:
        cats.insert(min(1, len(cats)), {"name": "Cloud", "auto": "cloud", "items": []})
    return cats


class Categories:
    def __init__(self, store: JsonStore, bus: EventBus,
                 default_factory: Optional[Callable[[], List[dict]]] = None) -> None:
        self._store = store
        self._bus = bus
        data = store.load(default=None)
        if data is None:                         # first run: create sensible defaults
            cats = default_factory() if default_factory else []
        elif isinstance(data, list):             # version 1: plain list with a drive snapshot
            cats = migrate_v1(data)
        else:
            cats = data.get("categories") or []
        for cat in cats:
            cat.setdefault("id", _new_id())
            cat.setdefault("collapsed", False)
            cat.setdefault("items", [])
            for item in cat["items"]:
                item.setdefault("id", _new_id())
        self._cats: List[dict] = cats
        if not isinstance(data, dict) or data.get("version") != VERSION:
            self._save()

    # -- reading -------------------------------------------------------------
    def all(self) -> List[dict]:
        """A deep copy, so callers can't accidentally change our data."""
        return copy.deepcopy(self._cats)

    def find_category(self, cat_id: str) -> Optional[dict]:
        return next((c for c in self._cats if c["id"] == cat_id), None)

    def find_item(self, item_id: str):
        """Returns (category, item) or (None, None)."""
        for cat in self._cats:
            for item in cat["items"]:
                if item["id"] == item_id:
                    return cat, item
        return None, None

    def auto_sections(self) -> Dict[str, str]:
        """{"drives": category id, ...} for the live sections that exist."""
        return {c["auto"]: c["id"] for c in self._cats if c.get("auto")}

    # -- categories ----------------------------------------------------------
    def add_category(self, name: str, index: Optional[int] = None, auto: Optional[str] = None) -> str:
        cat = {"id": _new_id(), "name": name, "collapsed": False, "items": []}
        if auto:
            cat["auto"] = auto
        self._cats.insert(len(self._cats) if index is None else index, cat)
        self._changed()
        return cat["id"]

    def rename_category(self, cat_id: str, name: str) -> None:
        cat = self.find_category(cat_id)
        if cat and name:
            cat["name"] = name
            self._changed()

    def delete_category(self, cat_id: str) -> None:
        self._cats = [c for c in self._cats if c["id"] != cat_id]
        self._changed()

    def move_category(self, cat_id: str, delta: int) -> None:
        idx = next((i for i, c in enumerate(self._cats) if c["id"] == cat_id), None)
        if idx is None:
            return
        new = max(0, min(len(self._cats) - 1, idx + delta))
        self._cats.insert(new, self._cats.pop(idx))
        self._changed()

    def set_collapsed(self, cat_id: str, collapsed: bool) -> None:
        cat = self.find_category(cat_id)
        if cat and cat.get("collapsed") != collapsed:
            cat["collapsed"] = collapsed
            self._changed(publish=False)       # no need to rebuild the sidebar

    # -- live entries (drives, mounts, cloud) ------------------------------------
    def hide_live(self, cat_id: str, path: str) -> None:
        """Stop showing one plugged-in/mounted entry in this live section."""
        cat = self.find_category(cat_id)
        if cat is not None and path not in cat.setdefault("hidden", []):
            cat["hidden"].append(path)
            self._changed()

    def unhide_all(self, cat_id: str) -> None:
        cat = self.find_category(cat_id)
        if cat is not None and cat.get("hidden"):
            cat["hidden"] = []
            self._changed()

    # -- items ---------------------------------------------------------------
    def add_item(self, cat_id: str, path: str, label: str, kind: str = "folder",
                 index: Optional[int] = None) -> Optional[str]:
        cat = self.find_category(cat_id)
        if cat is None:
            return None
        item = {"id": _new_id(), "label": label, "path": path, "kind": kind}
        cat["items"].insert(len(cat["items"]) if index is None else index, item)
        self._changed()
        return item["id"]

    def remove_item(self, item_id: str) -> None:
        for cat in self._cats:
            cat["items"] = [i for i in cat["items"] if i["id"] != item_id]
        self._changed()

    def rename_item(self, item_id: str, label: str) -> None:
        _, item = self.find_item(item_id)
        if item and label:
            item["label"] = label
            self._changed()

    def move_item(self, item_id: str, target_cat_id: str, index: Optional[int] = None) -> None:
        """Move an item to another (or the same) category at ``index``."""
        src_cat, item = self.find_item(item_id)
        dst_cat = self.find_category(target_cat_id)
        if not item or not dst_cat:
            return
        old_index = src_cat["items"].index(item)
        src_cat["items"].remove(item)
        if index is None:
            index = len(dst_cat["items"])
        elif src_cat is dst_cat and old_index < index:
            index -= 1          # list got shorter in front of the target position
        dst_cat["items"].insert(max(0, min(index, len(dst_cat["items"]))), item)
        self._changed()

    def shift_item(self, item_id: str, delta: int) -> None:
        cat, item = self.find_item(item_id)
        if not item:
            return
        idx = cat["items"].index(item)
        new = max(0, min(len(cat["items"]) - 1, idx + delta))
        cat["items"].insert(new, cat["items"].pop(idx))
        self._changed()

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        self._store.save({"version": VERSION, "categories": self._cats})

    def _changed(self, publish: bool = True) -> None:
        self._save()
        if publish:
            self._bus.publish(Topics.CATEGORIES_CHANGED)

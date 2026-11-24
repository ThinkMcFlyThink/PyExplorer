"""
FileHistory - what PyExplorer did to your files (the History tab, Ctrl+H).

    rec = history.record("rename", [{"src": "/x/a.txt", "dst": "/x/b.txt"}])
    history.records()            -> newest first
    history.pending(rec)         -> indexes of items that can still be undone
    history.mark_undone(rec.id, [0])

Every record is one user action: a rename, a move, a copy, a new folder,
a trip to the trash... Its ``items`` are the files/folders it touched:

    {"src": old path or None, "dst": new path or None, "dir": bool,
     "undone": bool, "blocked": "why this one can't be undone" (optional)}

What can be undone (the work itself is FileOperations.undo):
    rename  -> rename back             move   -> move back
    copy    -> trash the copies        create -> trash the new item
    trash / delete                     -> no (only listed)

Kept between sessions in history.json, within a size budget: the newest
records are saved until SAVE_BUDGET_ITEMS paths are used, so a 100k-file
bulk rename stays in memory for this session but never bloats the file.
Records older than MAX_AGE_DAYS are dropped. Turning off "Keep after
closing" (setting history.keep) deletes the file and stops saving.

Plain Python, UI thread only: background code hands records over through
the dispatcher (see app.py).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

MAX_RECORDS = 1000              # in memory (oldest dropped first)
SAVE_BUDGET_ITEMS = 20_000      # paths written to history.json (a few MB at most)
MAX_AGE_DAYS = 30

# kind -> (past-tense label, undo button label or "" when it can't be undone)
KINDS: Dict[str, tuple] = {
    "rename": ("Renamed", "Rename back"),
    "move": ("Moved", "Move back"),
    "copy": ("Copied", "Remove the copies"),
    "create": ("Created", "Remove"),
    "trash": ("Moved to trash", ""),
    "delete": ("Deleted permanently", ""),
}


def can_undo_kind(kind: str) -> bool:
    return bool(KINDS.get(kind, ("", ""))[1])


@dataclass
class Record:
    id: str
    kind: str
    time: float
    items: List[dict]
    source: str = ""                    # e.g. "Rename tab", "Drag and drop" (shown as a hint)
    undone_at: float = 0.0              # last time some of it was undone
    session_only: bool = field(default=False, compare=False)   # too big to save

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, (self.kind.title(), ""))[0]

    @property
    def undo_label(self) -> str:
        return KINDS.get(self.kind, ("", ""))[1]

    def pending(self) -> List[int]:
        """Indexes of items that can still be undone."""
        if not can_undo_kind(self.kind):
            return []
        return [i for i, it in enumerate(self.items) if not it.get("undone") and not it.get("blocked")]

    @property
    def undone(self) -> bool:
        """Everything that could be undone has been."""
        return bool(self.undone_at) and not self.pending()

    @property
    def partly_undone(self) -> bool:
        return any(it.get("undone") for it in self.items) and bool(self.pending())

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "time": self.time, "items": self.items,
                "source": self.source, "undone_at": self.undone_at}

    @classmethod
    def from_dict(cls, d: dict) -> Optional["Record"]:
        try:
            items = [dict(it) for it in d["items"] if isinstance(it, dict)]
            return cls(str(d["id"]), str(d["kind"]), float(d["time"]), items,
                       str(d.get("source") or ""), float(d.get("undone_at") or 0))
        except (KeyError, TypeError, ValueError):
            return None


class FileHistory:
    def __init__(self, store=None, keep: Callable[[], bool] = lambda: True,
                 on_change: Optional[Callable[[], None]] = None) -> None:
        self._store = store             # core.storage.JsonStore, or None (tests / never saved)
        self._keep = keep
        self._on_change = on_change
        self._records: List[Record] = []          # oldest first
        self.dirty = False
        self.load()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def records(self) -> List[Record]:
        """Newest first."""
        return list(reversed(self._records))

    def get(self, record_id: str) -> Optional[Record]:
        for rec in self._records:
            if rec.id == record_id:
                return rec
        return None

    def __len__(self) -> int:
        return len(self._records)

    # ------------------------------------------------------------------
    # Changing
    # ------------------------------------------------------------------
    def record(self, kind: str, items: Iterable[dict], source: str = "") -> Optional[Record]:
        items = [dict(it) for it in items]
        if not items:
            return None
        rec = Record(uuid.uuid4().hex[:12], kind, time.time(), items, source)
        self._records.append(rec)
        del self._records[:-MAX_RECORDS]
        self._changed()
        return rec

    def mark_undone(self, record_id: str, indexes: Iterable[int]) -> None:
        rec = self.get(record_id)
        if rec is None:
            return
        any_done = False
        for i in indexes:
            if 0 <= i < len(rec.items):
                rec.items[i]["undone"] = True
                any_done = True
        if any_done:
            rec.undone_at = time.time()
            self._changed()

    def mark_undone_pairs(self, record_id: str, pairs: Iterable[tuple]) -> None:
        """Same, by (src, dst) - for callers that only know the paths (the Rename tab)."""
        rec = self.get(record_id)
        if rec is None:
            return
        wanted = set(pairs)
        self.mark_undone(record_id, [i for i, it in enumerate(rec.items) if (it.get("src"), it.get("dst")) in wanted])

    def remove(self, record_ids: Iterable[str]) -> None:
        ids = set(record_ids)
        before = len(self._records)
        self._records = [r for r in self._records if r.id not in ids]
        if len(self._records) != before:
            self._changed()

    def clear(self) -> None:
        if self._records:
            self._records = []
            self._changed()

    def _changed(self) -> None:
        self.dirty = True
        if self._on_change:
            self._on_change()

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------
    def _to_save(self) -> List[Record]:
        """The newest records that fit in the budget (and are recent enough)."""
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        budget = SAVE_BUDGET_ITEMS
        keep: List[Record] = []
        for rec in reversed(self._records):
            rec.session_only = True
            if rec.time < cutoff:
                continue
            if len(rec.items) > budget:
                continue                    # too big: this session only (smaller, older ones may still fit)
            budget -= len(rec.items)
            rec.session_only = False
            keep.append(rec)
        keep.reverse()
        return keep

    def update_session_only(self) -> None:
        """Refresh each record's ``session_only`` flag (shown in the History tab)."""
        if self._keep():
            self._to_save()
        else:
            for rec in self._records:
                rec.session_only = True

    def save(self) -> None:
        if self._store is None:
            return
        if not self._keep():
            self.forget_saved()
            return
        self._store.save({"version": 1, "records": [r.to_dict() for r in self._to_save()]})
        self.dirty = False

    def forget_saved(self) -> None:
        """Delete history.json (used when "Keep after closing" is turned off)."""
        self.dirty = False
        if self._store is not None:
            try:
                self._store.path.unlink()
            except OSError:
                pass

    def load(self) -> None:
        if self._store is None or not self._keep():
            return
        data = self._store.load(default=None) or {}
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        records = [Record.from_dict(d) for d in data.get("records") or [] if isinstance(d, dict)]
        self._records = [r for r in records if r is not None and r.time >= cutoff][-MAX_RECORDS:]
        self.dirty = False

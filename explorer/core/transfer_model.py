"""
Transfer model - what the copy/move window shows, as plain data (no Qt, no I/O).

    Transfer      one copy or move the user started: what, where, when, how fast,
                  and one FileItem per file/folder with its state
    TransferLog   every transfer of this session (and earlier ones, from
                  transfers.json): the window doubles as a transfer history that
                  the user clears on demand

The engine (services/transfer_engine.py) fills these in from a worker thread;
the UI (ui/transfers/) only reads them, on a timer. Attribute writes are
atomic under the GIL, so no locks are needed for the display.

File states
-----------
    pending -> copying -> verifying -> verified        (green tick)
                                     -> verify_failed   (red cross: hashes differ)
                       -> copied        (verification off)
                       -> failed        (red cross, with the reason)
    moved       a whole item moved by renaming (same drive: nothing to verify)
    skipped     the user chose "skip" for a name conflict
    not_copied  never reached: the transfer was cancelled or a drive was full
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional, Tuple

PENDING, COPYING, VERIFYING = "pending", "copying", "verifying"
VERIFIED, COPIED, MOVED = "verified", "copied", "moved"
FAILED, VERIFY_FAILED, SKIPPED, NOT_COPIED = "failed", "verify_failed", "skipped", "not_copied"

GOOD = {VERIFIED, COPIED, MOVED}
BAD = {FAILED, VERIFY_FAILED}
FINAL = GOOD | BAD | {SKIPPED, NOT_COPIED}

STATE_LABELS: Dict[str, str] = {
    PENDING: "Waiting", COPYING: "Copying", VERIFYING: "Verifying", VERIFIED: "Verified", COPIED: "Copied",
    MOVED: "Moved", FAILED: "Failed", VERIFY_FAILED: "Verification failed", SKIPPED: "Skipped",
    NOT_COPIED: "Not copied",
}

# Transfer states
PREPARING, RUNNING, PAUSED = "preparing", "running", "paused"
DONE, CANCELLED, NO_SPACE, ERROR = "done", "cancelled", "no_space", "error"
ACTIVE = {PREPARING, RUNNING, PAUSED}

MAX_SAMPLES = 600                 # speed plot points kept per transfer (every 0.5 s: 5 minutes, then thinned)
SAVE_BUDGET_FILES = 50_000        # full file lists written to transfers.json, newest transfers first
MAX_BAD_ROWS = 10_000             # failed / not-copied rows always saved (per transfer), whatever the budget


class FileItem:
    __slots__ = ("src", "dst", "rel", "size", "is_dir", "state", "error", "action", "top")

    def __init__(self, src: str, dst: str, rel: str, size: int = 0, is_dir: bool = False,
                 action: str = "file", top: int = 0, state: str = PENDING, error: str = "") -> None:
        self.src = src
        self.dst = dst
        self.rel = rel              # shown in the list: path below the copied item ("Photos/2024/a.jpg")
        self.size = size
        self.is_dir = is_dir
        self.action = action        # file | mkdir | rename | link
        self.top = top              # which of the transfer's sources it belongs to
        self.state = state
        self.error = error

    def to_list(self) -> list:
        return [self.src, self.dst, self.rel, self.size, self.is_dir, self.state, self.error]

    @classmethod
    def from_list(cls, row: list) -> "FileItem":
        src, dst, rel, size, is_dir, state, error = row
        if state not in FINAL:
            state = NOT_COPIED                    # the app closed mid-transfer
        return cls(src, dst, rel, int(size), bool(is_dir), state=state, error=error)


class Transfer:
    def __init__(self, kind: str, sources: List[str], dest: str, verify: bool = True, algorithm: str = "") -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                          # "copy" | "move"
        self.sources = list(sources)
        self.dest = dest
        self.verify = verify
        self.algorithm = algorithm
        self.state = PREPARING
        self.message = ""                         # e.g. "Not enough space on Backup: needs 12 GB, 3 GB free"
        self.started = time.time()
        self.finished = 0.0
        self.files: List[FileItem] = []
        self.total_bytes = 0
        self.done_bytes = 0                       # bytes written (verification reads are not counted)
        self.current = ""                         # the file being worked on
        self.samples: List[Tuple[float, float]] = []   # (seconds since start, bytes per second)
        self.files_trimmed = 0                    # loaded from disk without all its rows (budget)
        self.active_seconds = 0.0                 # time spent copying (pauses excluded): for the average speed
        self.job = None                           # core.tasks.Job (status bar + cancel), while running

    # -- numbers ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.files:
            if not f.is_dir:
                out[f.state] = out.get(f.state, 0) + 1
        return out

    @property
    def file_count(self) -> int:
        return sum(1 for f in self.files if not f.is_dir) + self.files_trimmed

    @property
    def progress(self) -> float:
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        files = [f for f in self.files if not f.is_dir] or self.files
        if not files:
            return 1.0 if not self.active else 0.0
        return sum(1 for f in files if f.state in FINAL) / len(files)

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def average_speed(self) -> float:
        seconds = self.active_seconds or self.elapsed
        return self.done_bytes / seconds if seconds > 0 else 0.0

    @property
    def current_speed(self) -> float:
        return self.samples[-1][1] if self.samples and self.active else 0.0

    @property
    def eta(self) -> Optional[float]:
        speed = self.current_speed or self.average_speed
        if not self.active or speed <= 0 or not self.total_bytes:
            return None
        return max(0.0, (self.total_bytes - self.done_bytes) / speed)

    @property
    def failed(self) -> int:
        c = self.counts()
        return sum(c.get(s, 0) for s in BAD)

    @property
    def ok(self) -> bool:
        """Finished, and every file arrived (verified where verification was on)."""
        return self.state == DONE and self.failed == 0 and not self.counts().get(NOT_COPIED)

    def add_sample(self, speed: float) -> None:
        self.samples.append((self.elapsed, speed))
        if len(self.samples) > MAX_SAMPLES:          # long transfer: keep the whole curve, at half the detail
            self.samples = self.samples[::2]

    def finish(self, state: str, message: str = "") -> None:
        for f in self.files:
            if f.state not in FINAL:
                f.state = NOT_COPIED
        self.state = state
        if message:
            self.message = message
        self.finished = time.time()
        self.current = ""

    # -- saving ------------------------------------------------------------------
    def to_dict(self, max_files: Optional[int] = None) -> dict:
        rows = self.files
        trimmed = self.files_trimmed
        if max_files is not None and len(rows) > max_files:
            bad = [f for f in rows if f.state in BAD or f.state == NOT_COPIED]
            rows = bad[:MAX_BAD_ROWS]                    # keep what the user needs to act on
            trimmed += len([f for f in self.files if not f.is_dir]) - len([f for f in rows if not f.is_dir])
        return {"id": self.id, "kind": self.kind, "sources": self.sources, "dest": self.dest, "verify": self.verify,
                "algorithm": self.algorithm, "state": self.state if not self.active else CANCELLED,
                "message": self.message, "started": self.started, "finished": self.finished or time.time(),
                "total_bytes": self.total_bytes, "done_bytes": self.done_bytes, "active_seconds": self.active_seconds,
                "samples": self.samples[-120:], "files_trimmed": trimmed, "files": [f.to_list() for f in rows]}

    @classmethod
    def from_dict(cls, d: dict) -> Optional["Transfer"]:
        try:
            t = cls(d["kind"], d["sources"], d["dest"], bool(d.get("verify", True)), d.get("algorithm", ""))
            t.id = d["id"]
            t.state = d.get("state", DONE)
            t.message = d.get("message", "")
            t.started = float(d["started"])
            t.finished = float(d.get("finished") or t.started)
            t.total_bytes = int(d.get("total_bytes") or 0)
            t.done_bytes = int(d.get("done_bytes") or 0)
            t.active_seconds = float(d.get("active_seconds") or 0)
            t.samples = [tuple(s) for s in d.get("samples") or []]
            t.files_trimmed = int(d.get("files_trimmed") or 0)
            t.files = [FileItem.from_list(r) for r in d.get("files") or []]
            return t
        except (KeyError, TypeError, ValueError):
            return None


class TransferLog:
    """All transfers, newest last. ``store`` is a core.storage.JsonStore (or None)."""

    def __init__(self, store=None, keep=lambda: True) -> None:
        self._store = store
        self._keep = keep
        self.transfers: List[Transfer] = []
        self.dirty = False
        if store is not None and keep():
            data = store.load(default=None) or {}
            loaded = [Transfer.from_dict(d) for d in data.get("transfers") or [] if isinstance(d, dict)]
            self.transfers = [t for t in loaded if t is not None]

    def add(self, transfer: Transfer) -> None:
        self.transfers.append(transfer)
        self.dirty = True

    def get(self, transfer_id: str) -> Optional[Transfer]:
        return next((t for t in self.transfers if t.id == transfer_id), None)

    def active(self) -> List[Transfer]:
        return [t for t in self.transfers if t.active]

    def clear(self, transfer_ids: Optional[List[str]] = None) -> None:
        """Forget finished transfers (all, or the given ones). Running ones stay."""
        ids = set(transfer_ids) if transfer_ids is not None else None
        self.transfers = [t for t in self.transfers if t.active or (ids is not None and t.id not in ids)]
        self.dirty = True

    def save(self) -> None:
        if self._store is None:
            return
        if not self._keep():
            try:
                self._store.path.unlink()
            except OSError:
                pass
            self.dirty = False
            return
        budget = SAVE_BUDGET_FILES
        out = []
        for t in reversed(self.transfers):               # newest get their full file lists first
            d = t.to_dict(max_files=max(0, budget))
            budget -= len(t.files) if len(d["files"]) == len(t.files) else 0
            out.append(d)
        out.reverse()
        self._store.save({"version": 1, "transfers": out})
        self.dirty = False

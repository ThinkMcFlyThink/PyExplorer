"""
TransferEngine - PyExplorer's own copy/move (the transfer window), in the
spirit of a simplified TeraCopy. Every paste, drop and "copy/move to other
panel" comes here (unless Settings -> Transfers -> engine is "simple",
which uses the older FileOperations.transfer).

    t = engine.start(["/a/photos"], "/mnt/backup", move=False)    # -> core.transfer_model.Transfer
    engine.pause(t) / engine.resume(t) / engine.cancel(t)

What it promises
----------------
* Verify by default: each file is hashed WHILE it is copied, then the copy is
  read back and hashed again. (Local copies are flushed to disk and dropped
  from the OS cache first, so the read-back really comes from the disk.)
* One bad file never stops the transfer: it is marked failed (with the reason)
  and the next one starts. Only a FULL DRIVE stops it - continuing would just
  fail every remaining file.
* Checks the space first: a transfer that would fill the destination drive
  does not start; the user is told how much is needed and how much is free.
* Moves delete an original only after its copy is verified. Moves within one
  drive are a rename: instant, nothing to verify.
* Never leaves half-written files: a failed or cancelled file is removed.

Steps: plan (walk the sources, ask about name conflicts) -> space check ->
copy/verify each item -> tidy up (moves: remove emptied source folders) ->
record in the History tab.

No Qt. It talks to the rest of the app only through the callbacks given to
__init__ (called from the worker thread; app.py hops them to the UI thread).
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from ..core.tasks import Cancelled
from ..core.transfer_model import (BAD, CANCELLED, COPIED, COPYING, DONE, ERROR, FAILED, GOOD, MOVED, NO_SPACE,
                                   PAUSED, PENDING, RUNNING, SKIPPED, VERIFIED, VERIFY_FAILED, VERIFYING,
                                   FileItem, Transfer, TransferLog)
from ..core.formatting import human_size
from .fileops import Conflict, unique_name
from .search import is_inside

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024
SAMPLE_EVERY = 0.5                       # seconds between speed-plot points
BLOCK = 4096                             # files use whole blocks on disk: count them that way
RESERVE = 32 * 1024 * 1024               # never fill a drive to the last byte
DISK_FULL_WINERRORS = {39, 112}          # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL

ALGORITHMS = {"blake2b": "BLAKE2b", "xxh3": "xxHash3 (needs the xxhash package)", "md5": "MD5",
              "sha1": "SHA-1", "sha256": "SHA-256"}


def new_hasher(name: str):
    if name == "xxh3":
        try:
            import xxhash
            return xxhash.xxh3_128()
        except ImportError:
            name = "blake2b"
    try:
        return hashlib.new(name)
    except (ValueError, TypeError):
        return hashlib.blake2b()


def is_disk_full(exc: BaseException) -> bool:
    if getattr(exc, "errno", None) in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return True
    if getattr(exc, "winerror", None) in DISK_FULL_WINERRORS:
        return True
    text = str(exc).lower()
    return "no space left" in text or "disk full" in text or "quota exceeded" in text


def space_needed(files: List[FileItem]) -> int:
    """Bytes the copies will take on disk (whole blocks; renames take nothing)."""
    total = 0
    for f in files:
        if f.state != PENDING or f.action == "rename":
            continue
        total += BLOCK if f.is_dir else -(-max(f.size, 1) // BLOCK) * BLOCK
    return total


class _DiskFull(Exception):
    pass


class TransferEngine:
    def __init__(self, vfs, runner, jobs, transfers: TransferLog, options: Callable[[], dict],
                 ask_conflict: Callable, publish_changed: Callable[[List[str]], None],
                 record_history: Callable[[str, List[dict]], None],
                 on_event: Callable[[str, Transfer], None]) -> None:
        """``options()`` -> {"verify": bool, "algorithm": str, "verify_from_disk": bool}.
        ``on_event(event, transfer)`` with event = started | planned | no_space | finished."""
        self._vfs = vfs
        self._runner = runner
        self._jobs = jobs
        self.log = transfers
        self._options = options
        self._ask_conflict = ask_conflict
        self._publish_changed = publish_changed
        self._record = record_history
        self._on_event = on_event
        self._running: Dict[str, threading.Event] = {}     # transfer id -> set while NOT paused
        self._clock: Dict[str, list] = {}                  # transfer id -> [last sample time, bytes then]

    # ==================================================================
    # Control (UI thread)
    # ==================================================================
    def start(self, sources: List[str], dest: str, move: bool) -> Transfer:
        opts = self._options()
        t = Transfer("move" if move else "copy", sources, dest, bool(opts.get("verify", True)),
                     opts.get("algorithm") or "blake2b")
        n = len(sources)
        t.job = self._jobs.start(f"{'Moving' if move else 'Copying'} {n} item{'s' if n != 1 else ''}")
        run = threading.Event()
        run.set()
        self._running[t.id] = run
        self.log.add(t)
        self._runner.submit(self._run, t, dict(opts), pool="transfer", on_error=lambda exc: None)
        self._on_event("started", t)
        return t

    def pause(self, t: Transfer) -> None:
        run = self._running.get(t.id)
        if run is not None and t.state == RUNNING:
            run.clear()
            t.state = PAUSED

    def resume(self, t: Transfer) -> None:
        run = self._running.get(t.id)
        if run is not None and t.state == PAUSED:
            t.state = RUNNING
            run.set()

    def cancel(self, t: Transfer) -> None:
        if t.job is not None:
            t.job.token.cancel()
        run = self._running.get(t.id)
        if run is not None:
            run.set()                                   # a paused transfer must wake up to stop

    # ==================================================================
    # The work (worker thread)
    # ==================================================================
    def _run(self, t: Transfer, opts: dict) -> None:
        move = t.kind == "move"
        changed = {t.dest}
        tops: List[dict] = []
        try:
            dst_fs = self._vfs.resolve(t.dest)
            self._plan(t, dst_fs, move, tops, changed)
            self._on_event("planned", t)
            need = space_needed(t.files)
            usage = self._disk_usage(dst_fs, t.dest) if need else None
            if usage is not None and need > usage[2] - RESERVE:
                t.finish(NO_SPACE, f"Not enough space: this needs {human_size(need)}, "
                                   f"but only {human_size(max(0, usage[2]))} is free on the destination drive. "
                                   "Nothing was copied.")
                self._on_event("no_space", t)
                return
            t.state = RUNNING
            self._clock[t.id] = [time.monotonic(), 0]
            try:
                self._execute(t, dst_fs, move, opts)
            except _DiskFull as full:
                t.finish(NO_SPACE, str(full))
                self._on_event("no_space", t)
                return
            if move:
                self._remove_moved_folders(t)
            self._mark_folders(t)
            t.finish(DONE)
        except Cancelled:
            t.finish(CANCELLED, "Cancelled")
        except Exception as exc:  # noqa: BLE001  (never leave a transfer "running" forever)
            log.exception("Transfer failed")
            t.finish(ERROR, str(exc))
        finally:
            self._running.pop(t.id, None)
            self._clock.pop(t.id, None)
            if t.job is not None:
                bad = t.failed
                self._jobs.finish(t.job, t.message if t.state not in (DONE,) else
                                  (f"{bad} file{'s' if bad != 1 else ''} failed" if bad else None))
            self._publish_changed(sorted(changed))
            self._record_history(t, tops)
            self._on_event("finished", t)

    # -- 1. plan --------------------------------------------------------------------
    def _plan(self, t: Transfer, dst_fs, move: bool, tops: List[dict], changed: set) -> None:
        policy = {"choice": None}
        for top, src in enumerate(t.sources):
            self._checkpoint(t)
            src_fs = self._vfs.resolve(src)
            name = src_fs.basename(src)
            try:
                entry = src_fs.stat(src)
            except OSError as exc:
                t.files.append(FileItem(src, "", name, top=top, state=FAILED, error=str(exc)))
                continue
            if entry.is_dir and is_inside(t.dest, src):
                t.files.append(FileItem(src, "", name, is_dir=True, top=top, state=FAILED,
                                        error="Can't put a folder inside itself"))
                continue
            parent = src_fs.parent(src)
            if parent:
                changed.add(parent)
            same_folder = parent is not None and src_fs is dst_fs and \
                src_fs.normalize(parent) == dst_fs.normalize(t.dest)
            if same_folder and move:
                continue                                         # moving onto itself: nothing to do
            if same_folder:                                      # copy into its own folder = duplicate
                target = dst_fs.join(t.dest, unique_name(dst_fs, t.dest, name, entry.is_dir))
            else:
                target = self._resolve_conflict(dst_fs, t.dest, entry, dst_fs.join(t.dest, name), policy)
                if target is None:
                    t.files.append(FileItem(src, dst_fs.join(t.dest, name), name, entry.size, entry.is_dir,
                                            top=top, state=SKIPPED, error="Skipped: the name already exists"))
                    continue
            existed = dst_fs.exists(target)
            info = {"src": src, "dst": target, "dir": entry.is_dir, "top": top}
            if existed and entry.is_dir and not entry.is_link:
                info["blocked"] = "Merged into a folder that already existed"
            elif existed and not move:
                info["blocked"] = "Replaced an existing file, which can't be brought back"
            tops.append(info)
            if move and src_fs is dst_fs and src_fs.same_volume(src, t.dest):
                t.files.append(FileItem(src, target, dst_fs.basename(target), entry.size, entry.is_dir,
                                        action="rename", top=top))
                continue
            self._expand(t, src_fs, dst_fs, entry, target, dst_fs.basename(target), top, policy)

    def _expand(self, t: Transfer, src_fs, dst_fs, entry, target: str, rel: str, top: int, policy: dict) -> None:
        both_local = src_fs.is_local and dst_fs.is_local
        if entry.is_link and both_local:
            t.files.append(FileItem(entry.path, target, rel, 0, False, action="link", top=top))
            return
        if not entry.is_dir or entry.is_link:
            t.files.append(FileItem(entry.path, target, rel, entry.size, False, top=top))
            t.total_bytes += entry.size
            if len(t.files) % 500 == 0:
                t.current = f"Looking for files… {len(t.files):,} found"
            return
        t.files.append(FileItem(entry.path, target, rel, 0, True, action="mkdir", top=top))
        merging = dst_fs.exists(target)
        try:
            children = list(src_fs.list_dir(entry.path))
        except OSError as exc:
            t.files[-1].state = FAILED
            t.files[-1].error = f"Couldn't read this folder: {exc}"
            return
        for child in children:
            self._checkpoint(t)
            child_target = dst_fs.join(target, child.name)
            if merging and not child.is_dir:
                resolved = self._resolve_conflict(dst_fs, target, child, child_target, policy)
                if resolved is None:
                    t.files.append(FileItem(child.path, child_target, f"{rel}/{child.name}", child.size, False,
                                            top=top, state=SKIPPED, error="Skipped: the name already exists"))
                    continue
                child_target = resolved
            self._expand(t, src_fs, dst_fs, child, child_target, f"{rel}/{dst_fs.basename(child_target)}", top, policy)

    def _resolve_conflict(self, dst_fs, dest_dir: str, entry, target: str, policy: dict) -> Optional[str]:
        try:
            existing = dst_fs.stat(target)
        except OSError:
            return target
        if existing.is_dir and entry.is_dir:
            return target                                    # folders merge
        choice = policy["choice"]
        if choice is None:
            choice, apply_all = self._ask_conflict(entry, existing)
            if apply_all:
                policy["choice"] = choice
        if choice == Conflict.CANCEL:
            raise Cancelled()
        if choice == Conflict.SKIP:
            return None
        if choice == Conflict.RENAME:
            return dst_fs.join(dest_dir, unique_name(dst_fs, dest_dir, entry.name, entry.is_dir))
        return target

    @staticmethod
    def _disk_usage(fs, path: str):
        try:
            return fs.disk_usage(path)
        except Exception:  # noqa: BLE001  (servers that can't tell: no check)
            return None

    # -- 2. copy / verify -------------------------------------------------------------
    def _execute(self, t: Transfer, dst_fs, move: bool, opts: dict) -> None:
        failed_dirs = set()
        for f in t.files:
            if f.state != PENDING:
                if f.is_dir and f.state in BAD:
                    failed_dirs.add(f.dst)
                continue
            self._checkpoint(t)
            src_fs = self._vfs.resolve(f.src)
            parent = dst_fs.parent(f.dst)
            if parent in failed_dirs:
                f.state, f.error = FAILED, "Its folder could not be created"
                if f.is_dir:
                    failed_dirs.add(f.dst)
                continue
            t.current = f.rel
            try:
                if f.action == "mkdir":
                    if not dst_fs.exists(f.dst):
                        dst_fs.mkdir(f.dst)
                    f.state = VERIFIED if t.verify else COPIED
                elif f.action == "rename":
                    if dst_fs.exists(f.dst):                     # the user chose "replace"
                        dst_fs.remove_tree(f.dst)
                    src_fs.rename(f.src, f.dst)
                    f.state = MOVED
                elif f.action == "link":
                    os.symlink(os.readlink(f.src), f.dst)
                    f.state = COPIED
                else:
                    self._copy_file(t, f, src_fs, dst_fs, opts)
                if move and f.state in GOOD and f.action in ("file", "link"):
                    try:
                        src_fs.remove_file(f.src)
                    except OSError as exc:
                        f.state = FAILED
                        f.error = f"Copied, but the original couldn't be removed: {exc}"
            except Cancelled:
                raise
            except OSError as exc:
                f.state, f.error = FAILED, str(exc) or exc.__class__.__name__
                if f.is_dir:
                    failed_dirs.add(f.dst)
                if is_disk_full(exc):
                    f.error = "The destination drive is full"
                    raise _DiskFull(f"Stopped: the destination drive is full (while copying {f.rel}). "
                                    "The files not copied yet are marked.")
            self._tick(t)

    def _copy_file(self, t: Transfer, f: FileItem, src_fs, dst_fs, opts: dict) -> None:
        f.state = COPYING
        hasher = new_hasher(t.algorithm) if t.verify else None
        from_disk = t.verify and dst_fs.is_local and opts.get("verify_from_disk", True)
        written = 0
        try:
            with src_fs.open_read(f.src) as reader, dst_fs.open_write(f.dst) as writer:
                while True:
                    self._checkpoint(t)
                    chunk = reader.read(CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    written += len(chunk)
                    t.done_bytes += len(chunk)
                    self._tick(t)
                if from_disk:
                    writer.flush()
                    os.fsync(writer.fileno())
        except BaseException:
            t.done_bytes -= written                          # (it will be counted as not copied)
            try:
                dst_fs.remove_file(f.dst)                    # never leave a half-written file
            except OSError:
                pass
            raise
        if written != f.size:                                # it changed while we copied: the new size counts
            t.total_bytes += written - f.size
        self._copy_metadata(src_fs, dst_fs, f)
        if hasher is None:
            f.state = COPIED
            return
        f.state = VERIFYING
        if from_disk:
            self._drop_cache(f.dst)
        check = new_hasher(t.algorithm)
        with dst_fs.open_read(f.dst) as reader:
            while True:
                self._checkpoint(t)
                chunk = reader.read(CHUNK)
                if not chunk:
                    break
                check.update(chunk)
        if check.digest() != hasher.digest():
            f.state = VERIFY_FAILED
            f.error = "The copy is different from the original (checksums differ)"
        else:
            f.state = VERIFIED

    @staticmethod
    def _copy_metadata(src_fs, dst_fs, f: FileItem) -> None:
        try:
            entry = src_fs.stat(f.src)
            if entry.mtime:
                dst_fs.set_mtime(f.dst, entry.mtime)
        except OSError:
            pass
        if src_fs.is_local and dst_fs.is_local:
            try:
                import shutil
                shutil.copymode(f.src, f.dst)
            except OSError:
                pass

    @staticmethod
    def _drop_cache(path: str) -> None:
        """Make the verification read come from the disk, not from memory (Linux)."""
        if not hasattr(os, "posix_fadvise"):
            return
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
        except OSError:
            pass

    def _checkpoint(self, t: Transfer) -> None:
        """Stop here if cancelled; wait here while paused."""
        if t.job is not None:
            t.job.token.check()
        run = self._running.get(t.id)
        if run is not None and not run.is_set():
            run.wait()
            clock = self._clock.get(t.id)
            if clock is not None:                            # the pause doesn't count as slow copying
                clock[0], clock[1] = time.monotonic(), t.done_bytes
            if t.job is not None:
                t.job.token.check()

    def _tick(self, t: Transfer) -> None:
        clock = self._clock.get(t.id)
        if clock is None:
            return
        now = time.monotonic()
        dt = now - clock[0]
        if dt < SAMPLE_EVERY:
            return
        t.active_seconds += dt
        t.add_sample((t.done_bytes - clock[1]) / dt)
        clock[0], clock[1] = now, t.done_bytes
        if t.job is not None:
            self._jobs.update(t.job, t.progress, t.current)

    # -- 3. tidy up ---------------------------------------------------------------------
    def _remove_moved_folders(self, t: Transfer) -> None:
        """A move copied folder by folder: remove the originals that are now empty."""
        for f in reversed(t.files):
            if f.is_dir and f.action == "mkdir" and f.state in GOOD:
                try:
                    self._vfs.resolve(f.src).remove_dir(f.src)
                except OSError:
                    pass                                     # something in it failed: it stays, with that file

    def _mark_folders(self, t: Transfer) -> None:
        """A folder whose contents failed is not a green tick: say how many failed inside."""
        bad_under: Dict[str, int] = defaultdict(int)
        for f in reversed(t.files):
            n = 0
            if f.is_dir:
                n = bad_under.get(f.dst, 0)
                if n and f.state in GOOD:
                    f.state = FAILED
                    f.error = f"{n:,} item{'s' if n != 1 else ''} inside could not be copied"
            elif f.state in BAD:
                n = 1
            if n and f.dst:
                parent = os.path.dirname(f.dst.rstrip("/\\")) if "://" not in f.dst else f.dst.rsplit("/", 1)[0]
                bad_under[parent] += n

    def _record_history(self, t: Transfer, tops: List[dict]) -> None:
        """Tell the History tab about every source that arrived completely."""
        by_top: Dict[int, bool] = {}
        for f in t.files:
            ok = f.state in GOOD
            by_top[f.top] = by_top.get(f.top, True) and ok
        items = []
        for info in tops:
            if by_top.get(info["top"]):
                items.append({k: v for k, v in info.items() if k != "top"})
        if items:
            self._record(t.kind, items)

"""
parallel_walk - visit every file under a folder using several threads at once.

``os.walk`` reads one folder at a time. Disks (especially SSDs and network
shares) are much faster when several folders are read in parallel, and
Python releases the GIL while waiting for the OS, so threads give a real
speed-up here. Used by search, the search index and "folder size".

How it works: a shared queue of folders still to read. Each worker takes a
folder, lists it, sends the entries to ``emit`` and adds any sub-folders to
the queue. When the queue is empty AND no worker is busy, we are done.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Iterable, List, Optional

from ..core.fileinfo import FileEntry
from ..core.platform_info import IS_LINUX
from ..core.tasks import CancelToken

# Virtual Linux file systems that are huge, infinite or pointless to search.
DEFAULT_SKIP = {"/proc", "/sys", "/dev", "/run", "/snap"} if IS_LINUX else set()


def auto_workers(fs, setting: int = 0) -> int:
    """How many parallel readers to use. On a fast local disk Python is the
    bottleneck, so a few threads is best; network locations wait on the
    network, so more threads help a lot."""
    if setting and setting > 0:
        return setting
    return 3 if fs.is_local else 8


def parallel_walk(fs, root: str, token: CancelToken, emit: Callable[[List[FileEntry]], None],
                  workers: int = 8, batch_size: int = 2000, flush_interval: float = 0.1,
                  skip: Optional[Iterable[str]] = None, light: bool = False) -> int:
    """Blocking call (run it in a worker thread). Returns number of entries seen.
    ``light=True`` skips reading sizes/dates (much faster; see FileSystem.list_dir_light)."""
    lister = fs.list_dir_light if light else fs.list_dir
    skip_set = set(DEFAULT_SKIP if skip is None else skip)
    skip_set.discard(root)                      # searching *inside* /proc on purpose is fine
    pending = deque([root])
    busy = 0                                    # workers currently reading a folder
    cond = threading.Condition()
    out_lock = threading.Lock()
    out: List[FileEntry] = []
    state = {"total": 0, "last": time.monotonic()}

    def flush(force: bool = False) -> None:
        with out_lock:
            if not out:
                return
            now = time.monotonic()
            if force or len(out) >= batch_size or now - state["last"] >= flush_interval:
                batch = out[:]
                out.clear()
                state["last"] = now
                state["total"] += len(batch)
            else:
                return
        emit(batch)

    def worker() -> None:
        nonlocal busy
        while True:
            with cond:
                while not pending and busy and not token.cancelled:
                    cond.wait(0.05)
                if token.cancelled or (not pending and not busy):
                    cond.notify_all()
                    return
                folder = pending.popleft()
                busy += 1
            subdirs = []
            try:
                entries = list(lister(folder))
            except OSError:
                entries = []                     # no permission etc. - just skip it
            for e in entries:
                if e.is_dir and not e.is_link and e.path not in skip_set:
                    subdirs.append(e.path)
            if entries:
                with out_lock:
                    out.extend(entries)
                flush()
            with cond:
                pending.extend(subdirs)
                busy -= 1
                cond.notify_all()

    threads = [threading.Thread(target=worker, name=f"px-walk-{i}", daemon=True)
               for i in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if not token.cancelled:
        flush(force=True)
    return state["total"]


def folder_size(fs, root: str, token: CancelToken, progress: Optional[Callable] = None):
    """(total_bytes, files, folders) of a tree. ``progress(bytes, files, folders)``
    is called now and then from the worker thread."""
    totals = [0, 0, 0]

    def emit(batch: List[FileEntry]) -> None:
        for e in batch:
            if e.is_dir:
                totals[2] += 1
            else:
                totals[0] += e.size
                totals[1] += 1
        if progress:
            progress(*totals)

    parallel_walk(fs, root, token, emit, workers=auto_workers(fs), skip=())
    return tuple(totals)

"""
IndexManager - keeps the search indexes: which folders are indexed, building
new ones, saving them to disk, and keeping them up to date.

    manager.index_for("/home/me/Photos")   # an index that covers this folder, or None
    manager.build("/home/me", token)       # read it from disk (blocking, worker thread)
    manager.computer_roots()               # what "whole computer" means here
    manager.folder_changed("/home/me/x")   # something changed there: patch the index soon

Freshness, without a background service:
  * indexes are saved to ~/.cache/pyexplorer/index (the whole-computer index
    loads in about a second at the next start instead of being rebuilt);
  * folders the app itself changes, and folders open in a tab (the folder
    watcher), are patched into the index within a couple of seconds;
  * an index older than "search.refresh_minutes" still answers instantly, and
    a fresh one is built in the background and swapped in. ``on_updated(root)``
    is then called so open Search tabs can quietly re-run.

Plain Python - the app passes in a ``post`` function to hand ``on_updated``
calls to the UI thread.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import threading
import time
from typing import Callable, Dict, List, Optional

from ..core.platform_info import IS_LINUX, IS_MAC, IS_WINDOWS
from ..core.tasks import CancelToken
from .file_index import Cancelled, FileIndex, build_from_entries, build_local, is_inside
from .walker import auto_workers, parallel_walk

log = logging.getLogger(__name__)

# Never index these (virtual / huge / pointless): the same as the folder walker skips.
SYSTEM_SKIP = (["/proc", "/sys", "/dev", "/run", "/snap", "/var/lib/docker", "/var/snap"] if IS_LINUX else
               ["/System/Volumes", "/dev", "/private/var/vm", "/Volumes"] if IS_MAC else [])
PATCH_DELAY = 1.5                    # seconds of quiet before changed folders are patched in
SAVE_EVERY = 60.0                    # write a patched index to disk at most this often


class IndexManager:
    def __init__(self, vfs, settings, cache_dir: str, devices: Callable[[], list] = lambda: [],
                 post: Callable = lambda fn, *a: fn(*a), on_updated: Callable[[str], None] = lambda root: None
                 ) -> None:
        self._vfs = vfs
        self._settings = settings
        self._cache_dir = cache_dir
        self._devices = devices
        self._post = post
        self._on_updated = on_updated
        self._indexes: Dict[str, FileIndex] = {}
        self._used: Dict[str, float] = {}             # root -> last used (to drop old ones when memory is tight)
        self._building: Dict[str, threading.Event] = {}
        self._refreshing: set = set()
        self._lock = threading.RLock()
        self._dirty: Dict[str, set] = {}              # root -> folders to patch
        self._patch_timer: Optional[threading.Timer] = None
        self._last_save: Dict[str, float] = {}
        self._closed = False
        # Set once the indexes saved last time are loaded (or there are none to load).
        # Searches wait for it, so a restored Search tab never rebuilds what is on disk.
        self.loaded = threading.Event()

    # -- what exists -------------------------------------------------------------
    def index_for(self, location: str) -> Optional[FileIndex]:
        """The smallest index whose root contains ``location`` (or None)."""
        with self._lock:
            best = None
            for root, idx in self._indexes.items():
                if is_inside(location, root) and (best is None or len(root) > len(best.root)):
                    best = idx
            if best is not None:
                self._used[best.root] = time.time()
            return best

    def roots(self) -> List[str]:
        with self._lock:
            return list(self._indexes)

    def total_entries(self) -> int:
        with self._lock:
            return sum(i.count for i in self._indexes.values())

    def is_stale(self, idx: FileIndex) -> bool:
        minutes = float(self._settings.get("search.refresh_minutes", 30) or 0)
        return minutes > 0 and time.time() - idx.built_at > minutes * 60

    # -- "whole computer" ----------------------------------------------------------------
    def computer_roots(self) -> List[str]:
        """Linux/macOS: "/" (USB drives etc. are mounted inside it). Windows: every
        local drive letter. Network drives only if the setting allows."""
        if IS_WINDOWS:
            with_network = bool(self._settings.get("search.index_network", False))
            roots = [d.path for d in self._devices() if d.mounted and
                     (d.section == "drives" or (with_network and d.section == "network"))
                     and len(d.path) <= 3]
            return roots or ["C:\\"]
        return ["/"]

    def skip_for(self, root: str) -> List[str]:
        """Folders not to enter while indexing ``root``: system folders, and
        network / cloud mounts (slow, and may be huge) unless allowed."""
        skip = list(SYSTEM_SKIP)
        if not self._settings.get("search.index_network", False):
            for d in self._devices():
                if d.section in ("network", "cloud") and d.path != root and is_inside(d.path, root):
                    skip.append(d.path)
        skip += [p for p in (self._settings.get("search.skip_folders", []) or []) if p]
        return [s for s in skip if s != root]

    # -- building ---------------------------------------------------------------------------
    def build(self, root: str, token: CancelToken, progress: Callable[[int], None] = lambda n: None) -> FileIndex:
        """Read ``root`` completely (blocking; worker thread). If another thread is
        already building it, wait for that one instead of doing it twice."""
        with self._lock:
            waiting = self._building.get(root)
            if waiting is None:
                self._building[root] = threading.Event()
        if waiting is not None:
            while not waiting.wait(0.2):
                if token.cancelled:
                    raise Cancelled()
            idx = self.index_for(root)
            if idx is not None:
                return idx
            return self.build(root, token, progress)
        try:
            idx = self._read(root, token, progress)
            idx.hidden_dirs()                      # work it out now, not during the first search
            self._store(idx)
            self._save_soon(idx, force=True)
            return idx
        finally:
            with self._lock:
                self._building.pop(root).set()

    def _read(self, root: str, token: CancelToken, progress: Callable[[int], None]) -> FileIndex:
        fs = self._vfs.resolve(root)
        if fs.is_local:
            return build_local(root, skip=self.skip_for(root), token=token, progress=progress)
        sep = "\\" if "\\" in root and "/" not in root else "/"
        seen = [0]

        def walk(emit):
            def counted(batch):
                emit(batch)
                seen[0] += len(batch)
                progress(seen[0])
            parallel_walk(fs, root, token, counted, workers=auto_workers(fs, int(self._settings.get("search.workers", 0) or 0)))
        idx = build_from_entries(root, sep, walk)
        if token.cancelled:
            raise Cancelled()
        return idx

    def _store(self, idx: FileIndex) -> None:
        with self._lock:
            # A bigger index makes the ones inside it redundant.
            for root in [r for r in self._indexes if r != idx.root and is_inside(r, idx.root)]:
                del self._indexes[root]
                self._used.pop(root, None)
            self._indexes[idx.root] = idx
            self._used[idx.root] = time.time()
            limit = int(self._settings.get("search.max_index_entries", 8_000_000) or 0)
            whole = set(self.computer_roots())
            while limit and sum(i.count for i in self._indexes.values()) > limit:
                spare = [r for r in self._indexes if r not in whole and r != idx.root]
                if not spare:
                    break
                oldest = min(spare, key=lambda r: self._used.get(r, 0))
                del self._indexes[oldest]

    def refresh_in_background(self, root: str, runner) -> None:
        """Rebuild ``root`` without blocking anyone; searches keep using the old
        index until the new one is ready."""
        with self._lock:
            if root in self._refreshing or root in self._building:
                return
            self._refreshing.add(root)

        def work():
            try:
                self.build(root, CancelToken())
            finally:
                with self._lock:
                    self._refreshing.discard(root)
            return root
        runner.submit(work, pool="search", on_done=lambda r: self._notify(r))

    def _notify(self, root: str) -> None:
        if not self._closed:
            self._post(self._on_updated, root)

    def forget(self, root: str) -> None:
        with self._lock:
            self._indexes.pop(root, None)
        try:
            os.remove(self._file_for(root))
        except OSError:
            pass

    # -- keeping it current ------------------------------------------------------------------
    def folder_changed(self, folder: str) -> None:
        """Called (any thread) when something inside ``folder`` changed."""
        with self._lock:
            touched = False
            for root in self._indexes:
                if is_inside(folder, root):
                    self._dirty.setdefault(root, set()).add(folder)
                    touched = True
            if not touched or self._closed:
                return
            if self._patch_timer is not None:
                self._patch_timer.cancel()
            self._patch_timer = threading.Timer(PATCH_DELAY, self._apply_patches)
            self._patch_timer.daemon = True
            self._patch_timer.start()

    def _apply_patches(self) -> None:
        with self._lock:
            work, self._dirty = self._dirty, {}
        for root, folders in work.items():
            idx = self._indexes.get(root)
            if idx is None:
                continue
            try:
                fs = self._vfs.resolve(root)
                lister = None if fs.is_local else _vfs_lister(fs)
                new = idx.patched(folders, skip=self.skip_for(root), lister=lister, now=idx.built_at)
            except Exception:  # noqa: BLE001 - a failed patch just means a slightly stale index
                log.exception("Patching the search index failed")
                continue
            with self._lock:
                if self._indexes.get(root) is idx:      # not replaced meanwhile by a full rebuild
                    self._indexes[root] = new
            self._save_soon(new)
            self._notify(root)

    # -- on disk ---------------------------------------------------------------------------------
    def _file_for(self, root: str) -> str:
        return os.path.join(self._cache_dir, hashlib.sha1(root.encode("utf-8")).hexdigest()[:16] + ".idx")

    def _save_soon(self, idx: FileIndex, force: bool = False) -> None:
        if not self._vfs.resolve(idx.root).is_local:
            return                                      # remote indexes are kept for this session only
        now = time.time()
        if not force and now - self._last_save.get(idx.root, 0) < SAVE_EVERY:
            return
        self._last_save[idx.root] = now
        threading.Thread(target=self._save, args=(idx,), daemon=True, name="px-index-save").start()

    def _save(self, idx: FileIndex) -> None:
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            path = self._file_for(idx.root)
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                pickle.dump(idx.to_state(), fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)                   # file names can be private
            except OSError:
                pass
        except OSError:
            log.warning("Could not save the search index for %s", idx.root, exc_info=True)

    def load_saved(self) -> int:
        """Load the indexes saved last time (blocking; call from a worker thread)."""
        try:
            return self._load_saved()
        finally:
            self.loaded.set()

    def _load_saved(self) -> int:
        loaded = 0
        try:
            names = [n for n in os.listdir(self._cache_dir) if n.endswith(".idx")]
        except OSError:
            return 0
        for name in names:
            path = os.path.join(self._cache_dir, name)
            try:
                with open(path, "rb") as fh:
                    idx = FileIndex.from_state(pickle.load(fh))
            except Exception:  # noqa: BLE001 - old format / damaged file: just rebuild later
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            with self._lock:
                current = self._indexes.get(idx.root)
                if current is None or current.built_at < idx.built_at:
                    self._indexes[idx.root] = idx
                    loaded += 1
        return loaded

    def close(self) -> None:
        self._closed = True
        with self._lock:
            if self._patch_timer is not None:
                self._patch_timer.cancel()


def _vfs_lister(fs) -> Callable[[str], list]:
    """FileIndex.patched wants [(name, flags, size, mtime)]; read it through a VFS."""
    from .file_index import DIR, HIDDEN, LINK

    def lister(folder: str):
        out = []
        for e in fs.list_dir(folder):
            f = (DIR if e.is_dir else 0) | (LINK if e.is_link else 0) | (HIDDEN if e.hidden or e.name.startswith(".") else 0)
            out.append((e.name, f, 0 if e.is_dir else int(e.size or 0), float(e.mtime or 0)))
        return out
    return lister


def default_cache_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PyExplorer", "index")
    if IS_MAC:
        return os.path.expanduser("~/Library/Caches/PyExplorer/index")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "pyexplorer", "index")


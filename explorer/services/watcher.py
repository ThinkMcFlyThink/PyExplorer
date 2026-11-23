"""
FolderWatcher - notices when a folder you're looking at changes on disk
(a download finishes, another program creates a file...) and asks the tab
to refresh.

It simply checks each open folder's "modified time" every ~1.5 seconds in a
background thread. A folder's mtime changes whenever something inside it is
added, removed or renamed. This is portable (no OS-specific APIs), very
cheap (one stat per open tab) and never blocks the UI. Network locations
are not polled.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Iterable

from ..core.tasks import TaskRunner


class FolderWatcher:
    def __init__(self, vfs, runner: TaskRunner, schedule: Callable[[int, Callable], object],
                 get_paths: Callable[[], Iterable[str]], on_changed: Callable[[str], None],
                 interval_ms: int = 1500) -> None:
        self._vfs = vfs
        self._runner = runner
        self._schedule = schedule
        self._get_paths = get_paths
        self._on_changed = on_changed
        self.interval_ms = interval_ms
        self.enabled = True
        self._mtimes: Dict[str, float] = {}
        self._busy = False

    def start(self) -> None:
        self._stopped = False
        self._schedule(self.interval_ms, self._tick)

    def stop(self) -> None:
        self._stopped = True

    def forget(self, path: str) -> None:
        """Call after the app itself refreshed a folder, to avoid a double refresh."""
        try:
            self._mtimes[path] = os.stat(path).st_mtime
        except OSError:
            self._mtimes.pop(path, None)

    def _tick(self) -> None:
        if getattr(self, "_stopped", False):
            return
        if self.enabled and not self._busy:
            paths = []
            for p in set(self._get_paths()):
                try:
                    if self._vfs.resolve(p).can_watch:
                        paths.append(p)
                except OSError:
                    pass
            if paths:
                self._busy = True
                try:
                    self._runner.submit(self._stat_all, paths, pool="misc",
                                        on_done=self._compare, on_error=self._failed)
                except RuntimeError:          # the app is shutting down
                    return
        self._schedule(self.interval_ms, self._tick)

    @staticmethod
    def _stat_all(paths):
        result = {}
        for p in paths:
            try:
                result[p] = os.stat(p).st_mtime
            except OSError:
                result[p] = -1.0
        return result

    def _compare(self, mtimes: Dict[str, float]) -> None:
        self._busy = False
        for path, mtime in mtimes.items():
            old = self._mtimes.get(path)
            self._mtimes[path] = mtime
            if old is not None and old != mtime:
                self._on_changed(path)
        for gone in set(self._mtimes) - set(mtimes):     # forget folders no longer open
            del self._mtimes[gone]

    def _failed(self, exc: BaseException) -> None:
        self._busy = False

"""
DirectoryScanner - lists a folder in the background and STREAMS the results.

Instead of waiting for the whole folder (which could be 100k files on a slow
network share), the worker sends batches to the UI as soon as it has either
``batch_size`` entries or ``flush_interval`` seconds have passed. The window
stays responsive and the first files show up almost immediately.

Every scan gets a CancelToken. Navigating away cancels the old scan, and any
batches still in flight are silently dropped.
"""
from __future__ import annotations

import time
from typing import Callable, List

from ..core.fileinfo import FileEntry
from ..core.tasks import CancelToken, TaskRunner


def stream_listing(fs, path: str, token: CancelToken, emit: Callable[[List[FileEntry]], None],
                   batch_size: int = 500, flush_interval: float = 0.05) -> int:
    """Runs in a worker thread. Calls ``emit(batch)`` repeatedly. Returns the count."""
    batch: List[FileEntry] = []
    total = 0
    last = time.monotonic()
    for entry in fs.list_dir(path):
        if token.cancelled:
            return total
        batch.append(entry)
        now = time.monotonic()
        if len(batch) >= batch_size or now - last >= flush_interval:
            emit(batch)
            total += len(batch)
            batch = []
            last = now
            batch_size = min(batch_size * 2, 8000)   # grow batches: fewer UI updates for huge folders
    if batch and not token.cancelled:
        emit(batch)
        total += len(batch)
    return total


class DirectoryScanner:
    def __init__(self, vfs, runner: TaskRunner) -> None:
        self._vfs = vfs
        self._runner = runner

    def scan(self, path: str, on_batch: Callable[[List[FileEntry]], None],
             on_done: Callable[[int], None], on_error: Callable[[BaseException], None]) -> CancelToken:
        """Start listing ``path``. All callbacks run on the UI thread."""
        token = CancelToken()
        post = self._runner.dispatcher.post

        def deliver(batch: List[FileEntry]) -> None:      # UI thread
            if not token.cancelled:
                on_batch(batch)

        def work() -> int:                                 # worker thread
            fs = self._vfs.resolve(path)
            return stream_listing(fs, path, token, lambda b: post(deliver, b))

        self._runner.submit(work, pool="io", token=token, on_done=on_done, on_error=on_error)
        return token

    def list_all(self, path: str, on_done: Callable[[List[FileEntry]], None],
                 on_error: Callable[[BaseException], None]) -> CancelToken:
        """Non-streaming variant (used for tree-view children, breadcrumb menus)."""
        token = CancelToken()
        self._runner.submit(lambda: list(self._vfs.resolve(path).list_dir(path)), pool="io",
                            token=token, on_done=on_done, on_error=on_error)
        return token

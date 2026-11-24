"""
Background work: threads, processes, cancellation, and getting results back
to the UI thread safely.

THE ONE RULE OF GUI TOOLKITS + THREADS
--------------------------------------
Qt widgets are NOT thread-safe. Only the main (UI) thread may touch them.
So the pattern everywhere in PyExplorer is:

    1. The UI asks ``TaskRunner.submit(work, on_done=...)``.
    2. ``work`` runs in a worker thread (or process) - it may take seconds.
    3. Its result is put on a queue, and the ``UIDispatcher`` (the Qt version
       lives in ui/qt_bridge.py) calls ``on_done`` on the main thread, where
       touching widgets is safe.

Long jobs can also *stream* partial results with ``dispatcher.post(fn, ...)``
which is how folder listings and search results appear progressively.

Threads vs processes
--------------------
Listing folders, reading files and talking to servers is I/O: Python
releases the GIL while waiting on the disk/network, so threads run truly in
parallel for this work and need no copying of results between processes.
CPU-heavy work (e.g. checksums of huge files) can go to the "process" pool.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from .events import Topics

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised inside a task when its CancelToken was cancelled."""


class CancelToken:
    """A flag shared between the UI and a worker. The UI calls ``cancel()``;
    the worker checks ``cancelled`` (or calls ``check()``) regularly and stops."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


class UIDispatcher:
    """Runs callables on the UI thread.

    ``schedule(ms, fn)`` runs ``fn`` on the UI thread after ``ms`` milliseconds.
    Keeping it as a parameter means this class imports no GUI toolkit, so it
    can be tested without a display. ui/qt_bridge.QtDispatcher is the version
    the app uses: it wakes up instantly via a Qt signal instead of polling.
    """

    def __init__(self, schedule: Callable[[int, Callable], Any],
                 interval_ms: int = 12, budget_ms: float = 14.0) -> None:
        self._schedule = schedule
        self._queue: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self._interval = interval_ms
        self._budget = budget_ms / 1000.0
        self._main_thread = threading.current_thread()
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._schedule(self._interval, self._pump)

    def stop(self) -> None:
        self._running = False

    def is_ui_thread(self) -> bool:
        return threading.current_thread() is self._main_thread

    def post(self, fn: Callable, *args, **kwargs) -> None:
        """Thread-safe: ask the UI thread to call ``fn(*args, **kwargs)`` soon."""
        self._queue.put((fn, args, kwargs))

    def call_and_wait(self, fn: Callable, *args, timeout: Optional[float] = None, **kwargs) -> Any:
        """From a worker thread: run ``fn`` on the UI thread and wait for its
        result. Used for things like "file exists - overwrite?" prompts."""
        if self.is_ui_thread():
            return fn(*args, **kwargs)
        done = threading.Event()
        box: Dict[str, Any] = {}

        def runner() -> None:
            try:
                box["result"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                done.set()

        self.post(runner)
        if not done.wait(timeout):
            raise TimeoutError("UI did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def pump_once(self) -> int:
        """Process queued callbacks for at most ``budget_ms``. Returns count.
        The time budget keeps the window responsive even when thousands of
        results arrive at once - leftovers simply run on the next tick."""
        deadline = time.perf_counter() + self._budget
        count = 0
        while True:
            try:
                fn, args, kwargs = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                log.exception("UI callback failed")
            count += 1
            if time.perf_counter() >= deadline:
                break
        return count

    def _pump(self) -> None:
        if not self._running:
            return
        # Schedule the next tick first: if a callback opens a modal dialog (which
        # runs a nested event loop), other results keep flowing meanwhile.
        self._schedule(self._interval, self._pump)
        self.pump_once()


class TaskRunner:
    """Named pools of workers.

    Separate pools stop one kind of work starving another: a huge search
    ("search" pool) never delays opening a folder ("io" pool), and a 50 GB
    copy ("ops") never delays either.
    """

    POOL_SIZES = {"io": 8, "search": 4, "ops": 3, "thumb": 3, "misc": 4, "transfer": 4,
                  "usage": 3}     # drive fill bars: kept apart, a stuck network mount may block one

    def __init__(self, dispatcher: UIDispatcher) -> None:
        self.dispatcher = dispatcher
        self._pools: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._closed = False

    def _pool(self, name: str):
        with self._lock:
            pool = self._pools.get(name)
            if pool is None:
                if name == "process":
                    pool = ProcessPoolExecutor(max_workers=2)
                else:
                    pool = ThreadPoolExecutor(max_workers=self.POOL_SIZES.get(name, 4),
                                              thread_name_prefix=f"px-{name}")
                self._pools[name] = pool
            return pool

    def submit(self, fn: Callable, *args, pool: str = "misc",
               on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[BaseException], None]] = None,
               token: Optional[CancelToken] = None, **kwargs) -> Future:
        """Run ``fn(*args, **kwargs)`` in a pool. ``on_done(result)`` /
        ``on_error(exc)`` are called on the UI thread - unless ``token`` was
        cancelled in the meantime, in which case nobody is told (the result
        is stale and nobody wants it)."""
        if self._closed:
            raise RuntimeError("TaskRunner is shut down")
        future = self._pool(pool).submit(fn, *args, **kwargs)

        def finished(fut: Future) -> None:
            if token is not None and token.cancelled:
                return
            try:
                result = fut.result()
            except Cancelled:
                return
            except BaseException as exc:  # noqa: BLE001
                if on_error:
                    self.dispatcher.post(on_error, exc)
                else:
                    log.error("Background task failed: %r", exc)
                return
            if on_done:
                self.dispatcher.post(on_done, result)

        future.add_done_callback(finished)
        return future

    def shutdown(self) -> None:
        self._closed = True
        for pool in list(self._pools.values()):
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:           # Python < 3.9 has no cancel_futures
                pool.shutdown(wait=False)


class Job:
    """A long-running, user-visible operation (copy, delete, search...).
    Shown in the status bar with a progress bar and a cancel button."""

    _next_id = 1

    def __init__(self, title: str) -> None:
        self.id = Job._next_id
        Job._next_id += 1
        self.title = title
        self.detail = ""
        self.progress: Optional[float] = None    # 0..1, or None = "busy"
        self.token = CancelToken()
        self.finished = False
        self.error: Optional[str] = None


class JobManager:
    """Keeps the list of running jobs and announces changes on the bus.
    Workers call ``update`` from any thread; the event is published on the
    UI thread via the dispatcher."""

    def __init__(self, dispatcher: UIDispatcher, bus) -> None:
        self._dispatcher = dispatcher
        self._bus = bus
        self.jobs: Dict[int, Job] = {}
        self._last_emit: Dict[int, float] = {}

    def start(self, title: str) -> Job:
        job = Job(title)
        self.jobs[job.id] = job
        self._emit(job, force=True)
        return job

    def update(self, job: Job, progress: Optional[float] = None, detail: Optional[str] = None) -> None:
        if progress is not None:
            job.progress = max(0.0, min(1.0, progress))
        if detail is not None:
            job.detail = detail
        self._emit(job)

    def finish(self, job: Job, error: Optional[str] = None) -> None:
        job.finished = True
        job.error = error
        self._emit(job, force=True)

    def _emit(self, job: Job, force: bool = False) -> None:
        # Throttle to ~15 updates/second per job - progress bars don't need more.
        now = time.monotonic()
        if not force and now - self._last_emit.get(job.id, 0) < 0.066:
            return
        self._last_emit[job.id] = now

        def publish() -> None:
            if job.finished:
                self.jobs.pop(job.id, None)
                self._last_emit.pop(job.id, None)
            self._bus.publish(Topics.JOBS_CHANGED, job=job)
        self._dispatcher.post(publish)

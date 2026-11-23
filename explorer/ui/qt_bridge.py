"""
Glue between the plain-Python core and Qt's event loop.

QtDispatcher
    The Qt version of ``core.tasks.UIDispatcher``. Worker threads call
    ``post(fn, ...)``; we wake the UI thread with a queued Qt signal and run
    the callbacks there (Qt widgets may only be touched from the UI thread).
    Callbacks run in short time slices (``budget_ms``), so a flood of results
    - say 100,000 search hits - never makes the window stutter.

qt_schedule(ms, fn)
    A drop-in for the "schedule(ms, fn)" function the core expects.
    Used by the folder watcher.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..core.tasks import UIDispatcher


def qt_schedule(ms: int, fn) -> None:
    QTimer.singleShot(int(ms), fn)


class _Waker(QObject):
    wake = Signal()


class QtDispatcher(UIDispatcher):
    def __init__(self, budget_ms: float = 14.0) -> None:
        super().__init__(qt_schedule, budget_ms=budget_ms)
        self._armed = False                 # a wake-up is already on its way
        self._lock = threading.Lock()
        self._waker = _Waker()
        self._waker.wake.connect(self._on_wake, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._running = True
        self._arm()                         # run anything posted before start()

    def post(self, fn, *args, **kwargs) -> None:
        self._queue.put((fn, args, kwargs))
        self._arm()

    def _arm(self) -> None:
        with self._lock:
            if self._armed:
                return
            self._armed = True
        self._waker.wake.emit()             # thread-safe: delivered on the UI thread

    def _on_wake(self) -> None:
        with self._lock:
            self._armed = False
        if not self._running:
            return
        self.pump_once()
        if not self._queue.empty():         # time slice used up: continue on the next loop turn
            with self._lock:
                if self._armed:
                    return
                self._armed = True
            QTimer.singleShot(0, self._on_wake)

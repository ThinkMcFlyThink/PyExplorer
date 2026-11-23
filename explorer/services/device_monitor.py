"""
DeviceMonitor - notices drives, network mounts and cloud storage coming and
going, while the app runs.

    monitor = DeviceMonitor(dispatcher, on_change)   # takes the first snapshot now
    monitor.start()                                  # background thread
    monitor.devices                                  # the latest snapshot (list of Device)
    ...
    monitor.stop()

How it notices changes:
  * Linux: the kernel marks /proc/self/mounts as "changed" the moment anything
    is mounted or unmounted (USB stick, ``rclone mount``, ``mount -t cifs``,
    Nemo opening a share...). We sleep in poll() on that file, so a new mount
    shows up within a fraction of a second, and an idle app does no work.
    GVFS shares, phones and sync apps don't touch the mount list, so we also
    look again every couple of seconds (it takes about a millisecond).
  * Windows / macOS: a quick look every couple of seconds (drive letters,
    /Volumes, cloud sync folders).

When the snapshot differs from the last one, ``on_change(old, new)`` runs on
the UI thread (through the dispatcher). Nothing here imports Qt.
"""
from __future__ import annotations

import logging
import os
import select
import threading
import time
from typing import Callable, List, Optional

from ..core.platform_info import IS_LINUX
from .devices import Device, scan_devices, snapshot_key

log = logging.getLogger(__name__)

MOUNTS_FILE = "/proc/self/mounts"


class DeviceMonitor:
    def __init__(self, dispatcher, on_change: Callable[[List[Device], List[Device]], None],
                 scan: Callable[[], List[Device]] = scan_devices, interval: float = 2.0,
                 mounts_file: Optional[str] = MOUNTS_FILE) -> None:
        self._dispatcher = dispatcher
        self._on_change = on_change
        self._scan = scan
        self._interval = interval
        self._mounts_file = mounts_file if IS_LINUX else None
        self.devices: List[Device] = self._safe_scan() or []   # first snapshot: needed to build the sidebar
        self._key = snapshot_key(self.devices)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._wake_r, self._wake_w = os.pipe() if hasattr(os, "pipe") else (None, None)

    # -- control -----------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="device-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.refresh_now()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        for fd in (self._wake_r, self._wake_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._wake_r = self._wake_w = None

    def refresh_now(self) -> None:
        """Look again right away (e.g. after we mounted something ourselves)."""
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass

    # -- the background thread -----------------------------------------------
    def _run(self) -> None:
        poller = select.poll() if hasattr(select, "poll") else None
        mounts = None
        if poller is not None:
            if self._mounts_file:
                try:
                    mounts = open(self._mounts_file, "rb")      # noqa: SIM115 - kept open while we watch
                    poller.register(mounts, select.POLLPRI | select.POLLERR)
                except OSError:
                    mounts = None
            if self._wake_r is not None:
                poller.register(self._wake_r, select.POLLIN)
        try:
            while not self._stop.is_set():
                if poller is not None:
                    events = poller.poll(int(self._interval * 1000))
                    if any(fd == self._wake_r for fd, _ in events):
                        os.read(self._wake_r, 4096)              # empty the wake-up pipe
                    if mounts is not None and any(fd == mounts.fileno() for fd, _ in events):
                        time.sleep(0.15)        # mounts often come in a burst: let it settle
                else:
                    self._stop.wait(self._interval)
                if self._stop.is_set():
                    break
                self._check()
        finally:
            if mounts is not None:
                mounts.close()

    def _safe_scan(self) -> Optional[List[Device]]:
        try:
            return self._scan()
        except Exception:  # noqa: BLE001 - a bad config file must never kill the monitor
            log.exception("Scanning drives failed")
            return None

    def _check(self) -> None:
        new = self._safe_scan()
        if new is None:
            return
        key = snapshot_key(new)
        if key == self._key:
            return
        old, self.devices, self._key = self.devices, new, key
        self._dispatcher.post(self._on_change, old, new)


def describe_changes(old: List[Device], new: List[Device]) -> Optional[str]:
    """A short status-bar message: "Connected: OneDrive (rclone)" / "Removed: USB stick"."""
    before = {d.path: d for d in old if d.mounted}
    after = {d.path: d for d in new if d.mounted}
    added = [d for p, d in after.items() if p not in before]
    removed = [d for p, d in before.items() if p not in after]
    parts = []
    if added:
        parts.append("Connected: " + ", ".join(_name(d) for d in added[:3]) + ("…" if len(added) > 3 else ""))
    if removed:
        parts.append("Removed: " + ", ".join(_name(d) for d in removed[:3]) + ("…" if len(removed) > 3 else ""))
    return "   ".join(parts) or None


def _name(device: Device) -> str:
    if device.provider and device.provider.lower() not in device.label.lower():
        return f"{device.label} ({device.provider})"
    return device.label

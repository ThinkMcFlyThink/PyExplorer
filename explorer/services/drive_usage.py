"""
DriveUsage - how full each drive is (the bars behind drive names in the
sidebar and the Computer view).

    usage = ctx.drive_usage.get(path)      # cached Usage or None; never blocks
    usage.fraction  -> 0.0 .. 1.0          usage.text -> "76.3 GB free of 219 GB"

``get`` only reads the cache; when the value is missing or old it starts a
background measurement, and ``on_changed(path)`` (UI thread) says when a new
value arrived, so the caller just repaints.

Where the numbers come from
---------------------------
* drives and mounted network shares (SMB, NFS, SSHFS, GVFS...): the file
  system's own free/total (statvfs / GetDiskFreeSpaceEx) - refreshed every
  DISK_TTL seconds;
* rclone mounts: the same - rclone answers with the cloud account's quota.
  Backends without a quota make rclone report a fake 1 PiB disk; those are
  asked with ``rclone about`` instead;
* cloud SYNC folders (OneDrive / Dropbox / MEGA apps...): the disk numbers
  would be your local disk, which is misleading. If rclone has a remote for
  the same service, ``rclone about <remote>:`` gives the real account quota
  (refreshed every CLOUD_TTL seconds); otherwise no bar is shown.

Measuring can block (a stuck network mount, a slow cloud API), so it runs in
its own small pool and never twice at once for the same path.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..core.formatting import human_size
from . import rclone_config

log = logging.getLogger(__name__)

DISK_TTL = 20.0                 # seconds before a drive is measured again (when it is on screen)
CLOUD_TTL = 600.0               # rclone about = a call to the cloud service: rarely
FAILED_TTL = 120.0              # nothing to show: try again after this long
FAKE_TOTAL = 1 << 50            # rclone mount without quota support reports 1 PiB
RCLONE_TIMEOUT = 30


@dataclass(frozen=True)
class Usage:
    total: int
    used: int
    free: int
    source: str = "disk"        # "disk" | "rclone <remote>:"

    @property
    def fraction(self) -> float:
        return max(0.0, min(1.0, self.used / self.total)) if self.total else 0.0

    @property
    def text(self) -> str:
        return f"{human_size(self.free)} free of {human_size(self.total)}"

    @property
    def tooltip(self) -> str:
        where = "" if self.source == "disk" else f"\nCloud quota (from {self.source})"
        return f"{self.text} ({self.fraction * 100:.0f}% used){where}"


# ---------------------------------------------------------------------------
# Measuring (blocking - worker threads only)
# ---------------------------------------------------------------------------
def disk_usage(path: str) -> Optional[Usage]:
    try:
        u = shutil.disk_usage(path)
    except (OSError, ValueError):
        return None
    if u.total <= 0:
        return None
    return Usage(u.total, u.used, u.free)


def parse_about(text: str, remote: str) -> Optional[Usage]:
    """``rclone about --json`` output -> Usage (None if it has no total)."""
    try:
        data = json.loads(text)
        total = int(data.get("total") or 0)
        if total <= 0:
            return None
        used = int(data.get("used") or 0)
        free = int(data["free"]) if data.get("free") is not None else max(0, total - used)
        return Usage(total, used, free, f"rclone {remote}")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def rclone_about(remote: str, rclone: Optional[str] = None) -> Optional[Usage]:
    """Ask rclone for a remote's quota ("OneDrive:" -> total/used/free)."""
    exe = rclone or shutil.which("rclone")
    if not exe or not remote:
        return None
    if not remote.endswith(":"):
        remote = remote.split(":", 1)[0] + ":"
    try:
        result = subprocess.run([exe, "about", remote, "--json", "--ask-password=false"],
                                capture_output=True, text=True, timeout=RCLONE_TIMEOUT,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("rclone about %s failed: %s", remote, exc)
        return None
    if result.returncode != 0:
        log.info("rclone about %s failed: %s", remote, result.stderr.strip()[:200])
        return None
    return parse_about(result.stdout, remote)


def remote_for(provider: str, label: str, remotes: Dict[str, Dict[str, str]]) -> Optional[str]:
    """The rclone remote that is the same cloud service as a sync folder.
    One remote of that service: that one. Several (two OneDrive accounts):
    only one whose name appears in the folder's label, else None - better no
    bar than another account's numbers."""
    if not provider:
        return None
    matches = [name for name in remotes
               if rclone_config.describe_remote(name + ":", remotes).provider.lower() == provider.lower()]
    if len(matches) == 1:
        return matches[0] + ":"
    named = [m for m in matches if m.lower() in label.lower()]
    return named[0] + ":" if len(named) == 1 else None


def measure(path: str, device=None, remotes: Optional[Dict[str, Dict[str, str]]] = None,
            is_mount: Callable[[str], bool] = os.path.ismount,
            about: Callable[[str], Optional[Usage]] = rclone_about) -> Optional[Usage]:
    """How full the drive at ``path`` is. ``device`` (services.devices.Device) says
    what kind of place it is; without one, ``path`` is treated as a plain drive."""
    if "://" in path:                            # SFTP/FTP/SMB connections: not measured (yet)
        return None
    if device is not None and not device.mounted:
        return None                              # an fstab share that isn't mounted: don't mount it for this
    if device is None or device.section != "cloud":
        return disk_usage(path)
    source = getattr(device, "source", "")
    if source:                                   # rclone mount
        usage = disk_usage(path)
        if usage is not None and usage.total < FAKE_TOTAL:
            return usage
        return about(source)
    if "/gvfs/" in path or is_mount(path):       # a cloud drive mounted some other way (GVFS, onedriver)
        usage = disk_usage(path)
        return usage if usage is not None and usage.total < FAKE_TOTAL else None
    # A sync app's folder: the disk numbers would be the LOCAL disk. Use rclone if it knows the account.
    remotes = remotes if remotes is not None else rclone_config.read_remotes()
    remote = remote_for(device.provider, device.label, remotes)
    return about(remote) if remote else None


# ---------------------------------------------------------------------------
# The cache the UI reads
# ---------------------------------------------------------------------------
class DriveUsage:
    def __init__(self, runner, devices: Callable[[], list], on_changed: Callable[[str], None]) -> None:
        self._runner = runner
        self._devices = devices
        self._on_changed = on_changed
        self._cache: Dict[str, tuple] = {}         # path -> (Usage or None, measured at, ttl)
        self._busy: set = set()
        self.enabled = True

    def _device(self, path: str):
        for dev in self._devices() or ():
            if dev.path == path:
                return dev
        return None

    def get(self, path: str) -> Optional[Usage]:
        """Cached value (may be a bit old). Starts a refresh when needed. UI thread."""
        if not self.enabled or not path or "://" in path:
            return None
        hit = self._cache.get(path)
        if hit is None or time.monotonic() - hit[1] > hit[2]:
            self._start(path)
        return hit[0] if hit else None

    def _start(self, path: str) -> None:
        if path in self._busy:
            return
        self._busy.add(path)
        device = self._device(path)

        def work():
            return measure(path, device)

        def done(usage: Optional[Usage]) -> None:
            self._busy.discard(path)
            cloud = device is not None and device.section == "cloud" and usage is not None and usage.source != "disk"
            ttl = FAILED_TTL if usage is None else (CLOUD_TTL if cloud else DISK_TTL)
            old = self._cache.get(path)
            self._cache[path] = (usage, time.monotonic(), ttl)
            if old is None or old[0] != usage:
                self._on_changed(path)

        def failed(exc: BaseException) -> None:
            self._busy.discard(path)
            self._cache[path] = (None, time.monotonic(), FAILED_TTL)

        self._runner.submit(work, pool="usage", on_done=done, on_error=failed)

    def invalidate(self, paths: Optional[List[str]] = None) -> None:
        """Measure these (or all) drives again the next time they are shown - e.g. after
        a copy. Cloud quotas keep their slower schedule."""
        for path, (usage, at, ttl) in list(self._cache.items()):
            if paths is None or path in paths:
                if ttl != CLOUD_TTL:
                    self._cache[path] = (usage, 0.0, ttl)

"""
Automatic mounting of network shares listed in /etc/fstab (Linux).

When you open a folder that is the mount point of an SMB/NFS share from
/etc/fstab, and it isn't mounted yet, we run ``mount <folder>`` first - like
clicking it in Nemo. That only works without sudo when its fstab line has the
"user" (or "users") option; otherwise you get a message explaining how to
fix that, instead of a puzzling empty folder.

Called from LocalFileSystem.list_dir, which always runs in a background
thread, so a slow server never freezes the window.
"""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Dict, Optional, Tuple

from ..core.platform_info import IS_LINUX, fstab_network_entries, read_mounts

FSTAB = "/etc/fstab"
PROC_MOUNTS = "/proc/mounts"

_cache: Dict[str, dict] = {}
_cache_key: Optional[Tuple[float, int]] = None
_lock = threading.Lock()


def _entries() -> Dict[str, dict]:
    """fstab network entries by mount point, re-read only when fstab changes."""
    global _cache, _cache_key
    try:
        st = os.stat(FSTAB)
        key = (st.st_mtime, st.st_size)
    except OSError:
        return {}
    with _lock:
        if key != _cache_key:
            _cache = {e["mountpoint"]: e for e in fstab_network_entries(FSTAB, PROC_MOUNTS)}
            _cache_key = key
        return _cache


def ensure_mounted(path: str) -> bool:
    """Mount ``path`` if it is an unmounted fstab network share. True if we mounted it."""
    if not IS_LINUX:
        return False
    entry = _entries().get(path.rstrip("/") or "/")
    if entry is None:
        return False
    if any(mp == entry["mountpoint"] for mp, _ in read_mounts(PROC_MOUNTS)):
        return False
    if not entry["user_mountable"]:
        raise PermissionError(
            f"The network share {entry['spec']} isn't mounted.\n\n"
            f"Mount it with:  sudo mount {entry['mountpoint']}\n"
            f"or add the 'user' option to its line in /etc/fstab, so it can be mounted without sudo.")
    result = subprocess.run(["mount", entry["mountpoint"]], capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise OSError(f"Could not mount {entry['spec']}:\n{detail}")
    return True

"""
Read-only facts about the computer we're running on: which OS, where the
user's standard folders are, which drives are mounted. No side effects.
"""
from __future__ import annotations

import os
import re
import string
import sys
from pathlib import Path
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC


def home() -> str:
    return str(Path.home())


def user_dirs() -> List[Tuple[str, str]]:
    """[(label, path)] for Home, Desktop, Documents... that actually exist."""
    h = Path.home()
    found = {"Home": h}
    names = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]
    for name in names:
        found[name] = h / ("Movies" if (IS_MAC and name == "Videos") else name)

    if IS_LINUX:   # honour ~/.config/user-dirs.dirs (localised folder names)
        xdg = {"DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads",
               "PICTURES": "Pictures", "MUSIC": "Music", "VIDEOS": "Videos"}
        try:
            text = (h / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
            for key, value in re.findall(r'^XDG_(\w+)_DIR="(.*)"', text, re.M):
                if key in xdg:
                    found[xdg[key]] = Path(value.replace("$HOME", str(h)))
        except OSError:
            pass
    if IS_WINDOWS:  # OneDrive often redirects these
        onedrive = os.environ.get("OneDrive")
        for name in ("Desktop", "Documents", "Pictures"):
            if onedrive and not found[name].exists() and (Path(onedrive) / name).exists():
                found[name] = Path(onedrive) / name

    order = ["Home"] + names
    return [(label, str(found[label])) for label in order if found[label].is_dir()]


def os_drives() -> List[Tuple[str, str, str]]:
    """[(label, path, kind)] of the drive letters (Windows) or volumes (macOS).
    kind: drive | removable | network. On Linux only "/" - the full Linux picture
    (USB sticks, network mounts, rclone, phones...) is built by services/devices.py."""
    drives: List[Tuple[str, str, str]] = []
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            mask = kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if not mask & (1 << i):
                    continue
                root = f"{letter}:\\"
                dtype = kernel32.GetDriveTypeW(root)      # 2 removable, 3 fixed, 4 network, 5 cd
                kind = {2: "removable", 4: "network"}.get(dtype, "drive")
                label = f"{letter}:"
                if dtype == 3:   # only query names of local fixed disks (network ones can hang)
                    buf = ctypes.create_unicode_buffer(261)
                    if kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0) and buf.value:
                        label = f"{buf.value} ({letter}:)"
                drives.append((label, root, kind))
        except Exception:  # noqa: BLE001
            for letter in string.ascii_uppercase:
                if os.path.exists(f"{letter}:\\"):
                    drives.append((f"{letter}:", f"{letter}:\\", "drive"))
        return drives

    drives.append(("Root (/)", "/", "drive"))
    if IS_MAC:
        try:
            for entry in sorted(os.scandir("/Volumes"), key=lambda e: e.name):
                if entry.is_dir() and os.path.realpath(entry.path) != "/":
                    drives.append((entry.name, entry.path, "drive"))
        except OSError:
            pass
        return drives

    return drives


# ---------------------------------------------------------------------------
# Linux mounts (all functions take file paths as arguments so tests can use fake files)
# ---------------------------------------------------------------------------
SHARE_FS_TYPES = {"nfs", "nfs4", "cifs", "smb3", "smbfs"}       # what we look for in /etc/fstab


def _unescape(field: str) -> str:
    """/proc/mounts and fstab write spaces as \\040, tabs as \\011 and so on."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def read_mounts(proc_mounts: str = "/proc/mounts") -> List[Tuple[str, str]]:
    """[(mount point, filesystem type)] of everything mounted right now."""
    out = []
    try:
        with open(proc_mounts, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((_unescape(parts[1]), parts[2]))
    except OSError:
        pass
    return out


def read_mount_specs(proc_mounts: str = "/proc/mounts") -> List[Tuple[str, str, str]]:
    """[(source, mount point, type)] e.g. ("//truenas.local/media", "/mnt/media", "cifs")."""
    out = []
    try:
        with open(proc_mounts, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    except OSError:
        pass
    return out


def gvfs_root() -> Optional[str]:
    """The folder where GVFS (Nemo/Nautilus) makes network shares available to all apps."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime and hasattr(os, "getuid"):
        runtime = f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "gvfs") if runtime else None


def gvfs_mounts(root: Optional[str] = None) -> List[Tuple[str, str]]:
    """[(label, path)] for shares the desktop has mounted through GVFS, e.g.
    /run/user/1000/gvfs/smb-share:server=truenas.local,share=media  ->  "media on truenas.local" """
    root = root or gvfs_root()
    if root is None:
        return []
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        kind, _, params = name.partition(":")
        info = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        server = info.get("server") or info.get("host") or kind
        share = info.get("share")
        if share:
            label = f"{share} on {server}"
        else:
            label = f"{server} ({kind.upper()})" if kind else name
        out.append((label, os.path.join(root, name)))
    return out


def fstab_network_entries(fstab: str = "/etc/fstab", proc_mounts: str = "/proc/mounts") -> List[dict]:
    """SMB/NFS shares listed in /etc/fstab. Each: spec, mountpoint, fstype, options,
    mounted (bool) and user_mountable (bool: has the "user"/"users" option, so a
    normal user may mount it without sudo)."""
    mounted = {mp for mp, _ in read_mounts(proc_mounts)}
    out = []
    try:
        with open(fstab, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3 or parts[2] not in SHARE_FS_TYPES:
            continue
        options = parts[3].split(",") if len(parts) > 3 else []
        mountpoint = _unescape(parts[1]).rstrip("/") or "/"
        out.append({"spec": _unescape(parts[0]), "mountpoint": mountpoint, "fstype": parts[2],
                    "options": options, "mounted": mountpoint in mounted,
                    "user_mountable": any(o in ("user", "users", "owner") for o in options)})
    return out


def default_categories() -> List[dict]:
    """The sidebar categories created on first run. The user can change everything.
    Cloud, Drives and Network are live sections: they show what is synced,
    plugged in or mounted right now (see core/categories.py)."""
    places = [{"label": label, "path": path, "kind": "folder"} for label, path in user_dirs()]
    return [
        {"name": "Places", "items": places},
        {"name": "Cloud", "auto": "cloud", "items": []},
        {"name": "Drives", "auto": "drives",
         "items": [{"label": "Computer", "path": "computer://", "kind": "computer"}]},
        {"name": "Network", "auto": "network", "items": []},
    ]

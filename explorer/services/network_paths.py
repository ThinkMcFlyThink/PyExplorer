"""
Real paths for network files - so ANY app can open them directly.

PyExplorer talks to servers itself (vfs/smb.py, sftp.py...), which other apps
can't use. But often the same file is also reachable as an ordinary path:

  Linux  * the share is already mounted by Nemo/Nautilus (GVFS):
             smb://truenas/media/a.mkv -> /run/user/1000/gvfs/smb-share:server=truenas,share=media/a.mkv
         * it's mounted through /etc/fstab (cifs):  -> /mnt/media/a.mkv
         * or we mount it the way Nemo does, with ``gio mount`` (the mount stays
           after PyExplorer closes, exactly like mounting it in Nemo)
  Windows  the share's UNC path: \\\\truenas\\media\\a.mkv (Windows connects itself,
           using the username/password PyExplorer already has)

All functions can be slow (network, mounting) - call them from worker threads.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import List, Optional

from ..core.connections import split_uri
from ..core.platform_info import IS_LINUX, IS_WINDOWS, gvfs_root, read_mount_specs

CIFS_TYPES = {"cifs", "smb3", "smbfs"}


def _gvfs_params(name: str):
    kind, _, params = name.partition(":")
    return kind, {k.lower(): v for k, v in (p.split("=", 1) for p in params.split(",") if "=" in p)}


def gvfs_path_for(uri: str, root: Optional[str] = None) -> Optional[str]:
    """Where GVFS shows this smb://, sftp:// or ftp:// file, if that share/server is mounted."""
    root = root or gvfs_root()
    if not root:
        return None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    protocol, user, host, port, path = split_uri(uri)
    parts = [p for p in path.split("/") if p]
    for name in names:
        kind, info = _gvfs_params(name)
        server = (info.get("server") or info.get("host") or "").lower()
        if server != host.lower():
            continue
        if protocol == "smb" and kind == "smb-share" and parts and info.get("share", "").lower() == parts[0].lower():
            return os.path.join(root, name, *parts[1:])
        if protocol in ("sftp", "ftp", "ftps") and kind == protocol.replace("ftps", "ftp"):
            if "port" in info and int(info["port"]) != port:
                continue
            return os.path.join(root, name, *parts)
    return None


def cifs_mount_path_for(uri: str, mounts: Optional[List] = None) -> Optional[str]:
    """Where a kernel-mounted share (e.g. from /etc/fstab) shows this smb:// file."""
    protocol, user, host, port, path = split_uri(uri)
    parts = [p for p in path.split("/") if p]
    if protocol != "smb" or not parts:
        return None
    wanted = f"//{host}/{parts[0]}".lower()
    for source, mountpoint, fstype in (mounts if mounts is not None else read_mount_specs()):
        if fstype in CIFS_TYPES and source.rstrip("/").lower() == wanted:
            return os.path.join(mountpoint, *parts[1:])
    return None


def unc_path_for(uri: str) -> Optional[str]:
    """smb://truenas/media/a.mkv -> \\\\truenas\\media\\a.mkv (Windows only makes sense)."""
    protocol, user, host, port, path = split_uri(uri)
    parts = [p for p in path.split("/") if p]
    if protocol != "smb" or not parts or port not in (0, 445):
        return None
    return "\\\\" + host + "\\" + "\\".join(parts)


# ---------------------------------------------------------------------------
# Making a path available
# ---------------------------------------------------------------------------
def gvfs_available(root: Optional[str] = None) -> bool:
    """Is the desktop's GVFS running with its FUSE folder (so mounts get real paths)?"""
    root = root or gvfs_root()
    return bool(IS_LINUX and root and shutil.which("gio") and os.path.ismount(root))


def gio_mount_smb(uri: str, user: str, password: str, run=subprocess.run) -> bool:
    """Mount the share containing ``uri`` like Nemo does. The username/password are
    typed into gio's prompts through a pipe (never put on the command line)."""
    protocol, _user, host, port, path = split_uri(uri)
    parts = [p for p in path.split("/") if p]
    if protocol != "smb" or not parts:
        return False
    target = f"smb://{host}{'' if port in (0, 445) else f':{port}'}/{parts[0]}"
    domain = ""
    if "\\" in user:
        domain, user = user.split("\\", 1)
    answers = f"{user}\n{domain}\n{password}\n"         # gio asks: User / Domain / Password
    result = run(["gio", "mount", target], input=answers, capture_output=True, text=True, timeout=60)
    return result.returncode == 0 or "already mounted" in (result.stderr or "").lower()


def windows_connect(uri: str, user: str, password: str) -> bool:
    """Let Windows log in to the share (like 'net use', without showing the password)."""
    if not IS_WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes
    unc = unc_path_for(uri)
    if not unc:
        return False
    share = "\\".join(unc.split("\\")[:4])                 # \\host\share

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwDisplayType", wintypes.DWORD),
                    ("dwUsage", wintypes.DWORD), ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                    ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]

    resource = NETRESOURCEW()
    resource.dwType = 1                                    # RESOURCETYPE_DISK
    resource.lpRemoteName = share
    result = ctypes.windll.mpr.WNetAddConnection2W(ctypes.byref(resource), password or None, user or None, 0)
    return result in (0, 1219)                             # 1219: already connected with other credentials


def local_path_for(uri: str, user: str = "", password: str = "", allow_mount: bool = True) -> Optional[str]:
    """A real path for a network file, mounting the share if needed and allowed.
    None if there's no way (then the caller streams or downloads instead)."""
    if IS_WINDOWS:
        unc = unc_path_for(uri)
        if unc is None:
            return None
        share = "\\".join(unc.split("\\")[:4])
        if os.path.isdir(share) or (allow_mount and windows_connect(uri, user, password) and os.path.isdir(share)):
            return unc
        return None
    if not IS_LINUX:
        return None
    found = gvfs_path_for(uri) or cifs_mount_path_for(uri)
    if found or not allow_mount or not gvfs_available():
        return found
    if split_uri(uri)[0] == "smb" and gio_mount_smb(uri, user, password):
        for _ in range(20):                                 # the FUSE folder appears a moment later
            found = gvfs_path_for(uri)
            if found and os.path.exists(found):
                return found
            time.sleep(0.1)
    return None

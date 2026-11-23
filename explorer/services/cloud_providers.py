"""
CloudProviders - which folders on this computer belong to which cloud app
(OneDrive, Dropbox, Google Drive, iCloud...), and the "Always keep on this
device" / "Free up space" actions.

Detection (runs once at start-up, takes milliseconds):
  * Windows: every sync app registers its folders with Windows under
    HKLM\\...\\Explorer\\SyncRootManager - that one list covers OneDrive
    (personal + work/school), Dropbox, Google Drive, iCloud and others.
    The OneDrive environment variables are used as a fallback.
  * macOS: sync apps put their folders in ~/Library/CloudStorage
    (plus iCloud Drive in ~/Library/Mobile Documents).
  * Linux: the popular sync apps' own config files tell us their folders:
    the "onedrive" client (abraunegg), Dropbox, Nextcloud/ownCloud, Insync,
    MEGA. Mounted cloud storage (rclone, onedriver, pCloud, GNOME Online
    Accounts) is found from the mount list instead - see services/devices.py.
    Linux sync apps can't report per-file states, so no badges there.

Each folder becomes a CloudRoot with the path of its app, so the UI can show
that app's real icon (taken from the installed program, not drawn by us).

Pin/unpin (Windows only) sets the "pinned"/"unpinned" file attributes - the
same thing ``attrib +P`` / ``attrib +U`` do. The sync app sees the change and
downloads or frees the files in the background.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from ..core.platform_info import IS_MAC, IS_WINDOWS

# Registry key names -> friendly names
_FRIENDLY = {"onedrive": "OneDrive", "dropbox": "Dropbox", "googledrive": "Google Drive",
             "google drive": "Google Drive", "icloud": "iCloud Drive", "icloudrive": "iCloud Drive",
             "box": "Box", "mega": "MEGA", "pcloud": "pCloud", "nextcloud": "Nextcloud"}


@dataclass
class CloudRoot:
    provider: str                     # "OneDrive", "Dropbox"...
    label: str                        # shown in the sidebar: "OneDrive - Contoso"
    path: str                         # the synced folder
    app_path: Optional[str] = None    # the sync program (for its icon)
    can_pin: bool = False             # supports "always keep" / "free up space"
    reports_local: bool = True        # can tell downloaded files apart (else only "cloud only")
    icon_names: Tuple[str, ...] = ()  # icon-theme names to try for the app's icon (Linux)
    running: Optional[bool] = None    # is the sync app running? (None = can't tell / not relevant)
    detail: str = ""                  # extra tooltip text, e.g. "rclone mount of onedrive:"
    norm: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.norm = _norm(self.path)


def _norm(path: str) -> str:
    path = os.path.normpath(path)
    return os.path.normcase(path).rstrip("\\/")


def _friendly(raw: str) -> str:
    key = raw.lower()
    for k, v in _FRIENDLY.items():
        if key.startswith(k):
            return v
    return raw


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _expand(text: str) -> str:
    return os.path.expandvars(text.strip().strip('"'))


def _onedrive_exe() -> Optional[str]:
    for candidate in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "OneDrive", "OneDrive.exe"),
                      os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft OneDrive", "OneDrive.exe"),
                      os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft OneDrive", "OneDrive.exe")):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def detect_windows() -> List[CloudRoot]:
    roots: List[CloudRoot] = []
    try:
        import winreg
    except ImportError:
        return roots
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
    try:
        manager = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        manager = None
    if manager is not None:
        i = 0
        while True:
            try:
                key_name = winreg.EnumKey(manager, i)
            except OSError:
                break
            i += 1
            try:
                key = winreg.OpenKey(manager, key_name)
            except OSError:
                continue
            provider = _friendly(key_name.split("!")[0])
            app_path = None
            try:
                icon_resource = winreg.QueryValueEx(key, "IconResource")[0]     # "C:\...\OneDrive.exe,-588"
                app_path = _expand(icon_resource.rsplit(",", 1)[0])
            except OSError:
                pass
            if provider == "OneDrive" and not (app_path and app_path.lower().endswith(".exe")):
                app_path = _onedrive_exe()
            try:
                user_roots = winreg.OpenKey(key, "UserSyncRoots")
            except OSError:
                continue
            j = 0
            while True:
                try:
                    _sid, folder, _type = winreg.EnumValue(user_roots, j)
                except OSError:
                    break
                j += 1
                folder = _expand(str(folder))
                if os.path.isdir(folder):
                    label = os.path.basename(folder.rstrip("\\/")) or provider
                    roots.append(CloudRoot(provider, label, folder, app_path, can_pin=True))
    # Fallback / extra: OneDrive's own environment variables.
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        folder = os.environ.get(var)
        if folder and os.path.isdir(folder):
            roots.append(CloudRoot("OneDrive", os.path.basename(folder.rstrip("\\/")), folder,
                                   _onedrive_exe(), can_pin=True))
    return roots


def detect_mac(home: Optional[str] = None) -> List[CloudRoot]:
    home = home or os.path.expanduser("~")
    roots: List[CloudRoot] = []
    storage = os.path.join(home, "Library", "CloudStorage")
    apps = {"OneDrive": "OneDrive.app", "Dropbox": "Dropbox.app", "Google Drive": "Google Drive.app",
            "Box": "Box.app"}
    try:
        names = sorted(os.listdir(storage))
    except OSError:
        names = []
    for name in names:
        folder = os.path.join(storage, name)
        if not os.path.isdir(folder):
            continue
        provider = _friendly(name.split("-")[0])
        label = name.replace("-", " - ", 1) if "-" in name else name
        app = os.path.join("/Applications", apps.get(provider, provider + ".app"))
        # File Provider only tells us "dataless" (cloud only); it has no "pinned" attribute.
        roots.append(CloudRoot(provider, label, folder, app if os.path.exists(app) else None,
                               can_pin=False, reports_local=True))
    icloud = os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs")
    if os.path.isdir(icloud):
        roots.append(CloudRoot("iCloud Drive", "iCloud Drive", icloud, None, can_pin=False))
    return roots


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
# Icon-theme names that installed sync apps (or icon themes like Mint-Y/Papirus) provide.
PROVIDER_ICONS: Dict[str, Tuple[str, ...]] = {
    "OneDrive": ("onedrive", "com.microsoft.OneDrive", "onedrivegui", "folder-onedrive"),
    "Google Drive": ("google-drive", "insync", "folder-gdrive", "folder-google-drive"),
    "Google Photos": ("google-photos", "google-drive"),
    "Dropbox": ("dropbox", "folder-dropbox"),
    "Nextcloud": ("Nextcloud", "nextcloud", "com.nextcloud.desktopclient.nextcloud", "folder-nextcloud"),
    "ownCloud": ("owncloud", "ownCloud"),
    "MEGA": ("mega", "megasync", "mega-desktop-app", "folder-mega"),
    "pCloud": ("pcloud", "pCloud"),
    "Proton Drive": ("proton-drive", "protondrive"),
    "Box": ("box",),
    "Insync": ("insync",),
    "iCloud Drive": ("icloud",),
    "rclone": ("rclone", "rclone-browser"),
}

# Process names (as in /proc/<pid>/comm, max 15 characters) of each sync app.
SYNC_PROCESSES: Dict[str, Tuple[str, ...]] = {
    "OneDrive": ("onedrive",), "Dropbox": ("dropbox",), "Nextcloud": ("nextcloud",),
    "ownCloud": ("owncloud",), "Insync": ("insync",), "MEGA": ("megasync", "mega-desktop-ap"),
}


def icons_for(provider: str) -> Tuple[str, ...]:
    """Icon-theme names to try for a provider (rclone's own icon as a last try)."""
    return PROVIDER_ICONS.get(provider, ()) + PROVIDER_ICONS["rclone"]


_proc_cache: Tuple[float, Set[str]] = (0.0, set())


def running_process_names(proc: str = "/proc", max_age: float = 8.0) -> Set[str]:
    """Names of the programs running right now (Linux). Cached for a few seconds:
    it reads one tiny file per process, a few milliseconds in total."""
    global _proc_cache
    now = time.monotonic()
    if proc == "/proc" and now - _proc_cache[0] < max_age:
        return _proc_cache[1]
    names: Set[str] = set()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return names
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8", errors="replace") as fh:
                names.add(fh.read().strip().lower())
        except OSError:
            pass
    if proc == "/proc":
        _proc_cache = (now, names)
    return names


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _onedrive_client_roots(home: str, config_home: str) -> List[Tuple[str, str]]:
    """The open-source "onedrive" client (abraunegg). Each config folder
    (~/.config/onedrive, or ~/.config/onedrive-work etc. for extra accounts)
    has a "config" file whose sync_dir line names the synced folder."""
    out = []
    try:
        names = sorted(n for n in os.listdir(config_home) if n == "onedrive" or n.startswith("onedrive-"))
    except OSError:
        return out
    for name in names:
        conf_dir = os.path.join(config_home, name)
        if not os.path.isdir(conf_dir):
            continue
        text = _read(os.path.join(conf_dir, "config"))
        match = re.search(r'^\s*sync_dir\s*=\s*"?([^"\n]+)"?', text, re.M)
        folder = match.group(1).strip() if match else "~/OneDrive"
        folder = os.path.expanduser(folder.replace("~", home, 1) if folder.startswith("~") else folder)
        label = "OneDrive" if name == "onedrive" else f"OneDrive ({name[len('onedrive-'):]})"
        out.append((label, folder))
    return out


def _dropbox_roots(home: str) -> List[Tuple[str, str]]:
    """Dropbox writes its folder(s) to ~/.dropbox/info.json."""
    try:
        info = json.loads(_read(os.path.join(home, ".dropbox", "info.json")) or "{}")
    except ValueError:
        info = {}
    out = []
    for kind, data in info.items():
        if isinstance(data, dict) and data.get("path"):
            out.append(("Dropbox" if kind == "personal" else f"Dropbox ({kind.title()})", data["path"]))
    if not out and os.path.isdir(os.path.join(home, ".dropbox")):
        out.append(("Dropbox", os.path.join(home, "Dropbox")))
    return out


def _nextcloud_roots(config_home: str, app: str, cfg_name: str) -> List[Tuple[str, str]]:
    """Nextcloud/ownCloud list every synced folder as ...\\Folders\\N\\localPath=/path"""
    text = _read(os.path.join(config_home, app, cfg_name))
    folders = re.findall(r"\\Folders(?:WithPlaceholders)?\\\d+\\localPath=(.+)", text)
    out, seen = [], set()
    for folder in folders:
        folder = folder.strip().rstrip("/") or "/"
        if folder not in seen:
            seen.add(folder)
            out.append((app if len(folders) == 1 else f"{app} ({os.path.basename(folder)})", folder))
    return out


def detect_linux(home: Optional[str] = None, config_home: Optional[str] = None,
                 running: Optional[Iterable[str]] = None, skip: Iterable[str] = ()) -> List[CloudRoot]:
    """Sync apps' folders on Linux. ``skip``: paths we must not touch (mount
    points: a stuck network mount could make even "does it exist?" hang)."""
    home = home or os.path.expanduser("~")
    config_home = config_home or os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    running = set(running) if running is not None else running_process_names()
    skip = {os.path.normpath(p) for p in skip}
    found: List[Tuple[str, str, str]] = []           # (provider, label, folder)
    found += [("OneDrive", label, folder) for label, folder in _onedrive_client_roots(home, config_home)]
    found += [("Dropbox", label, folder) for label, folder in _dropbox_roots(home)]
    found += [("Nextcloud", label, folder) for label, folder in _nextcloud_roots(config_home, "Nextcloud", "nextcloud.cfg")]
    found += [("ownCloud", label, folder) for label, folder in _nextcloud_roots(config_home, "ownCloud", "owncloud.cfg")]
    # Insync and MEGA keep their settings in databases; use their default folders
    # when the app's data folder shows it is installed and set up.
    for provider, folder_name, data_dirs in (
            ("Insync", "Insync", (".config/Insync", ".local/share/Insync")),
            ("MEGA", "MEGA", (".local/share/data/Mega Limited",))):
        if any(os.path.isdir(os.path.join(home, d)) for d in data_dirs):
            found.append((provider, provider, os.path.join(home, folder_name)))
    roots = []
    for provider, label, folder in found:
        folder = os.path.normpath(folder)
        if folder not in skip and not os.path.isdir(folder):
            continue                                  # configured but never synced
        procs = SYNC_PROCESSES.get(provider, ())
        is_running = any(p in running for p in procs) if procs else None
        detail = f"{provider} sync app " + ("running" if is_running else "not running") if procs else ""
        roots.append(CloudRoot(provider, label, folder, None, can_pin=False, reports_local=False,
                               icon_names=PROVIDER_ICONS.get(provider, ()), running=is_running, detail=detail))
    return roots


# ---------------------------------------------------------------------------
class CloudProviders:
    def __init__(self, roots: Optional[List[CloudRoot]] = None) -> None:
        self.roots: List[CloudRoot] = []
        self.set_roots(roots if roots is not None else self.detect())

    @staticmethod
    def detect() -> List[CloudRoot]:
        try:
            if IS_WINDOWS:
                return detect_windows()
            if IS_MAC:
                return detect_mac()
            return detect_linux()
        except Exception:  # noqa: BLE001 - detection must never stop the app starting
            pass
        return []

    def set_roots(self, roots: List[CloudRoot]) -> None:
        unique = {}
        for root in roots:
            unique.setdefault(root.norm, root)          # the same folder can be registered twice
        # Longest path first, so nested sync folders match the most specific one.
        self.roots = sorted(unique.values(), key=lambda r: len(r.norm), reverse=True)

    def root_at(self, path: str) -> Optional[CloudRoot]:
        """The cloud root whose folder IS ``path`` (not just contains it)."""
        root = self.find(path)
        return root if root is not None and root.norm == _norm(path) else None

    def find(self, path: str) -> Optional[CloudRoot]:
        """The cloud folder containing ``path`` (or None)."""
        if not self.roots or "://" in path:
            return None
        p = _norm(path)
        for root in self.roots:
            if p == root.norm or p.startswith(root.norm + os.sep):
                return root
        return None


# ---------------------------------------------------------------------------
# Always keep on this device / Free up space (Windows)
# ---------------------------------------------------------------------------
def _attrib(args: List[str]) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    result = subprocess.run(["attrib"] + args, capture_output=True, text=True, creationflags=flags, timeout=600)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout or "attrib failed").strip())


def set_keep_on_device(path: str, keep: bool, is_dir: bool,
                       run: Callable[[List[str]], None] = _attrib) -> None:
    """keep=True: "Always keep on this device" (download and keep).
    keep=False: "Free up space" (keep only in the cloud).
    For folders, everything inside is changed too. ``run`` is replaceable for tests."""
    flags = ["+P", "-U"] if keep else ["-P", "+U"]
    run(flags + [path])
    if is_dir:
        run(flags + [os.path.join(path, "*"), "/S", "/D"])

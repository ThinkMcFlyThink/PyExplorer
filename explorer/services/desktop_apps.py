"""
Desktop applications on Linux: which apps can open a file type, which one
is the default, and how to start them.

Every installed app has a ".desktop" file (e.g. /usr/share/applications/mpv.desktop):

    Name=mpv Media Player
    Exec=mpv --player-operation-mode=pseudo-gui -- %U
    MimeType=video/x-matroska;video/mp4;...

The Exec line says how to start it. Its "field code" tells us what it accepts:
    %f / %F  one / several local FILE paths only
    %u / %U  one / several URLs (http://..., smb://...) - or file paths

That matters for network files: apps that accept URLs can stream a file
(see stream_server.py); file-only apps need a real path.

The default app comes from ``xdg-mime query default <type>`` (what Nemo and
the desktop use).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

SINGLE_CODES = ("%f", "%u")
MULTI_CODES = ("%F", "%U")


@dataclass
class DesktopApp:
    id: str                      # "mpv.desktop"
    name: str                    # "mpv Media Player"
    exec_line: str               # the Exec= value
    icon: str = ""
    mime_types: List[str] = field(default_factory=list)
    terminal: bool = False
    path: str = ""               # where the .desktop file is

    @property
    def accepts_urls(self) -> bool:
        return "%u" in self.exec_line or "%U" in self.exec_line

    @property
    def accepts_many(self) -> bool:
        return "%F" in self.exec_line or "%U" in self.exec_line

    def command(self, targets: List[str]) -> List[str]:
        """The command line that opens ``targets`` (paths or URLs) with this app."""
        try:
            parts = shlex.split(self.exec_line)
        except ValueError:
            parts = self.exec_line.split()
        argv: List[str] = []
        used = False
        for part in parts:
            if part in MULTI_CODES:
                argv.extend(targets)
                used = True
            elif part in SINGLE_CODES:
                argv.append(targets[0] if targets else "")
                used = True
            elif part == "%i":
                if self.icon:
                    argv += ["--icon", self.icon]
            elif part == "%c":
                argv.append(self.name)
            elif part == "%k":
                argv.append(self.path)
            elif part in ("%d", "%D", "%n", "%N", "%v", "%m"):
                continue                                   # deprecated codes: drop them
            else:
                for code in SINGLE_CODES:                  # e.g. "--file=%f"
                    if code in part and targets:
                        part = part.replace(code, targets[0])
                        used = True
                argv.append(part.replace("%%", "%"))
        if not used:                                       # no field code at all: append the file
            argv.extend(targets[:1])
        if self.terminal:
            term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
            if term:
                argv = [term, "-e"] + argv
        return argv


def application_dirs() -> List[str]:
    """Where .desktop files live, most important first (the user's own first)."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    dirs = [home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += ["/var/lib/flatpak/exports/share", os.path.join(os.path.expanduser("~"), ".local/share/flatpak/exports/share"),
             "/var/lib/snapd/desktop"]
    out = []
    for d in dirs:
        path = os.path.join(d, "applications")
        if d and path not in out and os.path.isdir(path):
            out.append(path)
    return out


def parse_desktop_file(path: str, app_id: str) -> Optional[DesktopApp]:
    values: Dict[str, str] = {}
    section = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    section = line
                    continue
                if section == "[Desktop Entry]" and "=" in line:
                    key, _, value = line.partition("=")
                    values.setdefault(key.strip(), value.strip())
    except OSError:
        return None
    if values.get("Type", "Application") != "Application" or "Exec" not in values:
        return None
    if values.get("Hidden", "").lower() == "true":
        return None
    try_exec = values.get("TryExec")
    if try_exec and not (os.path.isabs(try_exec) and os.path.exists(try_exec)) and not shutil.which(try_exec):
        return None                                        # the program isn't actually installed
    return DesktopApp(app_id, values.get("Name", app_id), values["Exec"], values.get("Icon", ""),
                      [m for m in values.get("MimeType", "").split(";") if m],
                      values.get("Terminal", "").lower() == "true", path)


class DesktopApps:
    """All installed apps, read once (in the background) and kept."""

    def __init__(self, dirs: Optional[List[str]] = None,
                 query_default=None) -> None:
        self._dirs = dirs
        self._apps: Optional[Dict[str, DesktopApp]] = None
        self._by_mime: Dict[str, List[str]] = {}
        self._defaults: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._query_default = query_default or self._xdg_default

    def load(self) -> Dict[str, DesktopApp]:
        with self._lock:
            if self._apps is None:
                apps: Dict[str, DesktopApp] = {}
                for base in self._dirs if self._dirs is not None else application_dirs():
                    for root, _dirs, files in os.walk(base):
                        for name in files:
                            if not name.endswith(".desktop"):
                                continue
                            rel = os.path.relpath(os.path.join(root, name), base)
                            app_id = rel.replace(os.sep, "-")      # spec: sub/dir/x.desktop -> sub-dir-x.desktop
                            if app_id in apps:
                                continue                           # the first (user's own) wins
                            app = parse_desktop_file(os.path.join(root, name), app_id)
                            if app:
                                apps[app_id] = app
                by_mime: Dict[str, List[str]] = {}
                for app in apps.values():
                    for mime in app.mime_types:
                        by_mime.setdefault(mime, []).append(app.id)
                self._apps, self._by_mime = apps, by_mime
            return self._apps

    def get(self, app_id: str) -> Optional[DesktopApp]:
        return self.load().get(app_id)

    @staticmethod
    def _xdg_default(mime: str) -> Optional[str]:
        if not shutil.which("xdg-mime"):
            return None
        try:
            out = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    def default_for(self, mime_types: Iterable[str]) -> Optional[DesktopApp]:
        """The desktop's default app for the first of ``mime_types`` that has one."""
        apps = self.load()
        for mime in mime_types:
            cached = self._defaults.get(mime)
            if cached is None or time.monotonic() - cached[1] > 60:
                cached = (self._query_default(mime), time.monotonic())
                self._defaults[mime] = cached
            if cached[0] and cached[0] in apps:
                return apps[cached[0]]
        return None

    def apps_for(self, mime_types: Iterable[str]) -> List[DesktopApp]:
        """Every app that can open these types, the default first."""
        apps = self.load()
        mime_types = list(mime_types)
        seen, out = set(), []
        default = self.default_for(mime_types)
        if default:
            seen.add(default.id)
            out.append(default)
        found = []
        for mime in mime_types:
            for app_id in self._by_mime.get(mime, []):
                if app_id not in seen:
                    seen.add(app_id)
                    found.append(apps[app_id])
        return out + sorted(found, key=lambda a: a.name.lower())

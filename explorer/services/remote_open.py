"""
RemoteOpener - decides HOW to hand a file on a network location to another
app (mpv, VLC, LibreOffice...), best option first:

  1. "path"     a real path to the same file (an existing mount, a mount we make
                like Nemo does, or a Windows \\\\server\\share path). Works with
                every app; the app reads/streams the file itself.
  2. "url"      a private streaming address from StreamServer - for apps that
                accept URLs (most media players do). Seeking works.
  3. "download" copy to a temp folder first (the old way; always works).

The setting "network.open_files" can force "download".
Call ``resolve`` from a worker thread: mounting and stat-ing can take a moment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import network_paths


@dataclass
class OpenTarget:
    kind: str                 # "path" | "url" | "download"
    value: Optional[str] = None


class RemoteOpener:
    def __init__(self, vfs, stream_server, settings) -> None:
        self._vfs = vfs
        self._stream = stream_server
        self._settings = settings

    def resolve(self, path: str, accepts_urls: bool) -> OpenTarget:
        fs = self._vfs.resolve(path)
        if fs.is_local:
            return OpenTarget("path", path)
        mode = self._settings.get("network.open_files", "stream")
        if mode == "download":
            return OpenTarget("download")
        conn = getattr(fs, "conn", {}) or {}
        local = network_paths.local_path_for(path, conn.get("user") or "", conn.get("password") or "",
                                              allow_mount=bool(self._settings.get("network.mount_shares", True)))
        if local:
            return OpenTarget("path", local)
        if accepts_urls:
            return OpenTarget("url", self._stream.url_for(path))
        return OpenTarget("download")

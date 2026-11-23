"""
ComputerFileSystem - the virtual "computer://" folder.

It lists every drive/mount/cloud folder plus every saved network connection,
so it's a handy starting point (Windows calls this "This PC"). Its entries
point at real locations, so opening one simply navigates there.

The drive list comes from ``set_device_source``: the app hands us the live
list kept up to date by services/device_monitor.py (this module may not
import services itself). Without one, it falls back to the OS drive list.
"""
from __future__ import annotations

from typing import Callable, Iterator, List, Optional, Tuple

from ..core.fileinfo import FileEntry
from ..core.platform_info import os_drives
from .base import FileSystem

ROOT = "computer://"


class ComputerFileSystem(FileSystem):
    scheme = "computer"

    def __init__(self, connections) -> None:
        self._connections = connections
        self._devices: Optional[Callable[[], list]] = None

    def set_device_source(self, source: Callable[[], list]) -> None:
        """``source()`` returns objects with label, path, kind, type_label (services.devices.Device)."""
        self._devices = source

    def list_dir(self, path: str) -> Iterator[FileEntry]:
        if self._devices is not None:
            for dev in self._devices():
                yield FileEntry(dev.label, dev.path, ROOT, True,
                                extra={"kind": dev.kind, "type_label": dev.type_label})
        else:
            for label, drive_path, kind in os_drives():
                yield FileEntry(label, drive_path, ROOT, True,
                                extra={"kind": kind, "type_label": "Network drive" if kind == "network" else "Drive"})
        for conn in self._connections.all():
            uri = self._connections.root_uri(conn)
            yield FileEntry(conn.get("name") or conn["host"], uri, ROOT, True,
                            extra={"kind": "network", "type_label": conn["protocol"].upper() + " connection"})

    def stat(self, path: str) -> FileEntry:
        return FileEntry("Computer", ROOT, ROOT, True, extra={"kind": "computer"})

    def join(self, folder: str, name: str) -> str:
        return ROOT

    def parent(self, path: str) -> Optional[str]:
        return None

    def basename(self, path: str) -> str:
        return "Computer"

    def breadcrumbs(self, path: str) -> List[Tuple[str, str]]:
        return [("Computer", ROOT)]

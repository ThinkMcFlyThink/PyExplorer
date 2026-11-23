"""
VfsRegistry - "which FileSystem owns this path?"

    registry.resolve("/home/me")                 -> LocalFileSystem
    registry.resolve("computer://")              -> ComputerFileSystem
    registry.resolve("sftp://me@host:22/var")    -> SFTPFileSystem for me@host:22

Remote file systems are created on first use and cached per server, so all
tabs browsing the same server share one connection.

EXTENDING: ``register_scheme("webdav", factory)`` where
``factory(prefix, conn_dict, credentials) -> FileSystem``.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict

from ..core.connections import DEFAULT_PORTS, make_prefix, split_uri
from ..core.platform_info import IS_WINDOWS
from .base import FileSystem
from .computer import ROOT as COMPUTER_ROOT, ComputerFileSystem
from .ftp import FTPFileSystem
from .local import LocalFileSystem
from .sftp import CredentialsProvider, SFTPFileSystem
from .smb import SMBFileSystem


def scheme_of(path: str) -> str:
    idx = path.find("://")
    return path[:idx].lower() if idx > 0 else ""


class VfsRegistry:
    def __init__(self, connections, credentials: CredentialsProvider) -> None:
        self._connections = connections
        self._credentials = credentials
        self.local = LocalFileSystem()
        self.computer = ComputerFileSystem(connections)
        self._factories: Dict[str, Callable] = {
            "sftp": SFTPFileSystem, "ftp": FTPFileSystem, "ftps": FTPFileSystem, "smb": SMBFileSystem,
        }
        self._remote: Dict[str, FileSystem] = {}
        self._lock = threading.Lock()

    def register_scheme(self, scheme: str, factory: Callable) -> None:
        self._factories[scheme.lower()] = factory

    @staticmethod
    def from_user_input(text: str) -> str:
        """Turn what someone typed into a path we understand:
            \\\\truenas\\media\\Films  (Windows-style share path, typed on Linux/macOS)
            -> smb://truenas/media/Films
        On Windows, UNC paths are left alone (Windows opens them itself)."""
        text = text.strip()
        if not IS_WINDOWS and text.startswith("\\\\") and len(text) > 2:
            return "smb://" + text.lstrip("\\/").replace("\\", "/")
        return text

    def is_remote(self, path: str) -> bool:
        return scheme_of(path) in self._factories

    def resolve(self, path: str) -> FileSystem:
        scheme = scheme_of(path)
        if not scheme or (len(scheme) == 1):     # "C://" is not a scheme
            return self.local
        if path.startswith(COMPUTER_ROOT):
            return self.computer
        factory = self._factories.get(scheme)
        if factory is None:
            raise OSError(f"Unsupported location type: {scheme}://")
        protocol, user, host, port, _ = split_uri(path)
        prefix = make_prefix(protocol, user, host, port or DEFAULT_PORTS.get(protocol, 0))
        with self._lock:
            fs = self._remote.get(prefix)
            if fs is None:
                conn = self._connections.find_for_uri(path) or {
                    "name": host, "protocol": protocol, "host": host,
                    "port": port, "user": user, "password": ""}
                fs = factory(prefix, conn, self._credentials)
                self._remote[prefix] = fs
            return fs

    def disconnect(self, prefix_or_path: str) -> None:
        with self._lock:
            for prefix in [p for p in self._remote if prefix_or_path.startswith(p)]:
                self._remote.pop(prefix).close()

    def close_all(self) -> None:
        with self._lock:
            for fs in self._remote.values():
                try:
                    fs.close()
                except Exception:  # noqa: BLE001
                    pass
            self._remote.clear()

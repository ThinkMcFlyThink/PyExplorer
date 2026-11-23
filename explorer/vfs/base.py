"""
The FileSystem interface that every location type implements.

Paths are plain strings:
    local  : "/home/me/file.txt" or "C:\\Users\\me\\file.txt"
    remote : "sftp://me@host:22/var/www/index.html"
    special: "computer://"   (list of drives + saved connections)

All methods may be slow (network!) so they are only ever called from worker
threads, never from the UI thread.
"""
from __future__ import annotations

import posixpath
from typing import BinaryIO, Iterator, List, Optional, Tuple

from ..core.fileinfo import FileEntry


class FileSystem:
    scheme = ""            # "" for local, "sftp", "ftp"...
    is_local = False       # True when paths are real OS paths (open with apps, terminal cd...)
    can_watch = False      # cheap to poll for changes?
    can_trash = False      # supports "move to recycle bin"?

    # -- listing -----------------------------------------------------------
    def list_dir(self, path: str) -> Iterator[FileEntry]:
        """Yield the entries of a folder (including hidden ones; the model
        decides whether to show them). Raise OSError on failure."""
        raise NotImplementedError

    def list_dir_light(self, path: str) -> Iterator[FileEntry]:
        """Like list_dir, but may skip size/date to be faster (used by search).
        Entries without details have ``mtime == -1``; call fill_details() on them."""
        return self.list_dir(path)

    def fill_details(self, entry: FileEntry) -> None:
        """Fetch size/date for an entry from list_dir_light (no-op if it has them)."""

    def stat(self, path: str) -> FileEntry:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        try:
            return self.stat(path).is_dir
        except OSError:
            return False

    # -- path helpers (pure string work, safe on any thread) ----------------
    def join(self, folder: str, name: str) -> str:
        raise NotImplementedError

    def parent(self, path: str) -> Optional[str]:
        raise NotImplementedError

    def basename(self, path: str) -> str:
        raise NotImplementedError

    def breadcrumbs(self, path: str) -> List[Tuple[str, str]]:
        """[(label, path)] from the root down to ``path`` - used by the address bar."""
        raise NotImplementedError

    def normalize(self, path: str) -> str:
        return path

    def is_inside(self, path: str, folder: str) -> bool:
        """True if ``path`` is ``folder`` or somewhere below it."""
        sep = "/" if "://" in folder else self.sep
        folder = folder.rstrip("/\\") + sep
        return path == folder[:-1] or path.startswith(folder)

    sep = "/"

    # -- changing things -----------------------------------------------------
    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def rename(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def remove_file(self, path: str) -> None:
        raise NotImplementedError

    def remove_dir(self, path: str) -> None:
        """Remove an EMPTY directory."""
        raise NotImplementedError

    def remove_tree(self, path: str, token=None) -> None:
        """Delete a file or a whole folder tree. Generic version works for any
        FileSystem; LocalFileSystem overrides it with a faster one."""
        entry = self.stat(path)
        if not entry.is_dir or entry.is_link:
            self.remove_file(path)
            return
        for child in list(self.list_dir(path)):
            if token is not None:
                token.check()
            self.remove_tree(child.path, token)
        self.remove_dir(path)

    def open_read(self, path: str) -> BinaryIO:
        raise NotImplementedError

    def open_write(self, path: str) -> BinaryIO:
        raise NotImplementedError

    def set_mtime(self, path: str, mtime: float) -> None:
        """Best effort; used to preserve dates when copying."""

    def disk_usage(self, path: str) -> Optional[Tuple[int, int, int]]:
        """(total, used, free) in bytes, or None if unknown."""
        return None

    def same_volume(self, a: str, b: str) -> bool:
        """Can ``rename`` move a -> b directly (instead of copy + delete)?"""
        return False

    def display(self, path: str) -> str:
        """Text shown in the address bar edit box."""
        return path

    def close(self) -> None:
        pass


class RemoteFileSystem(FileSystem):
    """Shared path logic for URI-based remote file systems.
    Internally they work with POSIX paths; outside they use full URIs."""

    def __init__(self, prefix: str, conn: dict) -> None:
        self.prefix = prefix          # e.g. "sftp://bob@host:22"
        self.conn = conn

    def to_remote(self, uri: str) -> str:
        if uri.startswith(self.prefix):
            rest = uri[len(self.prefix):]
        elif "://" in uri:
            # Same server written differently (e.g. without the port): take just the path part.
            from urllib.parse import unquote, urlsplit
            rest = unquote(urlsplit(uri).path)
        else:
            rest = uri
        remote = posixpath.normpath(rest or "/")
        if not remote.startswith("/"):
            remote = "/" + remote
        return "/" + remote.lstrip("/")      # posix normpath keeps a leading "//"

    def to_uri(self, remote: str) -> str:
        return self.prefix + (remote if remote.startswith("/") else "/" + remote)

    def join(self, folder: str, name: str) -> str:
        return self.to_uri(posixpath.join(self.to_remote(folder), name))

    def parent(self, path: str) -> Optional[str]:
        remote = self.to_remote(path)
        if remote == "/":
            return None
        return self.to_uri(posixpath.dirname(remote))

    def basename(self, path: str) -> str:
        return posixpath.basename(self.to_remote(path)) or self.conn.get("name") or self.prefix

    def normalize(self, path: str) -> str:
        return self.to_uri(self.to_remote(path))

    def breadcrumbs(self, path: str) -> List[Tuple[str, str]]:
        label = self.conn.get("name") or self.prefix.split("://", 1)[1]
        crumbs = [(label, self.prefix + "/")]
        acc = ""
        for part in [p for p in self.to_remote(path).split("/") if p]:
            acc += "/" + part
            crumbs.append((part, self.to_uri(acc)))
        return crumbs

    def same_volume(self, a: str, b: str) -> bool:
        return a.startswith(self.prefix) and b.startswith(self.prefix)

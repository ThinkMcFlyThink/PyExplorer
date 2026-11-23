"""
LocalFileSystem - your own disks (and anything the OS has mounted,
including Windows UNC paths like \\\\server\\share and Linux CIFS/NFS mounts).

Uses ``os.scandir``, the fastest way to list a folder in Python: on Windows
the size and dates come free with the listing; on Linux/macOS we need one
``stat`` per file, which is why the scanner streams results in batches.
"""
from __future__ import annotations

import os
import re
import shutil
import stat as stat_mod
import sys
from pathlib import PurePath
from typing import BinaryIO, Iterator, List, Optional, Tuple

from ..core.cloud import state_from_stat
from ..core.fileinfo import FileEntry
from ..core.platform_info import IS_LINUX, IS_MAC, IS_WINDOWS
from .automount import ensure_mounted
from .base import FileSystem

_HIDDEN_ATTR = 0x2   # FILE_ATTRIBUTE_HIDDEN on Windows
# Only Windows and macOS report cloud placeholder states (see core/cloud.py).
_CLOUD_AWARE = IS_WINDOWS or IS_MAC


def _is_hidden(name: str, st) -> bool:
    if name.startswith("."):
        return True
    if IS_WINDOWS:
        return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_ATTR)
    return False


class LocalFileSystem(FileSystem):
    scheme = ""
    is_local = True
    can_watch = True
    can_trash = True
    sep = os.sep

    def list_dir(self, path: str) -> Iterator[FileEntry]:
        if IS_LINUX:
            ensure_mounted(path)              # an /etc/fstab network share? mount it first
        with os.scandir(path) as it:
            for de in it:
                try:
                    is_link = de.is_symlink()
                    try:
                        st = de.stat()                     # follows links
                    except OSError:
                        st = de.stat(follow_symlinks=False)  # broken link
                    is_dir = stat_mod.S_ISDIR(st.st_mode)
                except OSError:
                    continue                               # vanished while listing
                entry = FileEntry(de.name, de.path, path, is_dir,
                                  0 if is_dir else st.st_size, st.st_mtime,
                                  is_link, _is_hidden(de.name, st))
                if _CLOUD_AWARE:
                    entry.cloud = state_from_stat(st)
                yield entry

    def list_dir_light(self, path: str) -> Iterator[FileEntry]:
        if IS_WINDOWS:                 # Windows hands us size/date for free with the listing
            yield from self.list_dir(path)
            return
        with os.scandir(path) as it:
            for de in it:
                try:
                    is_dir = de.is_dir()           # uses the directory's type info: no extra disk access
                    is_link = de.is_symlink()
                except OSError:
                    continue
                yield FileEntry(de.name, de.path, path, is_dir, 0, -1.0, is_link, de.name.startswith("."))

    def fill_details(self, entry: FileEntry) -> None:
        if entry.mtime != -1:
            return
        try:
            st = os.stat(entry.path)
            entry.size = 0 if entry.is_dir else st.st_size
            entry.mtime = st.st_mtime
            if _CLOUD_AWARE:
                entry.cloud = state_from_stat(st)
        except OSError:
            entry.mtime = 0.0

    def stat(self, path: str) -> FileEntry:
        st = os.stat(path)
        is_link = os.path.islink(path)
        name = self.basename(path)
        is_dir = stat_mod.S_ISDIR(st.st_mode)
        entry = FileEntry(name, path, self.parent(path) or path, is_dir,
                          0 if is_dir else st.st_size, st.st_mtime, is_link, _is_hidden(name, st))
        if _CLOUD_AWARE:
            entry.cloud = state_from_stat(st)
        return entry

    # -- paths ---------------------------------------------------------------
    def join(self, folder: str, name: str) -> str:
        return os.path.join(folder, name)

    def parent(self, path: str) -> Optional[str]:
        parent = os.path.dirname(path.rstrip("\\/") or path)
        if not parent or parent == path:
            return "computer://" if IS_WINDOWS else None
        return parent

    def basename(self, path: str) -> str:
        return os.path.basename(path.rstrip("\\/")) or path

    def normalize(self, path: str) -> str:
        path = os.path.expandvars(os.path.expanduser(path.strip()))
        if IS_WINDOWS and re.fullmatch(r"[A-Za-z]:", path):
            path += "\\"                      # "C:" alone means "current dir on C:"
        return os.path.normpath(os.path.abspath(path))

    def breadcrumbs(self, path: str) -> List[Tuple[str, str]]:
        crumbs: List[Tuple[str, str]] = []
        if IS_WINDOWS:
            crumbs.append(("This PC", "computer://"))
        acc: Optional[str] = None
        for part in PurePath(path).parts:
            acc = part if acc is None else os.path.join(acc, part)
            crumbs.append((part.rstrip("\\/") or part, acc))
        return crumbs

    # -- changing --------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        os.mkdir(path)

    def rename(self, src: str, dst: str) -> None:
        os.rename(src, dst)

    def remove_file(self, path: str) -> None:
        os.remove(path)

    def remove_dir(self, path: str) -> None:
        os.rmdir(path)

    def remove_tree(self, path: str, token=None) -> None:
        if os.path.isdir(path) and not os.path.islink(path):
            def retry(func, p, _exc):   # clear read-only flag (Windows) and retry once
                os.chmod(p, stat_mod.S_IWRITE)
                func(p)
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=retry)
            else:
                shutil.rmtree(path, onerror=retry)
        else:
            os.remove(path)

    def open_read(self, path: str) -> BinaryIO:
        return open(path, "rb")

    def open_write(self, path: str) -> BinaryIO:
        return open(path, "wb")

    def set_mtime(self, path: str, mtime: float) -> None:
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass

    def disk_usage(self, path: str):
        try:
            u = shutil.disk_usage(path)
            return (u.total, u.used, u.free)
        except OSError:
            return None

    def same_volume(self, a: str, b: str) -> bool:
        try:
            dev_a = os.stat(a).st_dev
            dev_b = os.stat(b if os.path.isdir(b) else os.path.dirname(b)).st_dev
            return dev_a == dev_b
        except OSError:
            return False

"""
The "General" section every item gets: size, dates, permissions, owner,
link target, MIME type - and for folders, what is inside (counted quickly,
with a time budget, so a folder of a million files never holds things up).
"""
from __future__ import annotations

import mimetypes
import os
import stat as stat_mod
import sys
import time
from typing import Optional

from ...core.formatting import human_size, human_time
from .base import Section, Source

FOLDER_COUNT_BUDGET = 0.6          # seconds; after that the count says "at least"
FOLDER_COUNT_LIMIT = 200_000


def size_text(num: int) -> str:
    return f"{human_size(num)} ({num:,} bytes)" if num >= 1024 else human_size(num)


def basic(entry) -> Section:
    """What we know without touching the disk (shown instantly)."""
    s = Section("General")
    if not entry.is_dir:
        s.add("Size", size_text(entry.size))
    s.add("Modified", human_time(entry.mtime))
    s.add("Location", entry.parent)
    return s


def _owner(st) -> str:
    if sys.platform.startswith("win"):
        return ""
    try:
        import grp
        import pwd
        user = pwd.getpwuid(st.st_uid).pw_name
    except (ImportError, KeyError):
        user = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except (ImportError, KeyError, NameError):
        group = str(st.st_gid)
    return f"{user} : {group}"


def general(src: Source) -> Section:
    """Worker thread: the full General section."""
    e = src.entry
    s = Section("General")
    if not e.is_dir:
        s.add("Size", size_text(e.size))
    if src.local:
        try:
            st = os.stat(e.path)
            lst = os.lstat(e.path)
        except OSError:
            st = lst = None
        if st is not None:
            if not e.is_dir and st.st_size != e.size:            # the listing is older than the file
                s.rows[0] = ("Size", size_text(st.st_size))
            s.add("Modified", human_time(st.st_mtime))
            birth = getattr(st, "st_birthtime", None)
            if birth:
                s.add("Created", human_time(birth))
            elif sys.platform.startswith("win"):
                s.add("Created", human_time(st.st_ctime))
            else:
                s.add("Changed", human_time(st.st_ctime))
            s.add("Accessed", human_time(st.st_atime))
            if not sys.platform.startswith("win"):
                s.add("Permissions", f"{stat_mod.filemode(st.st_mode)}  ({stat_mod.S_IMODE(st.st_mode):o})")
                s.add("Owner", _owner(st))
        if lst is not None and stat_mod.S_ISLNK(lst.st_mode):
            try:
                s.add("Link to", os.readlink(e.path))
            except OSError:
                pass
    else:
        s.add("Modified", human_time(e.mtime))
    if not e.is_dir:
        mime, encoding = mimetypes.guess_type(e.name, strict=False)
        s.add("MIME type", mime + (f" ({encoding})" if encoding else "") if mime else "")
    s.add("Location", e.parent)
    return s


def folder_contents(src: Source) -> Optional[Section]:
    """Count what a folder holds (direct children only)."""
    e = src.entry
    folders = files = hidden = 0
    total = 0
    complete = True
    start = time.monotonic()
    try:
        if src.local:
            with os.scandir(e.path) as it:
                for n, d in enumerate(it):
                    if n % 512 == 0:
                        src.check()
                        if time.monotonic() - start > FOLDER_COUNT_BUDGET or n >= FOLDER_COUNT_LIMIT:
                            complete = False
                            break
                    try:
                        is_dir = d.is_dir()
                    except OSError:
                        is_dir = False
                    if is_dir:
                        folders += 1
                    else:
                        files += 1
                        try:
                            total += d.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
                    if d.name.startswith("."):
                        hidden += 1
        else:
            for n, child in enumerate(src.fs.list_dir_light(e.path)):
                if n % 256 == 0:
                    src.check()
                    if time.monotonic() - start > FOLDER_COUNT_BUDGET * 3:
                        complete = False
                        break
                if child.is_dir:
                    folders += 1
                else:
                    files += 1
                    total += child.size or 0
                if child.hidden:
                    hidden += 1
    except OSError as exc:
        s = Section("Contents")
        s.add("Can't read", exc.strerror or str(exc))
        return s
    more = "" if complete else "+"
    s = Section("Contents")
    s.add("Folders", f"{folders:,}{more}")
    s.add("Files", f"{files:,}{more}")
    if hidden:
        s.add("Hidden", f"{hidden:,}{more}")
    if files:
        s.add("Files' size", ("at least " if not complete else "") + size_text(total) + "  (not counting subfolders)")
    return s

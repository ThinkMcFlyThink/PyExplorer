"""
file_index - a compact, very fast index of every file under a folder.

This is what makes the Search tab feel like Voidtools' "Everything".

How Everything is fast, and how we copy it in Python
-----------------------------------------------------
Looping over a million files in Python and testing each name takes about a
second - far too slow to search as you type. So the index does NOT keep one
Python object per file. Instead it keeps:

    names    ONE big string with every name, one per line:  "a.txt\\nb.jpg\\n..."
    lower    the same in lower case (for case-insensitive search)
    starts   where each name starts in that string           (array of ints)
    parent   which folder each entry is in (index into ``dirs``)
    flags    folder? hidden? symlink?                          (one byte each)
    size     bytes                                              (array of ints)
    mtime    modification time                                  (array of floats)
    dirs     the full path of every folder (a normal list)

Searching for "holiday" is then ONE ``str.find`` over the big string - that
runs in C at gigabytes per second. Only the handful of lines that contain a
match are looked at in Python. Wildcards and ``ext:`` become a regular
expression, also run in C over the whole string. Size/date/type filters run
over the compact arrays. A million files take ~70 MB and a name search takes
milliseconds.

Building: ``build_local`` reads the disk with several threads at once
(``os.scandir`` + one stat per entry for size/date). Remote folders (SFTP,
SMB...) are read through the app's file systems with ``build_from_entries``.

Keeping it current: ``FileIndex.patched`` re-reads just the folders that
changed and returns a new index (the old one keeps serving searches until
the new one is swapped in). See services/index_manager.py.

Plain Python - no Qt - so it can be tested and benchmarked on its own.
"""
from __future__ import annotations

import os
import re
import threading
from array import array
from bisect import bisect_right
from collections import deque
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from ..core.fileinfo import FileEntry

DIR, HIDDEN, LINK = 1, 2, 4            # bits in ``flags``
FORMAT_VERSION = 4                     # bump when the on-disk layout changes
IS_WINDOWS = os.name == "nt"
_WIN_HIDDEN = 0x2                      # FILE_ATTRIBUTE_HIDDEN


class Cancelled(Exception):
    pass


class FileIndex:
    """An immutable snapshot of every entry under ``root``. Thread-safe to read."""

    def __init__(self, root: str, sep: str, dirs: List[str], names: str, starts: array, lower: str,
                 lstarts: array, parent: array, flags: bytearray, size: array, mtime: array,
                 dir_id: array, built_at: float) -> None:
        self.root = root
        self.sep = sep                  # "/" or "\\" (Windows) - how child paths are joined
        self.dirs = dirs                # folder id -> full path (id 0 = root)
        self.parent = parent            # entry -> folder id
        self.flags = flags              # entry -> DIR | HIDDEN | LINK
        self.size = size                # entry -> bytes
        self.mtime = mtime              # entry -> seconds since epoch
        self.dir_id = dir_id            # entry -> its folder id if it is a folder, else -1
        self.built_at = built_at
        self.count = len(parent)
        self.names = names              # every name, one per line
        self.starts = starts            # entry -> offset in ``names`` (+ one past the end)
        self.lower = lower              # the same, lower case
        self.lstarts = lstarts          # offsets in ``lower`` (usually the very same array)
        self._hidden_dirs: Optional[bytearray] = None
        self._subtree_cache: Dict[Tuple, Optional[Set[int]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_names(cls, root: str, sep: str, dirs: List[str], names: List[str], parent: array,
                   flags: bytearray, size: array, mtime: array, dir_id: array, built_at: float) -> "FileIndex":
        blob = "\n".join(names) + "\n" if names else ""
        starts = _starts(names)
        lower = blob.lower()
        # lower() can change a name's length (e.g. "İ" -> "i̇"), then it needs its own offsets
        lstarts = starts if len(lower) == len(blob) else _starts([n.lower() for n in names])
        return cls(root, sep, dirs, blob, starts, lower, lstarts, parent, flags, size, mtime, dir_id, built_at)

    # -- reading entries ---------------------------------------------------------
    def name(self, i: int) -> str:
        return self.names[self.starts[i]:self.starts[i + 1] - 1]

    def path(self, i: int) -> str:
        folder = self.dirs[self.parent[i]]
        return folder + self.name(i) if folder.endswith(self.sep) else folder + self.sep + self.name(i)

    def entry(self, i: int) -> FileEntry:
        name = self.name(i)
        folder = self.dirs[self.parent[i]]
        path = folder + name if folder.endswith(self.sep) else folder + self.sep + name
        f = self.flags[i]
        return FileEntry(name, path, folder, bool(f & DIR), self.size[i], self.mtime[i],
                         is_link=bool(f & LINK), hidden=self.is_hidden(i))

    def is_hidden(self, i: int) -> bool:
        """Hidden itself, or inside a hidden folder (e.g. anything in ~/.cache)."""
        return bool(self.flags[i] & HIDDEN) or bool(self.hidden_dirs()[self.parent[i]])

    def hidden_dirs(self) -> bytearray:
        """folder id -> 1 if that folder is hidden or inside a hidden folder."""
        if self._hidden_dirs is None:
            hidden = bytearray(len(self.dirs))
            # A folder's own entry tells us if it is hidden; its parent tells us if an ancestor is.
            dir_ids, flags, parent = self.dir_id, self.flags, self.parent
            entry_of = {}
            for i in range(self.count):
                d = dir_ids[i]
                if d >= 0:
                    entry_of[d] = i
            for d in range(1, len(self.dirs)):          # ids grow downwards: parents come first
                i = entry_of.get(d)
                if i is not None:
                    hidden[d] = 1 if (flags[i] & HIDDEN or hidden[parent[i]]) else 0
            self._hidden_dirs = hidden
        return self._hidden_dirs

    # -- which folders a search may look in --------------------------------------
    def folders_under(self, location: str, exclude_names: Sequence[str] = ()) -> Optional[Set[int]]:
        """Folder ids inside ``location`` (including it), minus any folder whose
        name is in ``exclude_names`` and everything below those. ``None`` means
        "all folders" (cheapest: no check needed)."""
        key = (location, tuple(sorted(exclude_names)))
        with self._lock:
            if key in self._subtree_cache:
                return self._subtree_cache[key]
        whole = _same_path(location, self.root)
        if whole and not exclude_names:
            result = None
        else:
            sep = self.sep
            base = location.rstrip("/\\")
            prefix = base + sep
            excluded = {n.lower() for n in exclude_names}
            result = set()
            for d, folder in enumerate(self.dirs):
                if not whole and folder != base and folder != location and not folder.startswith(prefix):
                    continue
                if excluded:
                    rel = folder[len(base):] if not whole else folder[len(self.root.rstrip('/\\')):]
                    if any(part.lower() in excluded for part in rel.split(sep) if part):
                        continue
                result.add(d)
        with self._lock:
            if len(self._subtree_cache) > 32:
                self._subtree_cache.clear()
            self._subtree_cache[key] = result
        return result

    # -- the fast name scan --------------------------------------------------------
    def scan(self, pattern: "ScanPattern", chunk: int = 20000) -> Iterator[List[int]]:
        """Entry ids whose name matches ``pattern``, in index order, delivered in
        chunks - so the caller can stop early (e.g. after enough results) and a
        one-letter search doesn't have to collect two million hits first.

        The whole name string is searched in C (``str.find`` or a regex); after
        a hit we jump to the start of the next name, so each name counts once."""
        blob = self.names if pattern.case_sensitive else self.lower
        starts = self.starts if pattern.case_sensitive else self.lstarts
        check = pattern.check
        out: List[int] = []
        pos, n, last = 0, len(blob), self.count
        if pattern.literal is not None:
            find, needle = blob.find, pattern.literal
            while True:
                hit = find(needle, pos)
                if hit < 0:
                    break
                i = bisect_right(starts, hit) - 1
                if i >= last:
                    break
                nxt = starts[i + 1]
                if check is None or check(blob[starts[i]:nxt - 1]):
                    out.append(i)
                    if len(out) >= chunk:
                        yield out
                        out = []
                pos = nxt
        else:
            search = pattern.regex.search
            while pos < n:
                m = search(blob, pos)
                if m is None:
                    break
                i = bisect_right(starts, m.start()) - 1
                if i >= last:
                    break
                out.append(i)
                if len(out) >= chunk:
                    yield out
                    out = []
                pos = starts[i + 1]
        if out:
            yield out

    def test(self, pattern: "ScanPattern", ids: Iterable[int]) -> List[int]:
        """Keep the ids whose name matches ``pattern`` (to combine several patterns)."""
        blob = self.names if pattern.case_sensitive else self.lower
        starts = self.starts if pattern.case_sensitive else self.lstarts
        test = pattern.test
        return [i for i in ids if test(blob[starts[i]:starts[i + 1] - 1])]

    # -- keeping it current ------------------------------------------------------------
    def records(self) -> "Records":
        """Everything as plain lists (to build a changed copy)."""
        names = self.names.split("\n")[:-1] if self.count else []
        return Records(list(self.dirs), names, array("i", self.parent), bytearray(self.flags),
                       array("q", self.size), array("d", self.mtime), array("i", self.dir_id))

    def patched(self, changed_folders: Iterable[str], skip: Iterable[str] = (), now: float = 0.0,
                lister: Optional[Callable] = None) -> "FileIndex":
        """A copy with ``changed_folders`` re-read from disk: their direct
        contents are replaced; NEW sub-folders are read completely; REMOVED
        sub-folders disappear with everything inside them; unchanged
        sub-folders keep their (already indexed) contents."""
        import time as _time
        rec = self.records()
        dir_index = {p: d for d, p in enumerate(rec.dirs)}
        skip = set(skip)
        drop_parents: Set[int] = set()
        additions: List[Tuple[int, str, int, int, float, bool]] = []    # (parent, name, flags, size, mtime, is_dir)
        new_subtrees: List[Tuple[int, str]] = []                          # (entry index later, folder path)
        lister = lister or _list_local
        for folder in set(changed_folders):
            d = dir_index.get(folder)
            if d is None:
                continue                                                  # not in this index
            try:
                listing = lister(folder)
            except OSError:
                listing = None
            # Old children of this folder, by name: folders among them keep their subtree.
            old_child_dirs = {}
            for i in range(len(rec.names)):
                if rec.parent[i] == d and rec.dir_id[i] >= 0:
                    old_child_dirs[rec.names[i]] = rec.dir_id[i]
            drop_parents.add(d)
            if listing is None:                                           # the folder itself is gone
                drop_parents |= _subtree_ids(rec.dirs, folder, self.sep)
                continue
            seen_dirs = set()
            for name, flags, size, mtime in listing:
                is_dir = bool(flags & DIR) and not flags & LINK
                additions.append((d, name, flags, size, mtime, is_dir))
                if is_dir:
                    seen_dirs.add(name)
            for name, sub in old_child_dirs.items():
                if name not in seen_dirs:                                 # removed: drop all below it
                    drop_parents |= _subtree_ids(rec.dirs, rec.dirs[sub], self.sep)
        keep = [i for i in range(len(rec.names)) if rec.parent[i] not in drop_parents]
        names = [rec.names[i] for i in keep]
        parent = array("i", (rec.parent[i] for i in keep))
        flags = bytearray(rec.flags[i] for i in keep)
        size = array("q", (rec.size[i] for i in keep))
        mtime = array("d", (rec.mtime[i] for i in keep))
        dir_id = array("i", (rec.dir_id[i] for i in keep))
        dirs = rec.dirs
        for d, name, fl, sz, mt, is_dir in additions:
            folder = dirs[d]
            child = folder + name if folder.endswith(self.sep) else folder + self.sep + name
            sub = -1
            if is_dir:
                sub = dir_index.get(child)
                if sub is None:                                           # a brand-new folder
                    sub = len(dirs)
                    dirs.append(child)
                    dir_index[child] = sub
                    if child not in skip:
                        new_subtrees.append((sub, child))
            names.append(name)
            parent.append(d)
            flags.append(fl)
            size.append(sz)
            mtime.append(mt)
            dir_id.append(sub)
        for sub, path in new_subtrees:                                    # read new folders completely
            part = build_local(path, skip=skip)
            remap = [sub] + list(range(len(dirs), len(dirs) + len(part.dirs) - 1))   # part's folder 0 is ``sub``
            dirs.extend(part.dirs[1:])
            for j in range(part.count):
                names.append(part.name(j))
                parent.append(remap[part.parent[j]])
                flags.append(part.flags[j])
                size.append(part.size[j])
                mtime.append(part.mtime[j])
                dir_id.append(remap[part.dir_id[j]] if part.dir_id[j] >= 0 else -1)
        return FileIndex.from_names(self.root, self.sep, dirs, names, parent, flags, size, mtime, dir_id,
                                    now or _time.time())

    # -- saving --------------------------------------------------------------------------
    def to_state(self) -> dict:
        return {"version": FORMAT_VERSION, "root": self.root, "sep": self.sep, "dirs": self.dirs,
                "names": self.names, "starts": self.starts.tobytes(),
                # The lower-case copy is rebuilt on load (fast, in C) unless lower() changed some
                # name's length - then the copy and its offsets are saved too.
                "lower": None if self.lstarts is self.starts else self.lower,
                "lstarts": None if self.lstarts is self.starts else self.lstarts.tobytes(),
                "parent": self.parent.tobytes(), "flags": bytes(self.flags),
                "size": self.size.tobytes(), "mtime": self.mtime.tobytes(), "dir_id": self.dir_id.tobytes(),
                "built_at": self.built_at}

    @classmethod
    def from_state(cls, state: dict) -> "FileIndex":
        if state.get("version") != FORMAT_VERSION:
            raise ValueError("old index format")
        def arr(code: str, data: bytes) -> array:
            a = array(code)
            a.frombytes(data)
            return a
        starts = arr("q", state["starts"])
        lstarts = starts if state["lstarts"] is None else arr("q", state["lstarts"])
        lower = state["lower"] if state["lower"] is not None else state["names"].lower()
        return cls(state["root"], state["sep"], state["dirs"], state["names"], starts, lower, lstarts,
                   arr("i", state["parent"]), bytearray(state["flags"]), arr("q", state["size"]),
                   arr("d", state["mtime"]), arr("i", state["dir_id"]), state["built_at"])


class Records:
    """An index as plain lists (see FileIndex.records)."""

    def __init__(self, dirs, names, parent, flags, size, mtime, dir_id) -> None:
        self.dirs, self.names, self.parent, self.flags = dirs, names, parent, flags
        self.size, self.mtime, self.dir_id = size, mtime, dir_id


def _starts(names: List[str]) -> array:
    """Offsets of each name in "\\n".join(names) + "\\n", plus one past the end."""
    out = array("q", [0]) * (len(names) + 1)
    pos = 0
    for k, n in enumerate(names):
        out[k] = pos
        pos += len(n) + 1
    out[len(names)] = pos
    return out


def is_inside(path: str, folder: str) -> bool:
    """True if path == folder or path is below folder (works for / and \\ paths)."""
    if path == folder:
        return True
    base = folder.rstrip("/\\")
    if not base:                                   # folder is "/" (or "\\")
        return path.startswith(folder)
    return path.startswith(base + "/") or path.startswith(base + "\\") or path == base


def _same_path(a: str, b: str) -> bool:
    return a.rstrip("/\\") == b.rstrip("/\\") or a == b


def _subtree_ids(dirs: List[str], folder: str, sep: str) -> Set[int]:
    prefix = folder.rstrip("/\\") + sep
    return {d for d, p in enumerate(dirs) if p == folder or p.startswith(prefix)}


# ---------------------------------------------------------------------------
# Name patterns for the fast scan
# ---------------------------------------------------------------------------
class ScanPattern:
    """Something every matching NAME must contain - run over the whole name
    string at once. Either a plain ``literal`` (fastest) or a ``regex``."""

    def __init__(self, literal: Optional[str] = None, regex: Optional["re.Pattern"] = None,
                 case_sensitive: bool = False, check: Optional[Callable[[str], object]] = None) -> None:
        self.literal = literal          # fast scan for this text...
        self.regex = regex              # ...or for this regex (when there is no literal part)
        self.case_sensitive = case_sensitive
        self.check = check              # optional exact test of a candidate name (e.g. a wildcard)

    def test(self, name: str) -> bool:
        if self.literal is not None:
            return self.literal in name and (self.check is None or bool(self.check(name)))
        return self.regex.search(name) is not None

    @property
    def weight(self) -> int:
        """Rough selectivity: longer literals match fewer names (scan those first)."""
        return len(self.literal) * 10 if self.literal is not None else 5

    def __repr__(self) -> str:
        return f"ScanPattern({self.literal!r})" if self.literal is not None else f"ScanPattern(/{self.regex.pattern}/)"


def pattern_for(kind: str, value: str, case_sensitive: bool) -> ScanPattern:
    """Turn a query term (see core/query.scan_terms) into a ScanPattern.

    "literal"  plain text                       -> find it anywhere in the name
    "wildcard" e.g. "*.jpg", "IMG_????.*"       -> scan for its longest plain part,
                                                   then check the whole name
    "ext"      "jpg;png"                        -> scan for ".jpg"/".png", check the ending
    "regex"    a regular expression             -> run over all names at once
    """
    flags = re.MULTILINE | (0 if case_sensitive else re.IGNORECASE)
    if kind == "literal":
        return ScanPattern(literal=value if case_sensitive else value.lower(), case_sensitive=case_sensitive)
    if kind == "wildcard":
        rx = re.compile(wildcard_regex(value), flags)
        pieces = [p for p in re.split(r"[*?]", value) if p]
        longest = max(pieces, key=len) if pieces else ""
        if longest:
            return ScanPattern(literal=longest if case_sensitive else longest.lower(),
                               case_sensitive=case_sensitive, check=rx.search)
        return ScanPattern(regex=rx, case_sensitive=case_sensitive)
    if kind == "ext":
        exts = [x for x in value.split(";") if x]
        ends = tuple("." + (x if case_sensitive else x.lower()) for x in exts)
        if len(exts) == 1:
            return ScanPattern(literal=ends[0], case_sensitive=case_sensitive,
                               check=lambda name, ends=ends: name.endswith(ends))
        rx = re.compile(r"\.(?:%s)$" % "|".join(re.escape(x) for x in exts), flags)
        return ScanPattern(regex=rx, case_sensitive=case_sensitive)
    if kind == "alternatives":                     # "jpg|png" - each part may hold wildcards
        parts = []
        for alt in (a for a in value.split("|") if a):
            parts.append(wildcard_regex(alt) if ("*" in alt or "?" in alt) else re.escape(alt))
        return ScanPattern(regex=re.compile("|".join(f"(?:{p})" for p in parts), flags),
                           case_sensitive=case_sensitive)
    return ScanPattern(regex=re.compile(value, flags), case_sensitive=case_sensitive)


def wildcard_regex(pattern: str) -> str:
    """"*.jpg" -> a regex for a WHOLE name (one line of the name string)."""
    out = []
    for ch in pattern:
        if ch == "*":
            out.append("[^\\n]*")
        elif ch == "?":
            out.append("[^\\n]")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


# ---------------------------------------------------------------------------
# Building an index from disk
# ---------------------------------------------------------------------------
def _list_local(folder: str) -> List[Tuple[str, int, int, float]]:
    """[(name, flags, size, mtime)] of one folder (raises OSError if unreadable).
    Kept tight on purpose: it runs once per file on the whole disk."""
    out = []
    append = out.append
    with os.scandir(folder) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            mode = st.st_mode & 0o170000
            name = e.name
            if mode == 0o040000:                                 # folder
                f = DIR
            elif mode == 0o120000:                               # symlink: to a folder counts as a folder
                try:
                    f = LINK | (DIR if e.is_dir() else 0)
                except OSError:
                    f = LINK
            else:
                f = 0
            if name[0] == "." or (IS_WINDOWS and getattr(st, "st_file_attributes", 0) & _WIN_HIDDEN):
                f |= HIDDEN
            append((name, f, 0 if f & DIR else st.st_size, st.st_mtime))
    return out


def build_local(root: str, skip: Iterable[str] = (), workers: int = 1, token=None,
                progress: Optional[Callable[[int], None]] = None, now: float = 0.0) -> FileIndex:
    """Read everything under ``root`` from the local disk (blocking; run in a
    worker thread). ``skip``: folders not to enter (e.g. /proc, network mounts).
    One thread is fastest on local disks: the work is Python-bound, and more
    threads only take turns (measured: 1 thread 113k entries/s, 4 threads 36k/s).
    ``progress(count)`` is called now and then. Raises Cancelled."""
    import time as _time
    sep = "\\" if IS_WINDOWS else "/"
    skip_set = {os.path.normcase(s) for s in skip}
    dirs: List[str] = [root]
    names: List[str] = []
    parent = array("i")
    flags = bytearray()
    size = array("q")
    mtime = array("d")
    dir_id = array("i")
    pending = deque([0])
    busy = [0]
    cond = threading.Condition()
    lock = threading.Lock()
    last_report = [0]

    def work() -> None:
        while True:
            with cond:
                while not pending and busy[0] and not (token is not None and token.cancelled):
                    cond.wait(0.05)
                if (token is not None and token.cancelled) or (not pending and not busy[0]):
                    cond.notify_all()
                    return
                d = pending.popleft()
                busy[0] += 1
            folder = dirs[d]
            try:
                listing = _list_local(folder)
            except OSError:
                listing = []
            with lock:
                base = folder if folder.endswith(sep) else folder + sep
                new_dirs = []
                for name, fl, sz, mt in listing:
                    names.append(name)
                    parent.append(d)
                    flags.append(fl)
                    size.append(sz)
                    mtime.append(mt)
                    if fl & DIR and not fl & LINK:
                        child = base + name
                        if os.path.normcase(child) in skip_set:
                            dir_id.append(-1)
                            flags[-1] = fl            # keep it listed, just don't go inside
                            continue
                        sub = len(dirs)
                        dirs.append(child)
                        dir_id.append(sub)
                        new_dirs.append(sub)
                    else:
                        dir_id.append(-1)
                count = len(names)
            if progress is not None and count - last_report[0] >= 20000:
                last_report[0] = count
                progress(count)
            with cond:
                pending.extend(new_dirs)
                busy[0] -= 1
                cond.notify_all()

    threads = [threading.Thread(target=work, name=f"px-index-{k}", daemon=True) for k in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if token is not None and token.cancelled:
        raise Cancelled()
    return FileIndex.from_names(root, sep, dirs, names, parent, flags, size, mtime, dir_id, now or _time.time())


def build_from_entries(root: str, sep: str, walk: Callable[[Callable[[List[FileEntry]], None]], None],
                       now: float = 0.0) -> FileIndex:
    """Build an index from the app's own folder walker (for SFTP/SMB/FTP...).
    ``walk(emit)`` must call ``emit(batch_of_FileEntry)`` for everything below ``root``."""
    import time as _time
    dirs: List[str] = [root]
    dir_index = {root: 0, root.rstrip("/\\"): 0}
    rows: List[FileEntry] = []
    lock = threading.Lock()

    def emit(batch: List[FileEntry]) -> None:
        with lock:
            rows.extend(batch)

    walk(emit)
    # Folders first get ids (a folder can arrive after its children in a parallel walk).
    for e in rows:
        if e.is_dir and e.path not in dir_index:
            dir_index[e.path] = len(dirs)
            dirs.append(e.path)
    names: List[str] = []
    parent, flags, size, mtime, dir_id = array("i"), bytearray(), array("q"), array("d"), array("i")
    for e in rows:
        p = dir_index.get(e.parent)
        if p is None:
            p = dir_index.get(e.parent.rstrip("/\\"), 0)
        names.append(e.name)
        parent.append(p)
        f = (DIR if e.is_dir else 0) | (LINK if e.is_link else 0) | (HIDDEN if e.hidden or e.name.startswith(".") else 0)
        flags.append(f)
        size.append(0 if e.is_dir else max(0, int(e.size or 0)))
        mtime.append(float(e.mtime or 0))
        dir_id.append(dir_index.get(e.path, -1) if e.is_dir else -1)
    return FileIndex.from_names(root, sep, dirs, names, parent, flags, size, mtime, dir_id, now or _time.time())

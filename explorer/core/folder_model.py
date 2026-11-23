"""
FolderModel - the list of entries shown in one tab, kept sorted and filtered.

This is plain Python (no Qt) so it is easy to test and reuse. The views
only ever ask the model for ``visible`` - the final, sorted, filtered list.

Performance notes (this is what keeps 100k-file folders snappy):
* Every entry gets its sort key computed ONCE and stored in ``entry.sk``.
  Sorting then uses ``attrgetter('sk')``, which runs entirely in C.
* When a new batch streams in we append it and sort again. Python's sort
  (Timsort) spots the already-sorted part, so this is close to linear time.
* Hidden files are always kept in the model and simply skipped in
  ``visible``, so toggling "show hidden" is instant (no re-scan).
"""
from __future__ import annotations

import heapq
from operator import attrgetter
from typing import Iterable, List, Optional, Sequence

from .fileinfo import FileEntry
from .query import Query
from .sorting import key_func

_SK = attrgetter("sk")


def filter_entries(dirs: Sequence[FileEntry], files: Sequence[FileEntry], query: Optional[Query],
                   show_hidden: bool, folders_first: bool, reverse: bool) -> List[FileEntry]:
    """Pure function: combine + filter. Safe to call from a worker thread
    because it only reads the lists it is given."""
    def keep(seq: Sequence[FileEntry]) -> List[FileEntry]:
        if show_hidden and (query is None or query.is_empty):
            return list(seq)
        match = query.match if (query is not None and not query.is_empty) else None
        return [e for e in seq if (show_hidden or not e.hidden) and (match is None or match(e))]

    d, f = keep(dirs), keep(files)
    if folders_first:
        return d + f
    return list(heapq.merge(d, f, key=_SK, reverse=reverse))


class FolderModel:
    def __init__(self, sort_key: str = "name", reverse: bool = False,
                 folders_first: bool = True, show_hidden: bool = False) -> None:
        self.sort_key = sort_key
        self.reverse = reverse
        self.folders_first = folders_first
        self.show_hidden = show_hidden
        self.query: Optional[Query] = None
        self._dirs: List[FileEntry] = []
        self._files: List[FileEntry] = []
        self._visible: Optional[List[FileEntry]] = None
        self.generation = 0      # bumps on every change; lets async filters detect staleness

    # -- content -------------------------------------------------------------
    def reset(self, entries: Iterable[FileEntry] = ()) -> None:
        self._dirs, self._files = [], []
        self.add(entries)
        self._changed()

    def add(self, entries: Iterable[FileEntry]) -> None:
        keyf = key_func(self.sort_key)
        added = False
        for e in entries:
            e.sk = keyf(e)
            (self._dirs if e.is_dir else self._files).append(e)
            added = True
        if added:
            self._dirs.sort(key=_SK, reverse=self.reverse)
            self._files.sort(key=_SK, reverse=self.reverse)
            self._changed()

    def remove_paths(self, paths: Iterable[str]) -> None:
        gone = set(paths)
        self._dirs = [e for e in self._dirs if e.path not in gone]
        self._files = [e for e in self._files if e.path not in gone]
        self._changed()

    # -- options -------------------------------------------------------------
    def set_sort(self, sort_key: str, reverse: bool, folders_first: bool) -> None:
        self.sort_key, self.reverse, self.folders_first = sort_key, reverse, folders_first
        keyf = key_func(sort_key)
        for e in self._dirs:
            e.sk = keyf(e)
        for e in self._files:
            e.sk = keyf(e)
        self._dirs.sort(key=_SK, reverse=reverse)
        self._files.sort(key=_SK, reverse=reverse)
        self._changed()

    def set_query(self, query: Optional[Query]) -> None:
        self.query = query
        self._changed()

    def set_show_hidden(self, value: bool) -> None:
        self.show_hidden = value
        self._changed()

    # -- results -------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self._dirs) + len(self._files)

    @property
    def all_entries(self) -> List[FileEntry]:
        return self._dirs + self._files

    def snapshot(self):
        """Everything ``filter_entries`` needs, for running it in a thread."""
        return (list(self._dirs), list(self._files), self.query, self.show_hidden,
                self.folders_first, self.reverse)

    def set_visible(self, visible: List[FileEntry], generation: int) -> bool:
        """Install a result computed in a thread. Ignored if the model changed since."""
        if generation != self.generation:
            return False
        self._visible = visible
        return True

    @property
    def needs_filter(self) -> bool:
        return self._visible is None

    @property
    def visible(self) -> List[FileEntry]:
        if self._visible is None:
            self._visible = filter_entries(*self.snapshot())
        return self._visible

    def _changed(self) -> None:
        self._visible = None
        self.generation += 1

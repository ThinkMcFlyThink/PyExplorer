"""
Sort keys for file listings.

"Natural" sorting means "file2" comes before "file10" (a plain text sort
would put "file10" first). The key splits names into text and number
chunks: "file10.txt" -> ("file", 10, ".txt").
"""
from __future__ import annotations

import re
from typing import Callable, Dict

from .fileinfo import FileEntry

_DIGITS = re.compile(r"(\d+)")

SORT_KEYS = ("name", "size", "type", "modified")


def natural_key(name: str) -> tuple:
    # re.split with a capture group always alternates text, number, text...
    # so tuples compare str-with-str and int-with-int (never mixed).
    parts = _DIGITS.split(name.casefold())
    for i in range(1, len(parts), 2):
        parts[i] = int(parts[i])
    return tuple(parts)


def _nk(e: FileEntry) -> tuple:
    """The natural name key, computed once per entry and cached on it."""
    key = e.nk
    if key is None:
        key = e.nk = natural_key(e.name)
    return key


def _name(e: FileEntry):
    return _nk(e)


def _size(e: FileEntry):
    return (e.size, _nk(e))


def _type(e: FileEntry):
    return (e.type_label.casefold(), _nk(e))


def _modified(e: FileEntry):
    return (e.mtime, _nk(e))


def _location(e: FileEntry):
    return (natural_key(e.parent or ""), _nk(e))


KEY_FUNCS: Dict[str, Callable[[FileEntry], tuple]] = {
    "name": _name, "size": _size, "type": _type, "modified": _modified, "location": _location,
}


def key_func(sort_key: str) -> Callable[[FileEntry], tuple]:
    return KEY_FUNCS.get(sort_key, _name)

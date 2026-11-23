"""
bulk_rename - carry out a batch of renames safely (the Rename tab's "Rename" button).

    result = rename_all(vfs, [("/x/a.txt", "/x/b.txt"), ("/x/b.txt", "/x/a.txt")])
    result.done      -> [(old, new), ...]   what was renamed (for Undo)
    result.errors    -> [(old, message), ...]

The tricky cases a naive loop gets wrong:
  * swaps and chains: a -> b while b -> a (or b -> c). Renaming a to b first
    would overwrite b. So any rename whose target is still occupied by another
    item of the batch goes in two steps: first to a temporary name, then -
    once everything else has moved - to its final name;
  * case-only changes (photo.jpg -> Photo.jpg) on Windows/macOS, where those are
    "the same name": also done via a temporary name;
  * a failure half-way: an item that reached its temporary name but can't get
    its final one is put back under its old name.

Works on any file system the app supports (local, SMB, SFTP...), through ``vfs``.
Blocking - run it in a worker thread.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple


@dataclass
class RenameResult:
    done: List[Tuple[str, str]] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)


def _fold(path: str, case_insensitive: bool) -> str:
    return path.lower() if case_insensitive else path


def rename_all(vfs, pairs: Sequence[Tuple[str, str]], token=None,
               case_insensitive: bool = os.name == "nt" or os.name == "darwin") -> RenameResult:
    result = RenameResult()
    pairs = [(old, new) for old, new in pairs if old != new]
    sources = {_fold(old, case_insensitive) for old, _ in pairs}
    tag = uuid.uuid4().hex[:8]
    staged: List[Tuple[str, str, str]] = []       # (old, temporary, new)
    direct: List[Tuple[str, str]] = []

    # Step 1: anything whose target is taken by ANOTHER item of the batch (or that only
    # changes case) moves to a temporary name first.
    for k, (old, new) in enumerate(pairs):
        fs = vfs.resolve(old)
        target_taken = _fold(new, case_insensitive) in sources and _fold(new, case_insensitive) != _fold(old, case_insensitive)
        case_only = _fold(new, True) == _fold(old, True)
        if target_taken or (case_only and case_insensitive):
            parent = fs.parent(old) or ""
            temp = fs.join(parent, f".pyx-rename-{tag}-{k}")
            try:
                fs.rename(old, temp)
                staged.append((old, temp, new))
            except OSError as exc:
                result.errors.append((old, str(exc)))
        else:
            direct.append((old, new))

    # Step 2: the simple ones (their targets are free).
    for old, new in direct:
        if token is not None and token.cancelled:
            result.errors.append((old, "Cancelled"))
            continue
        fs = vfs.resolve(old)
        try:
            if fs.exists(new):
                raise FileExistsError(f"{fs.basename(new)} already exists")
            fs.rename(old, new)
            result.done.append((old, new))
        except OSError as exc:
            result.errors.append((old, str(exc)))

    # Step 3: the staged ones, now that their targets have moved away.
    for old, temp, new in staged:
        fs = vfs.resolve(old)
        try:
            if fs.exists(new) and not _fold(new, True) == _fold(old, True):
                raise FileExistsError(f"{fs.basename(new)} already exists")
            fs.rename(temp, new)
            result.done.append((old, new))
        except OSError as exc:
            try:
                fs.rename(temp, old)                  # put it back
            except OSError:
                exc = OSError(f"{exc} (left as {fs.basename(temp)})")
            result.errors.append((old, str(exc)))
    return result


def undo_pairs(done: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """The renames that reverse ``done`` (new -> old)."""
    return [(new, old) for old, new in reversed(done)]

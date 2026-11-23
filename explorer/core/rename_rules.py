"""
rename_rules - the Rename tab's rules, as plain data + pure functions.

    rules = RenameRules(replace_find="IMG_", replace_with="Holiday ", num_mode="suffix", num_pad=3)
    new_name("IMG_0042.JPG", False, 0, rules)       -> "Holiday 0042 001.JPG"
    plan(items, rules)                              -> [PlanRow(old, new, status, message), ...]

Like Bulk Rename Utility, the rules work on the name WITHOUT its extension
(the extension has its own rule) and are applied in this fixed order:

    1 Name        keep / remove / replace with fixed text / reverse
    2 Replace     find -> replace (optionally case-sensitive or a regular expression)
    3 Case        lower / UPPER / Title / Sentence / iNVERT
    4 Remove      first N, last N, characters From..To, crop before/after a text,
                  given characters, digits, trim, double spaces
    5 Move/Copy   move or copy the first/last N characters to the start / end / a position
    6 Add         prefix, insert at a position, suffix, spaces between words
    7 Numbering   1, 2, 3... (start, step, minimum digits, separator) as prefix / suffix / insert
    8 Extension   same / lower / UPPER / remove / new / extra

Positions count from 1. A negative position counts from the end (-1 = before
the last character). No Qt, no disk access: ``plan`` is told what already
exists through a callback, so all of it can be tested directly.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, fields
from typing import Callable, Dict, List, Optional, Sequence

IS_WINDOWS = os.name == "nt"
WINDOWS_BAD = '<>:"/\\|?*'
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


@dataclass
class RenameRules:
    # 1 Name
    name_mode: str = "keep"          # keep | remove | fixed | reverse
    name_text: str = ""
    # 2 Replace
    replace_find: str = ""
    replace_with: str = ""
    replace_case: bool = False
    replace_regex: bool = False
    # 3 Case
    case_mode: str = "same"          # same | lower | upper | title | sentence | invert
    # 4 Remove
    remove_first: int = 0
    remove_last: int = 0
    remove_from: int = 0             # 1-based, inclusive; 0 = off
    remove_to: int = 0
    crop_mode: str = "none"          # none | before | after  (remove everything before/after crop_text)
    crop_text: str = ""
    remove_chars: str = ""           # every one of these characters
    remove_digits: bool = False
    remove_trim: bool = False        # spaces, dots, dashes and underscores at both ends
    remove_double_spaces: bool = False
    # 5 Move / Copy
    move_mode: str = "none"          # none | move | copy
    move_what: str = "first"         # first | last
    move_count: int = 0
    move_to: str = "end"             # start | end | position
    move_pos: int = 1
    move_sep: str = ""
    # 6 Add
    add_prefix: str = ""
    add_insert: str = ""
    add_insert_pos: int = 1
    add_suffix: str = ""
    add_word_space: bool = False     # "HolidayPhotoRome" -> "Holiday Photo Rome"
    # 7 Numbering
    num_mode: str = "none"           # none | prefix | suffix | insert
    num_pos: int = 1
    num_start: int = 1
    num_step: int = 1
    num_pad: int = 1                 # minimum digits: 3 -> 001
    num_sep: str = " "
    # 8 Extension
    ext_mode: str = "same"           # same | lower | upper | title | remove | fixed | extra
    ext_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RenameRules":
        known = {f.name: f.type for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def is_identity(self) -> bool:
        return self == RenameRules()


# ---------------------------------------------------------------------------
# One name
# ---------------------------------------------------------------------------
def split_name(name: str, is_dir: bool) -> tuple:
    """("photo", "jpg") - folders and dot-files (".bashrc") have no extension."""
    if is_dir:
        return name, ""
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot + 1:]


def _position(text: str, pos: int) -> int:
    """1-based position -> index; negative counts from the end; clamped."""
    if pos > 0:
        return min(len(text), pos - 1)
    if pos < 0:
        return max(0, len(text) + pos)
    return 0


def _apply_case(text: str, mode: str) -> str:
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        # capitalise the first letter of every word; words are split by spaces, - _ . and (
        return re.sub(r"(^|[\s\-_.(\[])(\w)", lambda m: m.group(1) + m.group(2).upper(), text.lower())
    if mode == "sentence":
        lowered = text.lower()
        for i, ch in enumerate(lowered):
            if ch.isalpha():
                return lowered[:i] + ch.upper() + lowered[i + 1:]
        return lowered
    if mode == "invert":
        return text.swapcase()
    return text


def new_name(name: str, is_dir: bool, index: int, rules: RenameRules) -> str:
    """The name after all rules. ``index`` = 0-based position in the list (for numbering).
    Raises re.error for a bad Replace regular expression."""
    stem, ext = split_name(name, is_dir)
    r = rules

    # 1 Name
    if r.name_mode == "remove":
        stem = ""
    elif r.name_mode == "fixed":
        stem = r.name_text
    elif r.name_mode == "reverse":
        stem = stem[::-1]

    # 2 Replace
    if r.replace_find:
        if r.replace_regex:
            stem = re.sub(r.replace_find, r.replace_with, stem, flags=0 if r.replace_case else re.IGNORECASE)
        elif r.replace_case:
            stem = stem.replace(r.replace_find, r.replace_with)
        else:
            stem = re.sub(re.escape(r.replace_find), lambda m: r.replace_with, stem, flags=re.IGNORECASE)

    # 3 Case
    stem = _apply_case(stem, r.case_mode)

    # 4 Remove
    if r.remove_first > 0:
        stem = stem[r.remove_first:]
    if r.remove_last > 0:
        stem = stem[:-r.remove_last] if r.remove_last < len(stem) else ""
    if r.remove_from > 0:
        start = r.remove_from - 1
        end = r.remove_to if r.remove_to >= r.remove_from else r.remove_from
        stem = stem[:start] + stem[end:]
    if r.crop_mode in ("before", "after") and r.crop_text:
        at = stem.lower().find(r.crop_text.lower())
        if at >= 0:
            stem = stem[at:] if r.crop_mode == "before" else stem[:at + len(r.crop_text)]
    if r.remove_chars:
        stem = "".join(ch for ch in stem if ch not in r.remove_chars)
    if r.remove_digits:
        stem = "".join(ch for ch in stem if not ch.isdigit())
    if r.remove_double_spaces:
        stem = re.sub(r" {2,}", " ", stem)
    if r.remove_trim:
        stem = stem.strip(" ._-")

    # 5 Move / Copy
    if r.move_mode in ("move", "copy") and r.move_count > 0 and stem:
        n = min(r.move_count, len(stem))
        piece = stem[:n] if r.move_what == "first" else stem[-n:]
        rest = stem if r.move_mode == "copy" else (stem[n:] if r.move_what == "first" else stem[:-n])
        sep = r.move_sep
        if r.move_to == "start":
            stem = piece + sep + rest
        elif r.move_to == "end":
            stem = rest + sep + piece
        else:
            at = _position(rest, r.move_pos)
            stem = rest[:at] + sep + piece + sep + rest[at:] if sep else rest[:at] + piece + rest[at:]

    # 6 Add
    if r.add_word_space:
        stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", " ", stem)
    if r.add_insert:
        at = _position(stem, r.add_insert_pos)
        stem = stem[:at] + r.add_insert + stem[at:]
    stem = r.add_prefix + stem + r.add_suffix

    # 7 Numbering
    if r.num_mode in ("prefix", "suffix", "insert"):
        number = str(r.num_start + index * r.num_step)
        if number.startswith("-"):
            number = "-" + number[1:].zfill(max(1, r.num_pad))
        else:
            number = number.zfill(max(1, r.num_pad))
        sep = r.num_sep
        if r.num_mode == "prefix":
            stem = number + (sep if stem else "") + stem
        elif r.num_mode == "suffix":
            stem = stem + (sep if stem else "") + number
        else:
            at = _position(stem, r.num_pos)
            stem = stem[:at] + number + stem[at:]

    # 8 Extension
    if r.ext_mode == "lower":
        ext = ext.lower()
    elif r.ext_mode == "upper":
        ext = ext.upper()
    elif r.ext_mode == "title":
        ext = ext[:1].upper() + ext[1:].lower()
    elif r.ext_mode == "remove":
        ext = ""
    elif r.ext_mode == "fixed":
        ext = r.ext_text.strip().lstrip(".")
    elif r.ext_mode == "extra" and r.ext_text.strip().lstrip("."):
        ext = (ext + "." if ext else "") + r.ext_text.strip().lstrip(".")

    return stem + ("." + ext if ext else "")


# ---------------------------------------------------------------------------
# A whole batch: new names + what would go wrong
# ---------------------------------------------------------------------------
@dataclass
class RenameItem:
    path: str
    name: str
    parent: str
    is_dir: bool = False
    checked: bool = True


@dataclass
class PlanRow:
    item: RenameItem
    new: str                 # the new NAME (same as item.name if unchanged / unchecked)
    status: str              # "rename" | "same" | "skip" (unchecked) | "error"
    message: str = ""

    @property
    def changes(self) -> bool:
        return self.status == "rename"


def name_problem(name: str, windows: bool = IS_WINDOWS) -> Optional[str]:
    """Why ``name`` can't be used as a file name, or None."""
    if not name:
        return "The new name would be empty"
    if name in (".", ".."):
        return "Not a valid name"
    if "/" in name or "\0" in name:
        return "Names can't contain /"
    if windows:
        bad = [c for c in WINDOWS_BAD if c in name]
        if bad:
            return f"Names can't contain {' '.join(bad)}"
        if name.endswith((" ", ".")):
            return "Names can't end with a space or a dot"
        if name.split(".")[0].lower() in WINDOWS_RESERVED:
            return f"{name.split('.')[0]} is a reserved name"
    if len(name.encode("utf-8")) > 255:
        return "The new name is too long"
    return None


def plan(items: Sequence[RenameItem], rules: RenameRules, exists: Callable[[str, str], bool] = lambda p, n: False,
         case_insensitive: bool = IS_WINDOWS or os.name == "darwin") -> List[PlanRow]:
    """New names for every item, in order (numbering counts CHECKED items only),
    with each row's status. ``exists(parent, name)`` tells whether a name is
    already taken on disk; names taken by items of this batch that are being
    renamed away don't count. Raises re.error for a bad Replace regex."""
    def key(parent: str, name: str) -> tuple:
        return parent, name.lower() if case_insensitive else name

    rows: List[PlanRow] = []
    number = 0
    for item in items:
        if not item.checked:
            rows.append(PlanRow(item, item.name, "skip"))
            continue
        target = new_name(item.name, item.is_dir, number, rules)
        number += 1
        if target == item.name:
            rows.append(PlanRow(item, target, "same"))
            continue
        problem = name_problem(target)
        rows.append(PlanRow(item, target, "error" if problem else "rename", problem or ""))

    # Names that will be free once the batch has moved them away ... and names that stay.
    leaving = {key(r.item.parent, r.item.name) for r in rows if r.status == "rename"}
    staying: Dict[tuple, PlanRow] = {key(r.item.parent, r.item.name): r for r in rows if r.status != "rename"}
    targets: Dict[tuple, PlanRow] = {}
    for row in rows:
        if row.status != "rename":
            continue
        k = key(row.item.parent, row.new)
        if k in targets:
            other = targets[k]
            row.status, row.message = "error", f"Same new name as {other.item.name}"
            if other.status == "rename":
                other.status, other.message = "error", f"Same new name as {row.item.name}"
            continue
        targets[k] = row
        if k in staying and staying[k] is not row:
            row.status, row.message = "error", f"{staying[k].item.name} already has this name"
        elif k not in leaving and k != key(row.item.parent, row.item.name) and exists(row.item.parent, row.new):
            # (a change of case only, e.g. a.txt -> A.txt, is fine even where names ignore case)
            row.status, row.message = "error", "A file with this name already exists"
    return rows

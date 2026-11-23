"""
Query language for the instant filter and for search (inspired by "Everything").

    holiday photo        both words must appear in the name (AND)
    jpg|png              either word (OR)
    !thumb               must NOT contain "thumb"
    "two words"          exact phrase including the space
    *.py  report_??.doc  wildcards: match the WHOLE name
    ext:jpg;png          extension is one of these
    file:  folder:       only files / only folders  (also type:file, type:folder)
    size:>10mb  size:<1k  size:1mb..50mb
    dm:today  dm:7d  dm:>2024-01-31  dm:<2023-01-01   (date modified)
    path:music           match against the full path instead of the name
    regex:^IMG_\\d+      regular expression on the name
    case:                make the following terms case-sensitive

``compile_query(text)`` turns the text into a ``Query`` object whose
``match(entry)`` is fast enough to run over millions of entries.
"""
from __future__ import annotations

import fnmatch
import re
import shlex
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .fileinfo import FileEntry

Predicate = Callable[[FileEntry, str], bool]   # (entry, name_lower) -> bool

_SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2,
               "g": 1024 ** 3, "gb": 1024 ** 3, "t": 1024 ** 4, "tb": 1024 ** 4}


QUERY_HELP = [
    ("word1 word2", "both words (AND)"), ("a|b", "either word (OR)"), ("!word", "exclude"),
    ('"two words"', "exact phrase"), ("*.py  img_??", "wildcards"), ("ext:jpg;png", "extensions"),
    ("file:  folder:", "only files / folders"), ("size:>10mb", "size (also <, .., kb/gb)"),
    ("dm:today  dm:7d", "date modified (also dm:>2024-01-31)"), ("path:music", "match the full path"),
    ("regex:^IMG_\\d+", "regular expression"),
]   # (example, meaning) - shown by "?" buttons


class QueryError(ValueError):
    pass


def _parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]*)\s*", text)
    if not m or m.group(2).lower() not in _SIZE_UNITS:
        raise QueryError(f"Bad size: {text!r}")
    return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).lower()])


def _compare(op: str, limit: float) -> Callable[[float], bool]:
    return {
        ">": lambda v: v > limit, ">=": lambda v: v >= limit,
        "<": lambda v: v < limit, "<=": lambda v: v <= limit,
        "=": lambda v: v == limit, "": lambda v: v == limit,
    }[op]


def _size_predicate(arg: str) -> Predicate:
    if ".." in arg:
        lo, hi = arg.split("..", 1)
        lo_v, hi_v = _parse_size(lo), _parse_size(hi)
        return lambda e, n: (not e.is_dir) and lo_v <= e.size <= hi_v
    m = re.fullmatch(r"(>=|<=|>|<|=)?(.*)", arg)
    test = _compare(m.group(1) or "=", _parse_size(m.group(2)))
    return lambda e, n: (not e.is_dir) and test(e.size)


def _date_predicate(arg: str) -> Predicate:
    arg = arg.strip().lower()
    now = time.time()
    if arg == "today":
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return lambda e, n: e.mtime >= start
    m = re.fullmatch(r"(\d+)\s*([dwmy])", arg)
    if m:
        days = int(m.group(1)) * {"d": 1, "w": 7, "m": 30, "y": 365}[m.group(2)]
        start = now - days * 86400
        return lambda e, n: e.mtime >= start
    m = re.fullmatch(r"(>=|<=|>|<|=)?(\d{4}-\d{2}-\d{2})", arg)
    if m:
        stamp = datetime.strptime(m.group(2), "%Y-%m-%d").timestamp()
        op = m.group(1) or "="
        if op == "=":
            return lambda e, n: stamp <= e.mtime < stamp + 86400
        test = _compare(op, stamp)
        return lambda e, n: test(e.mtime)
    raise QueryError(f"Bad date: {arg!r} (try today, 7d, >2024-01-31)")


def _text_predicate(term: str, case: bool, on_path: bool) -> Predicate:
    """One plain search word, possibly with wildcards and | alternatives."""
    alternatives = [t for t in term.split("|") if t] or [term]
    tests: List[Callable[[str], bool]] = []
    for alt in alternatives:
        needle = alt if case else alt.lower()
        if "*" in alt or "?" in alt:
            regex = re.compile(fnmatch.translate(needle), 0 if case else re.IGNORECASE)
            tests.append(regex.match)
        else:
            tests.append(lambda hay, needle=needle: needle in hay)

    if on_path:
        def pred(e: FileEntry, n: str) -> bool:
            hay = e.path if case else e.path.lower()
            return any(t(hay) for t in tests)
    elif case:
        def pred(e: FileEntry, n: str) -> bool:
            return any(t(e.name) for t in tests)
    else:
        def pred(e: FileEntry, n: str) -> bool:
            return any(t(n) for t in tests)
    return pred


class Query:
    """A compiled query. ``match(entry)`` -> bool."""

    def __init__(self, text: str, predicates: List[Predicate], needs_stat: bool = False) -> None:
        self.text = text
        self._predicates = predicates
        self.needs_stat = needs_stat      # uses size:/dm: -> entries need their size/date first

    @property
    def is_empty(self) -> bool:
        return not self._predicates

    def match(self, entry: FileEntry) -> bool:
        name_lower = entry.name.lower()
        for pred in self._predicates:
            if not pred(entry, name_lower):
                return False
        return True


def _split(text: str) -> List[str]:
    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        lexer.escape = ""          # keep backslashes (Windows paths, regex)
        return list(lexer)
    except ValueError:             # unbalanced quotes while the user is still typing
        return text.split()


def compile_query(text: str, case_sensitive: bool = False) -> Query:
    """Parse ``text`` (see module docstring). Raises QueryError on bad input."""
    predicates: List[Predicate] = []
    needs_stat = False
    case = case_sensitive
    for raw in _split(text or ""):
        negate = raw.startswith("!") and len(raw) > 1
        token = raw[1:] if negate else raw
        lower = token.lower()
        pred: Optional[Predicate] = None

        if lower == "case:":
            case = True
            continue
        if lower in ("file:", "type:file"):
            pred = lambda e, n: not e.is_dir
        elif lower in ("folder:", "type:folder", "dir:"):
            pred = lambda e, n: e.is_dir
        elif lower.startswith("ext:"):
            exts = {x.strip(". ").lower() for x in re.split(r"[;,|]", token[4:]) if x.strip()}
            pred = lambda e, n, exts=exts: e.ext in exts
        elif lower.startswith("size:"):
            pred = _size_predicate(token[5:])
            needs_stat = True
        elif lower.startswith(("dm:", "datemodified:", "modified:")):
            pred = _date_predicate(token.split(":", 1)[1])
            needs_stat = True
        elif lower.startswith("path:"):
            pred = _text_predicate(token[5:], case, on_path=True)
        elif lower.startswith("regex:"):
            try:
                rx = re.compile(token[6:], 0 if case else re.IGNORECASE)
            except re.error as exc:
                raise QueryError(f"Bad regex: {exc}") from None
            pred = lambda e, n, rx=rx: rx.search(e.name) is not None
        elif token:
            pred = _text_predicate(token, case, on_path=False)

        if pred is None:
            continue
        if negate:
            pred = (lambda p: (lambda e, n: not p(e, n)))(pred)
        predicates.append(pred)
    return Query(text, predicates, needs_stat)


def safe_compile(text: str, case_sensitive: bool = False) -> Query:
    """Like compile_query but falls back to a plain substring match instead
    of raising - used by the instant filter while the user is mid-typing."""
    try:
        return compile_query(text, case_sensitive)
    except QueryError:
        needle = (text or "").strip().lower()
        return Query(text, [lambda e, n: needle in n] if needle else [])


def scan_terms(text: str, case_sensitive: bool = False) -> Tuple[List[Tuple[str, str, bool]], bool]:
    """What every matching NAME must contain, for the search index's fast scan
    (services/file_index.py runs these over all names at once, in C).

    Returns ``(terms, exact)``. Each term is ``(kind, value, case_sensitive)``
    with kind "literal" | "wildcard" | "ext" | "alternatives" | "regex".
    ``exact`` is True when the terms say everything the query says - then the
    scan's hits ARE the results and the full ``Query.match`` can be skipped.
    Terms that can't be scanned for (negation, size:, dm:, path:, file:...)
    make ``exact`` False; they are checked afterwards on the candidates."""
    terms: List[Tuple[str, str, bool]] = []
    exact = True
    case = case_sensitive
    for raw in _split(text or ""):
        negate = raw.startswith("!") and len(raw) > 1
        token = raw[1:] if negate else raw
        lower = token.lower()
        if lower == "case:":
            case = True
            continue
        if negate or not token:
            exact = False
            continue
        if lower in ("file:", "type:file", "folder:", "type:folder", "dir:") or \
                lower.startswith(("size:", "dm:", "datemodified:", "modified:", "path:")):
            exact = False
            continue
        if lower.startswith("ext:"):
            exts = [x.strip(". ") for x in re.split(r"[;,|]", token[4:]) if x.strip(". ")]
            if exts:
                terms.append(("ext", ";".join(exts), False))     # extensions never care about case
            exact = False                       # ext: never matches folders; checked afterwards
            continue
        if lower.startswith("regex:"):
            try:
                re.compile(token[6:])
            except re.error as exc:
                raise QueryError(f"Bad regex: {exc}") from None
            terms.append(("regex", token[6:], case))
            exact = False                       # a regex could span names in the big string
            continue
        if "[" in token:                        # fnmatch [abc] sets: let Query.match decide
            exact = False
        alternatives = [t for t in token.split("|") if t]
        if len(alternatives) > 1:
            terms.append(("alternatives", "|".join(alternatives), case))
        elif "*" in token or "?" in token:
            terms.append(("wildcard", token, case))
        else:
            terms.append(("literal", token, case))
    return terms, exact

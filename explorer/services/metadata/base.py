"""
The building blocks of the Info panel: what a provider returns, and the
provider base class.

A *provider* knows one family of files (videos, EPUB books, archives...).
It runs in a worker thread and returns plain data - an ``Info`` made of
``Section`` objects (titled lists of label/value rows) and, optionally, a
preview image. It never touches widgets, so providers are easy to test and
cannot freeze the window.

Adding support for a new kind of file:

    class MyProvider(InfoProvider):
        name = "My format"
        exts = {"abc"}
        def extract(self, src):
            info = Info(kind="ABC file")
            info.section("Details").add("Version", read_version(src.path))
            return info

    registry.register(MyProvider())

That is all: the panel finds it by extension and draws whatever it returns.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable, Dict, FrozenSet, List, Optional, Tuple, Union

# A preview is one of:
#   bytes                     - an encoded image (JPEG, PNG...) already in memory
#   str                       - the path of an image file on disk
#   callable(token) -> bytes|QImage|None
#                             - produced later, in a worker, after the text is shown
#                               (grabbing a video frame, rendering a PDF page...).
#                               ``token`` is a CancelToken: pass it to tools.run().
Preview = Union[bytes, str, Callable[[Any], Any], None]


@dataclass
class Section:
    """A titled block of the panel: rows of (label, value), or free text."""
    title: str
    rows: List[Tuple[str, str]] = field(default_factory=list)
    text: str = ""              # a paragraph instead of / after the rows (a book's description)
    mono: bool = False          # ``text`` is code / a file excerpt: fixed-width, no wrapping
    key: str = ""               # remembers folded/unfolded; default: the title without " #2"

    def add(self, label: str, value: Any) -> "Section":
        """Add a row. Empty values (None, "", [], 0-length) are skipped, so
        providers can add every field they know without checking each one."""
        if value is None or value == "" or value == [] or value == ():
            return self
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value if v not in (None, ""))
            if not value:
                return self
        self.rows.append((label, str(value)))
        return self

    @property
    def fold_key(self) -> str:
        return self.key or self.title.split(" #")[0]

    def __bool__(self) -> bool:
        return bool(self.rows or self.text)


@dataclass
class Info:
    """Everything the panel shows about one item, below its name."""
    kind: str = ""                              # the line under the name: "Video · Matroska"
    sections: List[Section] = field(default_factory=list)
    preview: Preview = None
    note: str = ""                              # a hint: "Install ffprobe for video details"
    extra: Dict[str, Any] = field(default_factory=dict)   # for the panel: {"font_file": path}

    def section(self, title: str, **kwargs) -> Section:
        s = Section(title, **kwargs)
        self.sections.append(s)
        return s

    def merge(self, other: Optional["Info"]) -> "Info":
        """Add another provider's findings after ours."""
        if other is None:
            return self
        self.kind = other.kind or self.kind
        self.sections.extend(other.sections)
        self.preview = other.preview if other.preview is not None else self.preview
        self.note = other.note or self.note
        self.extra.update(other.extra)
        return self

    def as_text(self) -> str:
        """Plain text of every section - for "Copy details"."""
        out = []
        for s in self.sections:
            if not s:
                continue
            out.append(f"[{s.title}]")
            width = max((len(k) for k, _ in s.rows), default=0)
            out.extend(f"{k.ljust(width)}  {v}" for k, v in s.rows)
            if s.text:
                out.append(s.text)
            out.append("")
        return "\n".join(out).rstrip() + "\n"


class Source:
    """The file a provider is asked about, plus everything it may need."""

    def __init__(self, entry, fs, token=None, options: Optional[Dict[str, Any]] = None) -> None:
        self.entry = entry
        self.path: str = entry.path
        self.fs = fs
        self.token = token
        self.options = options or {}
        self.local: bool = bool(getattr(fs, "is_local", False))

    def check(self) -> None:
        """Raise ``Cancelled`` if the user has already moved on to another file."""
        if self.token is not None:
            self.token.check()

    @property
    def cancelled(self) -> bool:
        return bool(self.token is not None and self.token.cancelled)

    def open(self) -> BinaryIO:
        if self.local:
            return open(self.path, "rb")
        return self.fs.open_read(self.path)

    def head(self, size: int) -> bytes:
        """The first ``size`` bytes (cheap even for remote files)."""
        with self.open() as fh:
            return fh.read(size)

    def seekable(self) -> BinaryIO:
        """A file object that supports seek (zipfile needs one). Remote
        streams that can't seek are read into memory - only for small files."""
        fh = self.open()
        try:
            if fh.seekable():
                return fh
        except (AttributeError, OSError):
            pass
        data = fh.read()
        fh.close()
        return io.BytesIO(data)

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


class InfoProvider:
    """Base class. Override ``extract``; the rest are hints for the registry."""
    name: str = ""
    exts: FrozenSet[str] = frozenset()      # lower-case extensions this provider understands
    priority: int = 50                      # higher runs first when several match
    local_only: bool = True                 # needs a real path (external tools, QtPdf...)
    max_remote_size: int = 32 * 1024 * 1024  # remote files bigger than this are skipped

    def matches(self, entry) -> bool:
        return not entry.is_dir and entry.ext in self.exts

    def available(self) -> bool:
        """False when a library / program it needs is missing (it is then skipped)."""
        return True

    def missing_hint(self) -> str:
        """Shown when ``available()`` is False: how to get the extra details."""
        return ""

    def extract(self, src: Source) -> Optional[Info]:
        """Return an ``Info``, or None to let the next provider try."""
        raise NotImplementedError

"""
Text, code and data files: encoding, line endings, line / word counts, the
language, and the first lines as a preview. A few formats get more:

    CSV / TSV    columns, rows, the delimiter
    JSON         valid or not (and where it breaks), what the top level holds
    SRT / VTT    number of subtitles and how long they run
    Markdown     number of headings

Files with an unknown extension are sniffed: if the start looks like text,
they're treated as text; otherwise this provider steps aside.
"""
from __future__ import annotations

import codecs
import csv
import io
import json
import re
from typing import Optional, Tuple

from ...core.fileinfo import EXT_CATEGORY
from . import tools
from .base import Info, InfoProvider, Source

HEAD_BYTES = 64 * 1024
COUNT_LIMIT = 256 * 1024 * 1024       # count lines of local files up to this size
WORDS_LIMIT = 4 * 1024 * 1024         # count words up to this size
JSON_LIMIT = 8 * 1024 * 1024
LINE_CLIP = 240

LANGUAGES = {
    "py": "Python", "pyw": "Python", "pyi": "Python stub", "js": "JavaScript", "mjs": "JavaScript",
    "cjs": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript (JSX)", "jsx": "JavaScript (JSX)", "java": "Java",
    "c": "C", "h": "C header", "cpp": "C++", "cc": "C++", "cxx": "C++", "hpp": "C++ header", "cs": "C#",
    "go": "Go", "rs": "Rust", "rb": "Ruby", "php": "PHP", "swift": "Swift", "kt": "Kotlin", "kts": "Kotlin script",
    "lua": "Lua", "sh": "Shell script", "bash": "Bash script", "zsh": "Zsh script", "fish": "Fish script",
    "ps1": "PowerShell", "bat": "Batch file", "cmd": "Batch file", "pl": "Perl", "r": "R", "scala": "Scala",
    "dart": "Dart", "html": "HTML", "htm": "HTML", "css": "CSS", "scss": "SCSS", "sass": "Sass", "less": "Less",
    "sql": "SQL", "json": "JSON", "jsonc": "JSON with comments", "yaml": "YAML", "yml": "YAML", "toml": "TOML",
    "xml": "XML", "ini": "INI", "cfg": "Config", "conf": "Config", "gradle": "Gradle", "md": "Markdown",
    "markdown": "Markdown", "rst": "reStructuredText", "tex": "LaTeX", "bib": "BibTeX", "csv": "CSV", "tsv": "TSV",
    "srt": "SubRip subtitles", "vtt": "WebVTT subtitles", "ass": "ASS subtitles", "ssa": "SSA subtitles",
    "vue": "Vue", "svelte": "Svelte", "ex": "Elixir", "exs": "Elixir", "erl": "Erlang", "hs": "Haskell",
    "ml": "OCaml", "clj": "Clojure", "jl": "Julia", "m": "Objective-C / MATLAB", "zig": "Zig", "nim": "Nim",
    "v": "V / Verilog", "asm": "Assembly", "s": "Assembly", "make": "Makefile", "cmake": "CMake",
    "dockerfile": "Dockerfile", "tf": "Terraform", "proto": "Protocol Buffers", "graphql": "GraphQL",
    "org": "Org mode", "adoc": "AsciiDoc", "log": "Log", "txt": "Plain text", "nfo": "NFO", "diff": "Diff",
    "patch": "Patch", "env": "Environment", "properties": "Properties", "desktop": "Desktop entry",
    "service": "systemd unit", "ipynb": "Jupyter notebook",
}
SPECIAL_NAMES = {"makefile": "Makefile", "dockerfile": "Dockerfile", "cmakelists.txt": "CMake", "readme": "Plain text",
                 "license": "Plain text", "copying": "Plain text", "gemfile": "Ruby", "rakefile": "Ruby",
                 "vagrantfile": "Ruby", "justfile": "Just", ".gitignore": "Git ignore", ".bashrc": "Bash script",
                 ".zshrc": "Zsh script", ".profile": "Shell script", ".editorconfig": "EditorConfig"}
TEXT_EXTS = {e for e, c in EXT_CATEGORY.items() if c in ("text", "code")} | set(LANGUAGES) | {
    "csv", "tsv", "srt", "vtt", "ass", "ssa", "sub", "rst", "adoc", "org", "tex", "bib", "lst", "manifest", "pem",
    "crt", "key", "pub", "asc", "gpg", "rtf", "svg", "plist", "reg", "inf", "url", "m3u", "m3u8", "pls", "cue",
    "strings", "po", "pot", "lock"}
SNIFF_LIMIT = 16 * 1024 * 1024        # unknown extensions: only sniff files up to this size

_BOMS = ((codecs.BOM_UTF32_LE, "UTF-32 LE", "utf-32"), (codecs.BOM_UTF32_BE, "UTF-32 BE", "utf-32"),
         (codecs.BOM_UTF8, "UTF-8 (with BOM)", "utf-8-sig"), (codecs.BOM_UTF16_LE, "UTF-16 LE", "utf-16"),
         (codecs.BOM_UTF16_BE, "UTF-16 BE", "utf-16"))
_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)\s*-->")
_TIME_SHORT = re.compile(r"(?<![:\d])(\d\d):(\d\d)[.](\d+)\s*-->")


def detect_encoding(head: bytes, truncated: bool) -> Optional[Tuple[str, str]]:
    """(label, codec) - or None when this looks like a binary file."""
    for bom, label, codec in _BOMS:
        if head.startswith(bom):
            return label, codec
    if b"\0" in head:
        return None
    try:
        head.decode("utf-8")
        return ("ASCII" if head.isascii() else "UTF-8"), "utf-8"
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(head) - 3:          # the sample cut a character in half
            return "UTF-8", "utf-8"
    # Not UTF-8: plain 8-bit text has few control characters.
    controls = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13, 27))
    if controls > len(head) // 100:
        return None
    return "8-bit (likely Windows-1252 / Latin-1)", "cp1252"


def line_endings(head: bytes) -> str:
    crlf = head.count(b"\r\n")
    lf = head.count(b"\n") - crlf
    cr = head.count(b"\r") - crlf
    kinds = [name for name, n in (("Windows (CRLF)", crlf), ("Unix (LF)", lf), ("old Mac (CR)", cr)) if n]
    if not kinds:
        return ""
    return kinds[0] if len(kinds) == 1 else "Mixed: " + ", ".join(kinds)


class TextProvider(InfoProvider):
    name = "Text and code"
    exts = frozenset(TEXT_EXTS)
    priority = 10                       # last resort: anything that turns out to be text
    local_only = False

    def matches(self, entry) -> bool:
        if entry.is_dir:
            return False
        if entry.ext in self.exts or entry.name.lower() in SPECIAL_NAMES:
            return True
        return entry.category == "other" and entry.size <= SNIFF_LIMIT

    def extract(self, src: Source) -> Optional[Info]:
        e = src.entry
        head = src.head(HEAD_BYTES)
        truncated = e.size > len(head)
        enc = detect_encoding(head, truncated)
        if enc is None:
            return None
        label, codec = enc
        lang = LANGUAGES.get(e.ext) or SPECIAL_NAMES.get(e.name.lower(), "")
        if head.startswith(b"#!"):
            shebang = head[2:head.find(b"\n") if b"\n" in head else 200].decode("utf-8", "replace").strip()
            parts = shebang.split()
            prog = parts[0].rsplit("/", 1)[-1] if parts else ""
            if prog == "env" and len(parts) > 1:                  # "#!/usr/bin/env python3"
                prog = parts[-1]
            lang = lang or {"python3": "Python", "python": "Python", "bash": "Bash script", "sh": "Shell script",
                            "node": "JavaScript", "perl": "Perl", "ruby": "Ruby", "zsh": "Zsh script"}.get(prog, "")
            lang = lang + (f"  (#!{shebang})" if shebang else "")
        info = Info(kind=lang.split("  (")[0] if lang else "Text file")
        s = info.section("Text")
        s.add("Language", lang if lang and lang not in ("Plain text",) else "")
        s.add("Encoding", label)
        s.add("Line endings", line_endings(head))
        text_head = head.decode(codec, "replace")

        whole: Optional[bytes] = head if not truncated else None
        lines = None
        if whole is not None:
            lines = whole.count(b"\n") + (0 if whole.endswith(b"\n") or not whole else 1)
        elif src.local and e.size <= COUNT_LIMIT and codec in ("utf-8", "utf-8-sig", "cp1252"):
            lines, whole = self._count_lines(src)
        if lines is not None:
            s.add("Lines", f"{lines:,}")
        if whole is not None and len(whole) <= WORDS_LIMIT:
            text = whole.decode(codec, "replace")
            s.add("Words", f"{len(text.split()):,}")
            s.add("Characters", f"{len(text):,}")
        elif whole is None and lines is None:                     # remote or huge: estimate from the sample
            per_line = max(len(head) / max(head.count(b"\n"), 1), 1)
            s.add("Lines", f"≈ {round(e.size / per_line):,} (estimated)")

        ext = e.ext
        if ext in ("csv", "tsv"):
            self._csv(info, text_head, lines, ext)
        elif ext in ("json", "geojson", "ipynb"):
            self._json(info, src, codec)
        elif ext in ("srt", "vtt"):
            self._subtitles(info, whole.decode(codec, "replace") if whole is not None else text_head, whole is None)
        elif ext in ("md", "markdown"):
            body = whole.decode(codec, "replace") if whole is not None else text_head
            heads = len(re.findall(r"(?m)^#{1,6}\s", body))
            s.add("Headings", f"{heads:,}" if heads else "")

        n = int(src.option("text_lines", 40) or 0)
        if n > 0:
            preview_lines = text_head.splitlines()[:n]
            if preview_lines:
                clipped = [ln if len(ln) <= LINE_CLIP else ln[:LINE_CLIP] + " …" for ln in preview_lines]
                info.section("Preview", text="\n".join(clipped).expandtabs(4), mono=True)
        return info

    @staticmethod
    def _count_lines(src: Source):
        count = 0
        keep = bytearray() if src.entry.size <= WORDS_LIMIT else None
        last = b""
        with open(src.path, "rb") as fh:
            while True:
                src.check()
                block = fh.read(4 * 1024 * 1024)
                if not block:
                    break
                count += block.count(b"\n")
                last = block[-1:]
                if keep is not None:
                    keep += block
        if last and last != b"\n":
            count += 1
        return count, (bytes(keep) if keep is not None else None)

    @staticmethod
    def _csv(info: Info, text: str, lines: Optional[int], ext: str) -> None:
        s = info.section("Table")
        sample = text[:32768]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|") if ext == "csv" else csv.excel_tab
        except csv.Error:
            dialect = csv.excel
        try:
            rows = list(csv.reader(io.StringIO(sample), dialect))
        except csv.Error:
            return
        if not rows:
            return
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = True
        names = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
        s.add("Delimiter", names.get(dialect.delimiter, repr(dialect.delimiter)))
        s.add("Columns", f"{len(rows[0]):,}")
        if lines is not None:
            s.add("Rows", f"{max(lines - (1 if has_header else 0), 0):,}" + (" (plus a header row)" if has_header else ""))
        if has_header:
            s.add("Header", tools.clip(", ".join(rows[0]), 300))

    @staticmethod
    def _json(info: Info, src: Source, codec: str) -> None:
        s = info.section("JSON")
        if src.entry.size > JSON_LIMIT or (not src.local and src.entry.size > HEAD_BYTES):
            return
        with src.open() as fh:
            raw = fh.read()
        try:
            data = json.loads(raw.decode(codec, "replace"))
        except ValueError as exc:
            s.add("Valid", f"No — {getattr(exc, 'msg', str(exc))} (line {getattr(exc, 'lineno', '?')}, "
                           f"column {getattr(exc, 'colno', '?')})")
            return
        s.add("Valid", "Yes")
        if isinstance(data, dict):
            s.add("Top level", f"object with {len(data):,} keys")
            s.add("Keys", tools.clip(", ".join(list(map(str, data))[:30]), 300))
            if src.entry.ext == "ipynb" and isinstance(data.get("cells"), list):
                cells = data["cells"]
                code = sum(1 for c in cells if isinstance(c, dict) and c.get("cell_type") == "code")
                s.add("Cells", f"{len(cells):,} ({code:,} code, {len(cells) - code:,} other)")
                kernel = ((data.get("metadata") or {}).get("kernelspec") or {}).get("display_name")
                s.add("Kernel", kernel)
        elif isinstance(data, list):
            s.add("Top level", f"array of {len(data):,} items")
        else:
            s.add("Top level", type(data).__name__)

    @staticmethod
    def _subtitles(info: Info, text: str, partial: bool) -> None:
        s = info.section("Subtitles")
        cues = text.count("-->")
        s.add("Cues", f"{cues:,}{'+' if partial else ''}" if cues else "")
        last = None
        for m in _TIME.finditer(text):
            last = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if last is None:
            for m in _TIME_SHORT.finditer(text):
                last = int(m.group(1)) * 60 + int(m.group(2))
        if last is not None and not partial:
            s.add("Runs until", tools.duration(last))

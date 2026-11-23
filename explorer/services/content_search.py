"""
content_search - does a file contain some text? (the Search tab's "Containing")

    matcher = ContentMatcher("invoice 2024", case_sensitive=False)
    snippet = matcher.match_local("/home/me/notes.txt")   # None, or the matching line

Fast path for local files: the file is memory-mapped and ONE regular
expression runs over it in C - no reading line by line in Python. Remote
files (SFTP, SMB...) are read in chunks through the app's file systems.

Text is matched as UTF-8 bytes. Case-insensitive matching also handles
accented letters in what you type (é matches É). Files that look binary (a
zero byte near the start) are skipped unless asked otherwise - they are
rarely what you want and can be huge.
"""
from __future__ import annotations

import mmap
import os
import re
from typing import Optional

SNIFF = 4096                      # how much of the start is checked for "binary"
CHUNK = 1024 * 1024               # remote files are read this much at a time
SNIPPET = 120                     # characters of context shown in the results


class ContentMatcher:
    def __init__(self, text: str, case_sensitive: bool = False, regex: bool = False,
                 whole_word: bool = False, max_bytes: int = 200 * 1024 * 1024,
                 include_binary: bool = False) -> None:
        self.max_bytes = max_bytes
        self.include_binary = include_binary
        if regex:
            body = text.encode("utf-8")
            flags = 0 if case_sensitive else re.IGNORECASE
        else:
            body = _literal_bytes(text, case_sensitive)
            flags = 0
        if whole_word:
            body = rb"(?<![A-Za-z0-9_])(?:" + body + rb")(?![A-Za-z0-9_])"
        self.regex = re.compile(body, flags | re.MULTILINE)   # raises re.error on a bad regex
        # how much of the previous chunk to keep when reading remote files in chunks
        self.overlap = max(256, len(text.encode("utf-8")) * 4)

    # -- local files ------------------------------------------------------------
    def match_local(self, path: str) -> Optional[str]:
        """The matching line (shortened), or None. Never raises."""
        try:
            size = os.path.getsize(path)
            if size == 0 or size > self.max_bytes:
                return None
            with open(path, "rb") as fh:
                if not self.include_binary and b"\0" in fh.read(SNIFF):
                    return None
                with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    m = self.regex.search(mm)
                    return _snippet(mm, m.start(), m.end()) if m else None
        except (OSError, ValueError):
            return None

    # -- files on other file systems (SFTP, SMB, FTP...) ------------------------------
    def match_stream(self, fs, path: str, size: int, token=None) -> Optional[str]:
        if size == 0 or size > self.max_bytes:
            return None
        try:
            with fs.open_read(path) as fh:
                tail = b""
                first = True
                while True:
                    if token is not None and token.cancelled:
                        return None
                    block = fh.read(CHUNK)
                    if not block:
                        return None
                    if first:
                        first = False
                        if not self.include_binary and b"\0" in block[:SNIFF]:
                            return None
                    data = tail + block
                    m = self.regex.search(data)
                    if m:
                        return _snippet(data, m.start(), m.end())
                    tail = data[-self.overlap:]
        except (OSError, ValueError, EOFError):
            return None


def _literal_bytes(text: str, case_sensitive: bool) -> bytes:
    """A bytes regex for ``text`` taken literally. Case-insensitive: every
    letter becomes [xX] in UTF-8, so accented letters work too (Python's
    re.IGNORECASE on bytes only knows A-Z)."""
    if case_sensitive:
        return re.escape(text.encode("utf-8"))
    out = []
    for ch in text:
        lo, up = ch.lower(), ch.upper()
        if lo == up or len(lo) != 1 or len(up) != 1:
            out.append(re.escape(ch.encode("utf-8")))
        else:
            out.append(b"(?:" + re.escape(lo.encode("utf-8")) + b"|" + re.escape(up.encode("utf-8")) + b")")
    return b"".join(out)


class Snippet(str):
    """The matching line (a plain str) that also knows where the match is in it:
    ``snippet[snippet.start:snippet.end]`` is the matched text (shown in bold)."""
    start: int = 0
    end: int = 0

    def __new__(cls, text: str, start: int, end: int) -> "Snippet":
        obj = super().__new__(cls, text)
        obj.start = max(0, min(len(text), start))
        obj.end = max(obj.start, min(len(text), end))
        return obj


def _snippet(data, start: int, end: int) -> Snippet:
    """The line around a match, shortened to about SNIPPET characters, keeping
    the match visible - and remembering where in it the match is."""
    line_start = data.rfind(b"\n", max(0, start - 2000), start) + 1
    line_end = data.find(b"\n", end, end + 2000)
    if line_end < 0:
        line_end = min(len(data), end + 2000)
    decode = lambda b: bytes(b).decode("utf-8", "replace").replace("\t", " ")   # noqa: E731
    before = decode(data[line_start:start])
    match = decode(data[start:end])
    after = decode(data[end:line_end])
    before = before.lstrip()
    after = after.rstrip("\r ").rstrip()
    if len(before) + len(match) + len(after) > SNIPPET:
        if len(before) > 24:                          # a little context before the match, then the match
            before = "…" + before[-24:]
        room = max(0, SNIPPET - len(before) - len(match))
        if len(after) > room:
            after = after[:room] + "…"
    text = before + match + after
    return Snippet(text, len(before), len(before) + len(match))

"""
.torrent files: name, total size, files, piece size, trackers, info hash
(BitTorrent v1 and v2) and a magnet link you can copy. A small bencode
reader, no extra packages.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from ...core.formatting import human_size
from . import tools
from .base import Info, InfoProvider, Source

MAX_TORRENT = 32 * 1024 * 1024


class _Decoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.info_span: Optional[Tuple[int, int]] = None

    def decode(self, i: int = 0, depth: int = 0) -> Tuple[Any, int]:
        if depth > 200:
            raise ValueError("Nested too deeply")
        d = self.data
        c = d[i:i + 1]
        if c == b"i":
            j = d.index(b"e", i)
            return int(d[i + 1:j]), j + 1
        if c == b"l":
            i += 1
            out = []
            while d[i:i + 1] != b"e":
                if i >= len(d):
                    raise ValueError("Truncated list")
                value, i = self.decode(i, depth + 1)
                out.append(value)
            return out, i + 1
        if c == b"d":
            i += 1
            out: Dict[bytes, Any] = {}
            while d[i:i + 1] != b"e":
                if i >= len(d):
                    raise ValueError("Truncated dictionary")
                key, i = self.decode(i, depth + 1)
                start = i
                value, i = self.decode(i, depth + 1)
                if key == b"info" and depth == 0:
                    self.info_span = (start, i)
                out[key] = value
            return out, i + 1
        if c.isdigit():
            j = d.index(b":", i)
            n = int(d[i:j])
            return d[j + 1:j + 1 + n], j + 1 + n
        raise ValueError("Not a torrent file")


def _s(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")


def _v2_files(tree: Dict, prefix: str = "") -> List[Tuple[str, int]]:
    out = []
    for name, node in tree.items():
        if name == b"" and isinstance(node, dict):
            out.append((prefix, int(node.get(b"length", 0))))
        elif isinstance(node, dict):
            out.extend(_v2_files(node, f"{prefix}/{_s(name)}" if prefix else _s(name)))
    return out


def read_torrent(data: bytes) -> Info:
    dec = _Decoder(data)
    meta, _ = dec.decode()
    if not isinstance(meta, dict) or not isinstance(meta.get(b"info"), dict):
        raise ValueError("Not a torrent file")
    info_dict = meta[b"info"]
    raw_info = data[dec.info_span[0]:dec.info_span[1]] if dec.info_span else b""
    name = _s(info_dict.get(b"name.utf-8") or info_dict.get(b"name"))
    files: List[Tuple[str, int]] = []
    if b"files" in info_dict:
        for f in info_dict[b"files"]:
            path = f.get(b"path.utf-8") or f.get(b"path") or []
            files.append(("/".join(_s(p) for p in path), int(f.get(b"length", 0))))
    elif b"length" in info_dict:
        files.append((name, int(info_dict[b"length"])))
    elif b"file tree" in info_dict:
        files = _v2_files(info_dict[b"file tree"])
    files = [f for f in files if not f[0].split("/")[-1].startswith(".pad")]
    total = sum(size for _, size in files)
    version = int(info_dict.get(b"meta version", 1) or 1)
    hybrid = version == 2 and b"pieces" in info_dict

    info = Info(kind="BitTorrent file" + (" (v2)" if version == 2 and not hybrid else " (hybrid v1+v2)" if hybrid else ""))
    s = info.section("Torrent")
    s.add("Name", name)
    s.add("Total size", human_size(total) if files else "")
    s.add("Files", f"{len(files):,}")
    piece = int(info_dict.get(b"piece length", 0) or 0)
    if piece:
        pieces = len(info_dict.get(b"pieces", b"")) // 20 or (total + piece - 1) // piece
        s.add("Pieces", f"{pieces:,} × {human_size(piece)}")
    s.add("Private", "Yes — only its own trackers, no DHT" if info_dict.get(b"private") == 1 else "")
    s.add("Created by", _s(meta.get(b"created by")))
    created = meta.get(b"creation date")
    if isinstance(created, int) and created > 0:
        try:
            s.add("Created", datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M"))
        except (OverflowError, OSError, ValueError):
            pass
    s.add("Comment", tools.clip(_s(meta.get(b"comment")), 300))
    s.add("Source", _s(info_dict.get(b"source")))
    h = info.section("Identity")
    v1 = hashlib.sha1(raw_info).hexdigest() if raw_info and (version == 1 or hybrid) else ""
    v2 = hashlib.sha256(raw_info).hexdigest() if raw_info and version == 2 else ""
    h.add("Info hash (v1)", v1)
    h.add("Info hash (v2)", v2)
    if v1 or v2:
        xt = f"urn:btih:{v1}" if v1 else f"urn:btmh:1220{v2}"
        h.add("Magnet link", f"magnet:?xt={xt}&dn={quote(name)}")

    trackers: List[str] = []
    for tier in meta.get(b"announce-list") or []:
        for url in tier if isinstance(tier, list) else [tier]:
            trackers.append(_s(url))
    if meta.get(b"announce"):
        trackers.insert(0, _s(meta[b"announce"]))
    trackers = list(dict.fromkeys(t for t in trackers if t))
    if trackers:
        t = info.section(f"Trackers ({len(trackers)})", key="Trackers")
        for url in trackers[:12]:
            parsed = urlparse(url)
            t.add(parsed.scheme.upper() or "Tracker", parsed.netloc or url)
        if len(trackers) > 12:
            t.add("…", f"{len(trackers) - 12} more")
    seeds = meta.get(b"url-list")
    if seeds:
        seeds = seeds if isinstance(seeds, list) else [seeds]
        info.sections[0].add("Web seeds", f"{len(seeds)}")
    if len(files) > 1:
        f = info.section(f"Files ({len(files):,})", key="Files")
        for path, size in sorted(files, key=lambda x: -x[1])[:12]:
            f.add(path, human_size(size))
        if len(files) > 12:
            f.add("…", f"{len(files) - 12:,} more")
    return info


class TorrentProvider(InfoProvider):
    name = "Torrents"
    exts = frozenset({"torrent"})
    priority = 60
    local_only = False

    def extract(self, src: Source) -> Optional[Info]:
        if src.entry.size > MAX_TORRENT:
            return None
        with src.open() as fh:
            return read_torrent(fh.read())

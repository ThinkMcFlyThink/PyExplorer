"""
Font files (TTF, OTF, TTC, WOFF): family, style, version, designer, licence,
glyph count, weight, variable-font axes, colour support, embedding rights.

Read straight from the font's own tables (the OpenType "name", "maxp",
"OS/2" and "fvar" tables) with nothing but ``struct``. The panel also draws
a sample in the font itself.
"""
from __future__ import annotations

import struct
import zlib
from typing import Dict, Optional, Tuple

from . import tools
from .base import Info, InfoProvider, Source

NAME_IDS = {0: "Copyright", 1: "Family", 2: "Style", 4: "Full name", 5: "Version", 8: "Foundry", 9: "Designer",
            11: "Foundry website", 13: "Licence", 14: "Licence URL", 16: "Family", 17: "Style"}
WEIGHTS = {100: "Thin", 200: "Extra Light", 300: "Light", 400: "Regular", 500: "Medium", 600: "Semi Bold",
           700: "Bold", 800: "Extra Bold", 900: "Black"}
AXES = {"wght": "weight", "wdth": "width", "slnt": "slant", "ital": "italic", "opsz": "optical size"}


class FontFile:
    """Just enough of an OpenType reader to get at a few tables."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.flavor = ""
        self.collection = 0
        self.tables: Dict[str, Tuple[int, int, int]] = {}      # tag -> (offset, comp_length, orig_length)
        self.woff = False
        tag = data[:4]
        if tag == b"ttcf":
            self.collection = struct.unpack(">I", data[8:12])[0]
            first = struct.unpack(">I", data[12:16])[0]
            self._sfnt(first)
        elif tag == b"wOFF":
            self.woff = True
            self.flavor = "CFF" if data[4:8] == b"OTTO" else "TrueType"
            count = struct.unpack(">H", data[12:14])[0]
            for i in range(count):
                t, off, comp, orig, _ = struct.unpack(">4sIIII", data[44 + i * 20:64 + i * 20])
                self.tables[t.decode("latin-1")] = (off, comp, orig)
        elif tag == b"wOF2":
            self.woff = True
            self.flavor = "CFF" if data[4:8] == b"OTTO" else "TrueType"
        elif tag in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
            self._sfnt(0)
        else:
            raise ValueError("Not a font file")

    def _sfnt(self, base: int) -> None:
        self.flavor = "CFF" if self.data[base:base + 4] == b"OTTO" else "TrueType"
        count = struct.unpack(">H", self.data[base + 4:base + 6])[0]
        for i in range(count):
            rec = base + 12 + i * 16
            t, _, off, length = struct.unpack(">4sIII", self.data[rec:rec + 16])
            self.tables[t.decode("latin-1")] = (off, length, length)

    def table(self, tag: str) -> Optional[bytes]:
        entry = self.tables.get(tag)
        if entry is None:
            return None
        off, comp, orig = entry
        raw = self.data[off:off + comp]
        if self.woff and comp < orig:
            raw = zlib.decompress(raw)
        return raw

    def names(self) -> Dict[int, str]:
        raw = self.table("name")
        if not raw:
            return {}
        _fmt, count, storage = struct.unpack(">HHH", raw[:6])
        best: Dict[int, Tuple[int, str]] = {}
        for i in range(count):
            pid, eid, lang, nid, length, off = struct.unpack(">HHHHHH", raw[6 + i * 12:18 + i * 12])
            if nid not in NAME_IDS:
                continue
            blob = raw[storage + off:storage + off + length]
            if pid == 3 or pid == 0:
                text = blob.decode("utf-16-be", "replace")
                score = 3 if lang == 0x409 or pid == 0 else 1
            elif pid == 1 and eid == 0:
                text = blob.decode("mac_roman", "replace")
                score = 2 if lang == 0 else 0
            else:
                continue
            if nid not in best or score > best[nid][0]:
                best[nid] = (score, text.strip())
        return {nid: text for nid, (_, text) in best.items()}


def read_font(data: bytes) -> Info:
    font = FontFile(data)
    names = font.names()
    info = Info(kind=("WOFF web font" if font.woff else "OpenType font" if font.flavor == "CFF" else "TrueType font"))
    s = info.section("Font")
    family = names.get(16) or names.get(1)
    style = names.get(17) or names.get(2)
    s.add("Family", family)
    s.add("Style", style)
    s.add("Full name", names.get(4) if names.get(4) not in (family, f"{family} {style}") else "")
    s.add("Version", (names.get(5) or "").replace("Version ", ""))
    if font.collection:
        s.add("Collection", f"{font.collection} fonts in this file (showing the first)")
    s.add("Outlines", "PostScript (CFF)" if font.flavor == "CFF" else "TrueType" if font.tables else font.flavor)
    maxp = font.table("maxp")
    if maxp and len(maxp) >= 6:
        s.add("Glyphs", f"{struct.unpack('>H', maxp[4:6])[0]:,}")
    os2 = font.table("OS/2")
    if os2 and len(os2) >= 10:
        weight = struct.unpack(">H", os2[4:6])[0]
        s.add("Weight", f"{weight} ({WEIGHTS.get(round(weight / 100) * 100, '')})".replace(" ()", ""))
        fs_type = struct.unpack(">H", os2[8:10])[0]
        s.add("Embedding", "Installable (no restrictions)" if fs_type == 0 else
              "Restricted — may not be embedded" if fs_type & 0x2 else
              "Preview and print only" if fs_type & 0x4 else "Editable" if fs_type & 0x8 else f"flags {fs_type:#x}")
    fvar = font.table("fvar")
    if fvar and len(fvar) >= 16:
        axes_off, _, count, size = struct.unpack(">HHHH", fvar[4:12])
        axes = []
        for i in range(count):
            rec = fvar[axes_off + i * size: axes_off + i * size + 20]
            if len(rec) < 20:
                break
            tag, lo, _default, hi = struct.unpack(">4siii", rec[:16])
            tag = tag.decode("latin-1")
            axes.append(f"{AXES.get(tag, tag)} {lo / 65536:g}–{hi / 65536:g}")
        s.add("Variable", "Yes — " + ", ".join(axes) if axes else "Yes")
    colour = [n for t, n in (("COLR", "COLR"), ("SVG ", "SVG"), ("sbix", "bitmap (sbix)"), ("CBDT", "bitmap (CBDT)"))
              if t in font.tables]
    s.add("Colour glyphs", ", ".join(colour))
    m = info.section("Credits")
    m.add("Designer", names.get(9))
    m.add("Foundry", names.get(8))
    m.add("Website", names.get(11))
    m.add("Copyright", tools.clip(names.get(0, ""), 300))
    m.add("Licence", tools.clip(names.get(13, ""), 400))
    m.add("Licence URL", names.get(14))
    info.extra["font_family"] = family or ""
    return info


class FontProvider(InfoProvider):
    name = "Fonts"
    exts = frozenset({"ttf", "otf", "ttc", "otc", "woff", "woff2", "dfont"})
    priority = 60
    local_only = False
    max_remote_size = 16 * 1024 * 1024

    def extract(self, src: Source) -> Optional[Info]:
        with src.open() as fh:
            data = fh.read(64 * 1024 * 1024)
        info = read_font(data)
        if src.local and src.entry.ext != "dfont":
            info.extra["font_file"] = src.path
        return info

"""
E-books and comics - with their covers.

    EPUB        title, authors, series, publisher, ISBN, language, description,
                pages (the publisher's page list if the book has one, otherwise
                an estimate from the word count), reading time, DRM, cover
    MOBI/AZW3   Kindle books: the same from the EXTH header, and the cover
    CBZ/CBR/CB7 comics: page count, ComicInfo.xml (series, issue, writer...), cover

Only Python's standard library is needed (CBR/CB7 use 7-Zip or unrar if installed).
"""
from __future__ import annotations

import html
import posixpath
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from ...core.sorting import natural_key
from . import tools
from .archives import read_7z, seven_zip
from .base import Info, InfoProvider, Source

WORDS_PER_PAGE = 275          # a typical printed paperback page
WORDS_PER_MINUTE = 250        # average adult reading speed
MAX_TEXT_BYTES = 80 * 1024 * 1024
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".jxl")

_TAG = re.compile(rb"<[^>]+>")
_HEAD = re.compile(rb"<head\b.*?</head>", re.S | re.I)
_STYLE = re.compile(rb"<(script|style)\b.*?</\1>", re.S | re.I)
_WORD = re.compile(r"\w+(?:['’]\w+)*")
_ENTITY = re.compile(rb"&#?\w+;")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(html_text: str) -> str:
    """A book description is often HTML: turn it into plain text with paragraphs."""
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d)\s*/?>", "\n", html_text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def reading_time(words: int) -> str:
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    h, m = divmod(minutes, 60)
    return f"about {h} h {m:02d} min" if h else f"about {m} min"


def count_words(data: bytes) -> int:
    data = _STYLE.sub(b" ", _HEAD.sub(b" ", data))
    data = _ENTITY.sub(b" ", _TAG.sub(b" ", data))
    return len(_WORD.findall(data.decode("utf-8", "replace")))


# ======================================================================================
# EPUB
# ======================================================================================
def read_epub(src: Source) -> Info:
    with zipfile.ZipFile(src.seekable()) as zf:
        names = set(zf.namelist())
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        opf_path = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), None)
        if not opf_path or opf_path not in names:
            raise ValueError("Not a valid EPUB (no package file)")
        root = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)

        def href(h: str) -> str:
            from urllib.parse import unquote
            return posixpath.normpath(posixpath.join(base, unquote(h.split("#")[0]))) if h else ""

        meta: Dict[str, List[str]] = {}
        named: Dict[str, str] = {}             # <meta name="calibre:series" content="..."/>
        props: Dict[str, str] = {}             # EPUB 3 <meta property="...">value</meta>
        refines: Dict[str, Dict[str, str]] = {}
        ids: Dict[str, str] = {}
        identifiers: List[tuple] = []
        manifest: Dict[str, dict] = {}
        spine: List[str] = []
        guide_cover = ""
        for el in root.iter():
            tag = _local(el.tag)
            if tag == "meta":
                if el.get("name"):
                    named[el.get("name").lower()] = el.get("content", "")
                elif el.get("property"):
                    value = (el.text or "").strip()
                    target = (el.get("refines") or "").lstrip("#")
                    if target:
                        refines.setdefault(target, {})[el.get("property")] = value
                    else:
                        props.setdefault(el.get("property"), value)
            elif el.tag.startswith("{http://purl.org/dc/elements/1.1/}"):
                value = " ".join((el.text or "").split())
                if value:
                    meta.setdefault(tag, []).append(value)
                    if el.get("id"):
                        ids[el.get("id")] = value
                    if tag == "identifier":
                        scheme = next((v for k, v in el.attrib.items() if _local(k) == "scheme"), "")
                        identifiers.append((scheme, value))
            elif tag == "item":
                manifest[el.get("id", "")] = {"href": href(el.get("href", "")), "type": el.get("media-type", ""),
                                              "props": el.get("properties", "")}
            elif tag == "itemref":
                spine.append(el.get("idref", ""))
            elif tag == "reference" and el.get("type") == "cover":
                guide_cover = href(el.get("href", ""))

        version = root.get("version", "").split(".")[0]
        info = Info(kind=f"EPUB {version} book" if version else "EPUB book")
        s = info.section("Book")
        s.add("Title", (meta.get("title") or [""])[0])
        authors = []
        for el in root.iter():
            if _local(el.tag) == "creator" and (el.text or "").strip():
                role = next((v for k, v in el.attrib.items() if _local(k) == "role"), "")
                role = role or refines.get(el.get("id", ""), {}).get("role", "")
                if role in ("", "aut"):
                    authors.append(" ".join(el.text.split()))
        s.add("Author" if len(authors) == 1 else "Authors", authors)
        series = named.get("calibre:series") or props.get("belongs-to-collection", "")
        index = named.get("calibre:series_index", "")
        if not index:
            for rid, r in refines.items():
                if r.get("group-position") and props.get("belongs-to-collection"):
                    index = r["group-position"]
        if series:
            try:
                index = f"{float(index):g}" if index else ""
            except ValueError:
                pass
            s.add("Series", series + (f" #{index}" if index else ""))
        s.add("Publisher", (meta.get("publisher") or [""])[0])
        date = (meta.get("date") or [""])[0]
        s.add("Published", date[:10] if re.match(r"\d{4}-\d\d-\d\d", date) else date)
        s.add("Language", ", ".join(tools.language(lang.split("-")[0]) or lang for lang in meta.get("language", [])))
        isbn = next((v for sch, v in identifiers if "isbn" in sch.lower() or "isbn" in v.lower()), "")
        if not isbn:
            isbn = next((v for _sch, v in identifiers if re.fullmatch(r"(97[89])?\d{9}[\dXx]", v.replace("-", ""))), "")
        s.add("ISBN", re.sub(r"(?i)^urn:isbn:", "", isbn))
        s.add("Subjects", tools.clip(", ".join(meta.get("subject", [])), 300))
        s.add("Rights", tools.clip((meta.get("rights") or [""])[0], 200))
        if props.get("rendition:layout") == "pre-paginated":
            s.add("Layout", "Fixed (like a PDF)")

        # DRM: encryption.xml that encrypts more than fonts, or Adobe's rights.xml
        drm = "META-INF/rights.xml" in names
        if "META-INF/encryption.xml" in names and not drm:
            enc = zf.read("META-INF/encryption.xml").decode("utf-8", "replace")
            algorithms = set(re.findall(r'Algorithm="([^"]+)"', enc))
            font_obfuscation = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
            drm = bool(algorithms - font_obfuscation)
        if drm:
            s.add("DRM", "Yes — protected, some apps can't open it")

        # contents: the table of contents, the publisher's page list, words
        c = info.section("Contents")
        nav_item = next((m for m in manifest.values() if "nav" in m["props"].split()), None)
        ncx_item = next((m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
        toc = pages = 0
        if nav_item and nav_item["href"] in names:
            nav = zf.read(nav_item["href"])
            toc_part = re.search(rb'epub:type="toc".*?</nav>', nav, re.S)
            if toc_part:
                toc = len(re.findall(rb"<a\b", toc_part.group(0)))
            page_part = re.search(rb'epub:type="page-list".*?</nav>', nav, re.S)
            if page_part:
                pages = len(re.findall(rb"<a\b", page_part.group(0)))
        if ncx_item and ncx_item["href"] in names and (not toc or not pages):
            ncx = zf.read(ncx_item["href"])
            toc = toc or len(re.findall(rb"<navPoint\b", ncx))
            pages = pages or len(re.findall(rb"<pageTarget\b", ncx))
        c.add("Chapters", f"{toc:,}" if toc else "")
        words = 0
        read_text = src.local or src.entry.size <= InfoProvider.max_remote_size
        if read_text:
            budget = MAX_TEXT_BYTES
            for idref in spine:
                src.check()
                item = manifest.get(idref)
                if not item or item["href"] not in names or "html" not in item["type"]:
                    continue
                zi = zf.getinfo(item["href"])
                if zi.file_size > budget or zi.flag_bits & 0x1:
                    continue
                budget -= zi.file_size
                words += count_words(zf.read(zi))
        if pages:
            c.add("Pages", f"{pages:,} (print edition page list)")
        elif words:
            c.add("Pages", f"≈ {max(1, round(words / WORDS_PER_PAGE)):,} (estimated from the words)")
        if words:
            c.add("Words", f"{words:,}")
            c.add("Reading time", reading_time(words))
        images = sum(1 for m in manifest.values() if m["type"].startswith("image/"))
        c.add("Images", f"{images:,}" if images else "")

        description = (meta.get("description") or [""])[0]
        if description:
            info.section("Description", text=tools.clip(_text(html.unescape(description)), 1500))

        # the cover image
        cover = next((m for m in manifest.values() if "cover-image" in m["props"].split()), None)
        if cover is None and named.get("cover") in manifest:
            cover = manifest[named["cover"]]
        if cover is None:
            cover = next((m for i, m in manifest.items() if m["type"].startswith("image/")
                          and "cover" in (i + m["href"]).lower()), None)
        cover_path = cover["href"] if cover else ""
        if not cover_path and guide_cover in names:              # an XHTML cover page: its first image
            page = zf.read(guide_cover).decode("utf-8", "replace")
            found = re.search(r'(?:src|xlink:href|href)="([^"]+\.(?:jpe?g|png|gif|webp))"', page, re.I)
            if found:
                cover_path = posixpath.normpath(posixpath.join(posixpath.dirname(guide_cover), found.group(1)))
        if cover_path in names:
            info.preview = zf.read(cover_path)
    return info


# ======================================================================================
# MOBI / AZW3 (Kindle)
# ======================================================================================
EXTH_FIELDS = {100: "Author", 101: "Publisher", 103: "Description", 104: "ISBN", 105: "Subjects",
               106: "Published", 108: "Contributor", 109: "Rights", 113: "ASIN", 503: "Title", 524: "Language"}


def read_mobi(src: Source) -> Info:
    with src.open() as fh:
        data = fh.read(min(src.entry.size, 64 * 1024 * 1024) if not src.local else 4 * 1024 * 1024)
        if len(data) < 78 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
            raise ValueError("Not a Kindle book")
        count = struct.unpack(">H", data[76:78])[0]
        offsets = [struct.unpack(">I", data[78 + i * 8: 82 + i * 8])[0] for i in range(count)]
        rec0 = data[offsets[0]:offsets[1] if count > 1 else None]
        _compression, _, text_len, _, _, encryption = struct.unpack(">HHIHHH", rec0[:14])
        info = Info(kind="Kindle book")
        s = info.section("Book")
        fields: Dict[str, List[str]] = {}
        cover_index = None
        first_image = None
        if rec0[16:20] == b"MOBI":
            header_len = struct.unpack(">I", rec0[20:24])[0]
            version = struct.unpack(">I", rec0[36:40])[0]
            info.kind = "Kindle book (AZW3 / KF8)" if version >= 8 or src.entry.ext == "azw3" else "Kindle book (MOBI)"
            name_off, name_len = struct.unpack(">II", rec0[84:92])
            fields["Title"] = [rec0[name_off:name_off + name_len].decode("utf-8", "replace")]
            first_image = struct.unpack(">I", rec0[108:112])[0]
            exth_flags = struct.unpack(">I", rec0[128:132])[0]
            if exth_flags & 0x40:
                pos = 16 + header_len
                if rec0[pos:pos + 4] == b"EXTH":
                    n_records = struct.unpack(">I", rec0[pos + 8:pos + 12])[0]
                    pos += 12
                    for _ in range(n_records):
                        rtype, rlen = struct.unpack(">II", rec0[pos:pos + 8])
                        value = rec0[pos + 8:pos + rlen]
                        if rtype == 201 and len(value) == 4:
                            cover_index = struct.unpack(">I", value)[0]
                        elif rtype in EXTH_FIELDS:
                            fields.setdefault(EXTH_FIELDS[rtype], []).append(value.decode("utf-8", "replace"))
                        pos += rlen
        title = (fields.get("Title") or [""])[-1]
        s.add("Title", title)
        authors = fields.get("Author", [])
        s.add("Author" if len(authors) == 1 else "Authors", authors)
        s.add("Publisher", (fields.get("Publisher") or [""])[0])
        s.add("Published", (fields.get("Published") or [""])[0][:10])
        s.add("Language", (fields.get("Language") or [""])[0])
        s.add("ISBN", (fields.get("ISBN") or [""])[0])
        s.add("ASIN", (fields.get("ASIN") or [""])[0])
        s.add("Subjects", tools.clip(", ".join(fields.get("Subjects", [])), 300))
        if encryption:
            s.add("DRM", "Yes — protected, some apps can't open it")
        c = info.section("Contents")
        if text_len:
            c.add("Pages", f"≈ {max(1, round(text_len / (WORDS_PER_PAGE * 6))):,} (estimated from the text length)")
            c.add("Reading time", reading_time(text_len // 6))
        if fields.get("Description"):
            info.section("Description", text=tools.clip(_text(fields["Description"][0]), 1500))
        if first_image not in (None, 0xFFFFFFFF) and cover_index is not None:
            idx = first_image + cover_index
            if idx + 1 < count and offsets[idx + 1] <= len(data):
                img = data[offsets[idx]:offsets[idx + 1]]
                if img[:3] == b"\xff\xd8\xff" or img[:8] == b"\x89PNG\r\n\x1a\n" or img[:3] == b"GIF":
                    info.preview = img
        return info


# ======================================================================================
# Comics
# ======================================================================================
COMIC_FIELDS = [("Series", "Series"), ("Number", "Issue"), ("Volume", "Volume"), ("Title", "Title"),
                ("Writer", "Writer"), ("Penciller", "Artist"), ("Publisher", "Publisher"), ("Imprint", "Imprint"),
                ("Genre", "Genre"), ("LanguageISO", "Language"), ("AgeRating", "Age rating")]


def _comic_info(xml_bytes: bytes, info: Info) -> None:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return
    values = {_local(el.tag): (el.text or "").strip() for el in root}
    s = info.section("Comic")
    for key, label in COMIC_FIELDS:
        value = values.get(key.lower(), "")
        if key == "LanguageISO":
            value = tools.language(value)
        s.add(label, value)
    if values.get("year"):
        s.add("Date", "-".join(x for x in (values.get("year"), values.get("month", "").zfill(2) if values.get("month")
                                                                        else "") if x))
    if values.get("manga", "").lower().startswith("yes"):
        s.add("Reading", "Right to left (manga)")
    if values.get("summary"):
        info.section("Summary", text=tools.clip(values["summary"], 1500))


def read_comic(src: Source) -> Info:
    ext = src.entry.ext
    info = Info(kind={"cbz": "Comic book (ZIP)", "cbr": "Comic book (RAR)", "cb7": "Comic book (7z)",
                      "cbt": "Comic book (TAR)"}.get(ext, "Comic book"))
    pages: List[str] = []
    comic_xml = b""
    cover = None
    if ext == "cbz" or (ext != "cbr" and src.head(4) == b"PK\x03\x04"):   # some .cb7 files are really ZIPs
        with zipfile.ZipFile(src.seekable()) as zf:
            names = zf.namelist()
            pages = sorted((n for n in names if n.lower().endswith(IMAGE_EXTS) and "__macosx" not in n.lower()),
                           key=natural_key)
            xml_name = next((n for n in names if n.lower().endswith("comicinfo.xml")), None)
            if xml_name:
                comic_xml = zf.read(xml_name)
            if pages:
                cover = zf.read(pages[0])
    elif src.local and (seven_zip() or (ext == "cbr" and tools.which("unrar"))):
        if seven_zip():
            listing = read_7z(src.path, src.token)
            names = listing.names
        else:
            names = tools.run([tools.which("unrar"), "lb", "--", src.path], timeout=15,
                              token=src.token).decode("utf-8", "replace").splitlines()
        pages = sorted((n for n in names if n.lower().endswith(IMAGE_EXTS)), key=natural_key)
        xml_name = next((n for n in names if n.lower().endswith("comicinfo.xml")), None)
        if xml_name:
            comic_xml = _extract(src.path, xml_name, src.token)
        if pages:
            first, path = pages[0], src.path
            cover = lambda token=None: _extract(path, first, token)     # noqa: E731 - runs later, in a worker
    else:
        info.note = "Install 7-Zip to read this comic's pages."
    if comic_xml:
        _comic_info(comic_xml, info)
    info.section("Contents").add("Pages", f"{len(pages):,}" if pages else "")
    info.preview = cover
    return info


def _extract(archive: str, member: str, token) -> bytes:
    if seven_zip():
        return tools.run([seven_zip(), "e", "-so", "-bd", "--", archive, member], timeout=15, token=token)
    return tools.run([tools.which("unrar"), "p", "-inul", "--", archive, member], timeout=15, token=token)


class BookProvider(InfoProvider):
    name = "E-books and comics"
    exts = frozenset({"epub", "kepub", "mobi", "azw", "azw3", "prc", "cbz", "cbr", "cb7", "cbt"})
    priority = 70
    local_only = False

    def extract(self, src: Source) -> Optional[Info]:
        ext = src.entry.ext
        if ext in ("epub", "kepub"):
            return read_epub(src)
        if ext in ("mobi", "azw", "azw3", "prc"):
            return read_mobi(src)
        return read_comic(src)

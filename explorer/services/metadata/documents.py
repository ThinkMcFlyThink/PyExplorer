"""
Office documents - Word / Excel / PowerPoint (OOXML) and LibreOffice /
OpenDocument. Both are ZIP files with small XML files describing the
document, so only Python's standard library is needed:

    title, author, last edited by, dates, revision, application,
    pages / words / characters / slides / sheet names,
    and the thumbnail the office app saved inside (when it did).
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Optional

from ...core.formatting import human_time
from . import tools
from .base import Info, InfoProvider, Source

OOXML = {"docx": "Word document", "docm": "Word document (macros)", "dotx": "Word template",
         "xlsx": "Excel workbook", "xlsm": "Excel workbook (macros)", "xltx": "Excel template",
         "pptx": "PowerPoint presentation", "pptm": "PowerPoint presentation (macros)", "ppsx": "PowerPoint show",
         "potx": "PowerPoint template", "vsdx": "Visio drawing"}
ODF = {"odt": "OpenDocument text", "ods": "OpenDocument spreadsheet", "odp": "OpenDocument presentation",
       "odg": "OpenDocument drawing", "ott": "OpenDocument text template", "ots": "OpenDocument spreadsheet template",
       "otp": "OpenDocument presentation template", "odf": "OpenDocument formula"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_fields(data: bytes) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return out
    for el in root.iter():
        if el is root:
            continue
        text = (el.text or "").strip()
        if text:
            out.setdefault(_local(el.tag), text)
        for k, v in el.attrib.items():                    # ODF statistics are attributes
            out.setdefault(_local(k), v)
    return out


def _date(text: str) -> str:
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return human_time(dt.timestamp())
    except ValueError:
        return text[:19].replace("T", " ")


def _count(value) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    return f"{n:,}" if n else ""


def read_ooxml(zf: zipfile.ZipFile, ext: str) -> Info:
    names = set(zf.namelist())
    info = Info(kind=OOXML.get(ext, "Office document"))
    core = _xml_fields(zf.read("docProps/core.xml")) if "docProps/core.xml" in names else {}
    app = _xml_fields(zf.read("docProps/app.xml")) if "docProps/app.xml" in names else {}
    d = info.section("Document")
    d.add("Title", core.get("title"))
    d.add("Subject", core.get("subject"))
    d.add("Author", core.get("creator"))
    d.add("Last edited by", core.get("lastModifiedBy"))
    d.add("Created", _date(core.get("created", "")))
    d.add("Saved", _date(core.get("modified", "")))
    d.add("Revision", core.get("revision"))
    d.add("Keywords", core.get("keywords"))
    d.add("Company", app.get("Company"))
    application = app.get("Application", "")
    if application and app.get("AppVersion"):
        application += f" {app['AppVersion']}"
    d.add("Made with", application)
    d.add("Template", app.get("Template") if app.get("Template", "").lower() not in ("normal.dotm", "normal") else "")
    if any(n.startswith(("word/vbaProject", "xl/vbaProject", "ppt/vbaProject")) for n in names):
        d.add("Macros", "Yes — only open it if you trust where it came from")
    s = info.section("Statistics")
    if ext.startswith("doc"):
        s.add("Pages", _count(app.get("Pages")))
        s.add("Words", _count(app.get("Words")))
        s.add("Characters", _count(app.get("CharactersWithSpaces") or app.get("Characters")))
        s.add("Paragraphs", _count(app.get("Paragraphs")))
        minutes = app.get("TotalTime", "")
        s.add("Editing time", f"{int(minutes):,} min" if minutes.isdigit() and int(minutes) else "")
    elif ext.startswith("xl"):
        if "xl/workbook.xml" in names:
            sheets = re.findall(rb'<(?:\w+:)?sheet\b[^>]*\bname="([^"]*)"', zf.read("xl/workbook.xml"))
            s.add("Sheets", f"{len(sheets)}: " + tools.clip(", ".join(x.decode("utf-8", "replace") for x in sheets), 300)
                  if sheets else "")
    elif ext.startswith(("ppt", "pps", "pot")):
        s.add("Slides", _count(app.get("Slides")) or _count(sum(1 for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))))
        s.add("Notes", _count(app.get("Notes")))
        s.add("Hidden slides", _count(app.get("HiddenSlides")))
        s.add("Media clips", _count(app.get("MMClips")))
    media = sum(1 for n in names if "/media/" in n)
    s.add("Embedded images and media", _count(media))
    thumb = next((n for n in ("docProps/thumbnail.jpeg", "docProps/thumbnail.jpg", "docProps/thumbnail.png",
                              "docProps/thumbnail.wmf") if n in names and not n.endswith("wmf")), None)
    if thumb:
        info.preview = zf.read(thumb)
    return info


def read_odf(zf: zipfile.ZipFile, ext: str) -> Info:
    names = set(zf.namelist())
    info = Info(kind=ODF.get(ext, "OpenDocument file"))
    meta = _xml_fields(zf.read("meta.xml")) if "meta.xml" in names else {}
    d = info.section("Document")
    d.add("Title", meta.get("title"))
    d.add("Subject", meta.get("subject"))
    d.add("Author", meta.get("initial-creator"))
    d.add("Last edited by", meta.get("creator"))
    d.add("Created", _date(meta.get("creation-date", "")))
    d.add("Saved", _date(meta.get("date", "")))
    d.add("Revision", meta.get("editing-cycles"))
    d.add("Keywords", meta.get("keyword"))
    d.add("Made with", (meta.get("generator") or "").split("$")[0].replace("/", " ").strip())
    s = info.section("Statistics")
    s.add("Pages", _count(meta.get("page-count")))
    s.add("Words", _count(meta.get("word-count")))
    s.add("Characters", _count(meta.get("character-count")))
    s.add("Paragraphs", _count(meta.get("paragraph-count")))
    s.add("Tables", _count(meta.get("table-count")))
    s.add("Images", _count(meta.get("image-count")))
    s.add("Cells", _count(meta.get("cell-count")))
    if ext in ("ods", "ots") and "content.xml" in names:
        head = zf.open("content.xml").read(4 * 1024 * 1024)
        sheets = re.findall(rb'<table:table\b[^>]*\btable:name="([^"]*)"', head)
        s.add("Sheets", f"{len(sheets)}: " + tools.clip(", ".join(x.decode("utf-8", "replace") for x in sheets), 300)
              if sheets else "")
    if ext in ("odp", "otp") and "content.xml" in names:
        slides = len(re.findall(rb"<draw:page\b", zf.read("content.xml")))
        s.add("Slides", _count(slides))
    if "Thumbnails/thumbnail.png" in names:
        info.preview = zf.read("Thumbnails/thumbnail.png")
    return info


class OfficeProvider(InfoProvider):
    name = "Office documents"
    exts = frozenset(set(OOXML) | set(ODF))
    priority = 60
    local_only = False

    def extract(self, src: Source) -> Optional[Info]:
        with zipfile.ZipFile(src.seekable()) as zf:
            if src.entry.ext in OOXML:
                return read_ooxml(zf, src.entry.ext)
            return read_odf(zf, src.entry.ext)

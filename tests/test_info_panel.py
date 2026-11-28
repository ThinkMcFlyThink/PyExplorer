"""
The Info panel (Alt+P) and the metadata providers behind it.

  * every provider, on small files made here (video/audio only if FFmpeg is installed)
  * the panel: Alt+P, details + previews appear, multi-selection, folders,
    and the speed rules - no work at all while it is closed, and holding an
    arrow key through many files reads only the one you stop on.

Run:  python tests/test_info_panel.py
"""
import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication([])

from explorer.core.tasks import CancelToken, Cancelled  # noqa: E402
from explorer.services.metadata import gather, tools  # noqa: E402
from explorer.services.metadata.base import Section  # noqa: E402
from explorer.ui.info_providers import register_qt_providers  # noqa: E402
from explorer.vfs.local import LocalFileSystem  # noqa: E402

FAILED = []


def check(cond, message):
    print(("  ok - " if cond else "  FAIL - ") + message)
    if not cond:
        FAILED.append(message)


def spin(cond=lambda: False, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def png(w=40, h=60, color=0x3366CC) -> bytes:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(color)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def bencode(x) -> bytes:
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b"%d:" % len(x) + x
    if isinstance(x, list):
        return b"l" + b"".join(map(bencode, x)) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(x.items())) + b"e"


# ---------------------------------------------------------------------------- sample files
D = tempfile.mkdtemp(prefix="pyx-info-")
with zipfile.ZipFile(f"{D}/book.epub", "w") as z:
    z.writestr("mimetype", "application/epub+zip")
    z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
               'version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
    z.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata '
               'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Test Book</dc:title><dc:creator>Jane '
               'Writer</dc:creator><dc:language>en</dc:language><dc:identifier>urn:isbn:9780306406157'
               '</dc:identifier><dc:description>&lt;p&gt;A &lt;b&gt;fine&lt;/b&gt; book.&lt;/p&gt;</dc:description>'
               '<meta name="calibre:series" content="Tests"/><meta name="calibre:series_index" content="2"/>'
               '</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               'properties="nav"/><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/><item '
               'id="cov" href="img/cover.png" media-type="image/png" properties="cover-image"/></manifest>'
               '<spine><itemref idref="c1"/></spine></package>')
    z.writestr("OEBPS/nav.xhtml", '<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc">'
               '<ol><li><a href="ch1.xhtml">One</a></li></ol></nav><nav epub:type="page-list"><ol><li><a href="'
               'ch1.xhtml#p1">1</a></li><li><a href="ch1.xhtml#p2">2</a></li></ol></nav></body></html>')
    z.writestr("OEBPS/ch1.xhtml", "<html><head><title>x</title></head><body><p>" + "word " * 2750 + "</p></body></html>")
    z.writestr("OEBPS/img/cover.png", png())
with zipfile.ZipFile(f"{D}/comic.cbz", "w") as z:
    for i in (10, 2, 1):
        z.writestr(f"page{i}.png", png(color=0x110000 * i))
    z.writestr("ComicInfo.xml", "<ComicInfo><Series>Heroes</Series><Number>3</Number><Writer>Al</Writer></ComicInfo>")
with zipfile.ZipFile(f"{D}/report.docx", "w") as z:
    z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:title>Report</dc:title><dc:creator>Bob</dc:creator></cp:coreProperties>')
    z.writestr("docProps/app.xml", '<Properties><Pages>12</Pages><Words>3456</Words></Properties>')
with zipfile.ZipFile(f"{D}/notes.odt", "w") as z:
    z.writestr("meta.xml", '<office:document-meta xmlns:office="o" xmlns:meta="m" xmlns:dc="http://purl.org/dc/'
               'elements/1.1/"><office:meta><dc:title>Notes</dc:title><meta:document-statistic meta:page-count="3" '
               'meta:word-count="420"/></office:meta></office:document-meta>')
    z.writestr("Thumbnails/thumbnail.png", png())
with zipfile.ZipFile(f"{D}/stuff.zip", "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("dir/a.txt", "a" * 10000)
    z.writestr("dir/b.txt", "b" * 5000)
    z.writestr("top.txt", "hello")
with tarfile.open(f"{D}/stuff.tar.gz", "w:gz") as t:
    t.add(f"{D}/stuff.zip", arcname="inner/stuff.zip")
open(f"{D}/log.txt.gz", "wb").write(gzip.compress(b"hello\n" * 1000))
open(f"{D}/data.csv", "w").write("name,age\n" + "".join(f"p{i},{i}\n" for i in range(50)))
open(f"{D}/good.json", "w").write(json.dumps({"a": 1, "b": [1, 2]}))
open(f"{D}/bad.json", "w").write('{"a": 1,,}')
open(f"{D}/run_me", "w").write("#!/usr/bin/env python3\nprint('hi')\n")
open(f"{D}/subs.srt", "w").write("1\n00:00:01,000 --> 00:00:02,000\nHi\n\n2\n00:01:01,000 --> 00:01:05,500\nBye\n")
open(f"{D}/blob.dat", "wb").write(bytes(range(256)) * 8)
open(f"{D}/files.torrent", "wb").write(bencode({
    "announce": "udp://tracker.example.org:1337/announce", "created by": "Test", "creation date": 1700000000,
    "info": {"name": "Some Files", "piece length": 262144, "pieces": b"x" * 40,
             "files": [{"length": 1000, "path": ["a.bin"]}, {"length": 2000000, "path": ["sub", "b.bin"]}]}}))
open(f"{D}/picture.png", "wb").write(png(320, 200))
os.mkdir(f"{D}/folder")
for i in range(7):
    open(f"{D}/folder/f{i}.txt", "w").write("x" * i)
os.mkdir(f"{D}/folder/sub")
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
if HAVE_FFMPEG:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-metadata:s:a:0", "language=eng", f"{D}/clip.mkv"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1",
                    "-metadata", "artist=Some Artist", "-metadata", "album=An Album", f"{D}/song.mp3"], check=True)
for n in range(40):
    open(f"{D}/many{n:02d}.txt", "w").write(f"file {n}\n")

fs = LocalFileSystem()
register_qt_providers()


def info_for(name, **options):
    return gather(fs.stat(os.path.join(D, name)), fs, None, {"text_lines": 5, **options})


def rows(info, title):
    s = next((s for s in info.sections if s.title.split(" (")[0] == title), None)
    return dict(s.rows) if s else {}


# ---------------------------------------------------------------------------- providers
print("providers")
i = info_for("book.epub")
r = rows(i, "Book")
check(r.get("Title") == "The Test Book" and r.get("Author") == "Jane Writer" and r.get("ISBN") == "9780306406157",
      "EPUB: title, author, ISBN")
check(r.get("Series") == "Tests #2" and r.get("Language") == "English", "EPUB: series (calibre) and language name")
c = rows(i, "Contents")
check(c.get("Pages", "").startswith("2 ") and c.get("Words") == "2,750", "EPUB: print page list beats the estimate")
check(isinstance(i.preview, bytes) and i.preview[:4] == b"\x89PNG", "EPUB: cover image found")
check(any(s.title == "Description" and s.text == "A fine book." for s in i.sections), "EPUB: description, HTML removed")

i = info_for("comic.cbz")
check(rows(i, "Comic").get("Series") == "Heroes" and rows(i, "Contents").get("Pages") == "3", "comic: ComicInfo + pages")
check(isinstance(i.preview, bytes) and QImage.fromData(i.preview).pixelColor(0, 0).red() == 0x11,
      "comic: cover is page1 (natural order, not page10)")

i = info_for("report.docx")
check(rows(i, "Document").get("Author") == "Bob" and rows(i, "Statistics").get("Pages") == "12", "Word: author, pages")
i = info_for("notes.odt")
check(rows(i, "Statistics").get("Words") == "420" and i.preview is not None, "OpenDocument: words + thumbnail")

i = info_for("stuff.zip")
a = rows(i, "Archive")
check(a.get("Contents") == "3 files, 1 folder" and "saves" in a.get("Compression", ""), "ZIP: counts + compression")
check(rows(i, "Top level").get("dir/", "").startswith("2 files"), "ZIP: top level shows the folder")
i = info_for("stuff.tar.gz")
check(rows(i, "Archive").get("Format") == "TAR + gzip", "tar.gz listed")
i = info_for("log.txt.gz")
check("saves" in rows(i, "Archive").get("Unpacked size", ""), "gzip: original size from the trailer")

i = info_for("data.csv")
t = rows(i, "Table")
check(t.get("Columns") == "2" and t.get("Rows", "").startswith("50"), "CSV: columns and rows")
check(any(s.mono and s.text.startswith("name,age") for s in i.sections), "text preview (monospace)")
check(rows(info_for("good.json"), "JSON").get("Valid") == "Yes", "JSON: valid")
check(rows(info_for("bad.json"), "JSON").get("Valid", "").startswith("No"), "JSON: invalid, with the reason")
check(info_for("run_me").kind == "Python", "no extension: sniffed as text, language from #!")
check(rows(info_for("subs.srt"), "Subtitles").get("Runs until") == "1:01", "SRT: cues and length")
check(not [s for s in info_for("blob.dat").sections[1:]], "binary junk: nothing but General")

i = info_for("files.torrent")
check(rows(i, "Torrent").get("Files") == "2" and rows(i, "Identity").get("Magnet link", "").startswith("magnet:?xt=urn:btih:"),
      "torrent: files + magnet link")
i = info_for("picture.png")
check(rows(i, "Image").get("Dimensions", "").startswith("320 × 200") and i.preview == f"{D}/picture.png",
      "image: dimensions without decoding, preview = the file")
ls = shutil.which("ls")
if ls:
    i = gather(fs.stat(os.path.realpath(ls)), fs, None, {})
    check(rows(i, "Program").get("CPU") is not None, "ELF program: CPU and type")
font = next((os.path.join(r, f) for r, _, fs_ in os.walk("/usr/share/fonts") for f in fs_ if f.endswith(".ttf")), None)
if font:
    i = gather(fs.stat(font), fs, None, {})
    check(rows(i, "Font").get("Family") and i.extra.get("font_file") == font, "font: family + sample")
i = info_for("folder")
check(rows(i, "Contents") == {"Folders": "1", "Files": "7", "Files' size": "21 B  (not counting subfolders)"},
      "folder: contents counted")
check(len(info_for("book.epub", deep=False).sections) == 1, "deep=False (cloud placeholder): General only")
check(info_for("book.epub").as_text().startswith("[General]"), "Copy details: plain text")
s = Section("x").add("a", "").add("b", None).add("c", []).add("d", 0).add("e", ["x", ""])
check(s.rows == [("d", "0"), ("e", "x")], "Section.add skips empty values")

if HAVE_FFMPEG:
    i = info_for("clip.mkv")
    v, m = rows(i, "Video"), rows(i, "Media")
    check(v.get("Resolution") == "1280 × 720  (720p)" and v.get("Frame rate") == "25 fps", "video: resolution, fps")
    check(m.get("Duration") == "0:02" and "Matroska" in m.get("Container", ""), "video: container, duration")
    check(rows(i, "Audio").get("Language") == "English", "video: audio language")
    check(callable(i.preview) and QImage.fromData(i.preview(None)).width() > 0, "video: poster frame from ffmpeg")
    i = info_for("song.mp3")
    check(rows(i, "Tags").get("Artist") == "Some Artist" and i.kind.startswith("Audio"), "audio: tags first")
    # cancelling kills the external program straight away
    token = CancelToken()
    token.cancel()
    try:
        tools.run(["sleep", "5"], token=token)
        check(False, "cancelled tools.run raises")
    except Cancelled:
        check(True, "cancelled tools.run raises Cancelled at once")
else:
    print("  (FFmpeg not installed: video/audio checks skipped)")

# ---------------------------------------------------------------------------- the panel
print("panel")
from explorer.app import build_context  # noqa: E402
import explorer.services.metadata as metadata  # noqa: E402

ctx = build_context(app)
main = ctx.main
main.restore(None)
ctx.dispatcher.start()
main.resize(1300, 820)
main.show()
spin(timeout=0.3)
tab = ctx.commands.active_tab()
tab.navigate(D)
spin(lambda: not tab._loading, 5)
panel = main.info

calls = []
real_gather = metadata.gather


def counting_gather(entry, *a, **k):
    calls.append(entry.name)
    return real_gather(entry, *a, **k)


metadata.gather = counting_gather

check(not main.info_visible and not ctx.actions.qaction("window.info").isChecked(), "closed by default")
tab.view.select_paths([f"{D}/book.epub"])
spin(timeout=0.4)
check(calls == [] and panel._key is None, "closed: selecting files does no work")
check(ctx.actions.get("window.info").shortcut == "Alt+P", "shortcut is Alt+P")

ctx.actions.trigger("window.info")
check(main.info_visible and ctx.actions.qaction("window.info").isChecked(), "Alt+P opens it")
check(spin(lambda: calls == ["book.epub"] and panel.note.isHidden(), 5), "opening reads the selected item")
check(spin(lambda: not panel.preview.isHidden(), 3), "cover shown")
titles = [w.text() for w in panel.sections.findChildren(QPushButton) if w.objectName() == "InfoSection"]
check(titles[:3] == ["▾  General", "▾  Book", "▾  Contents"], f"sections drawn {titles}")
check(ctx.settings.get("info_panel.visible") is True, "open state saved")

print("speed")
calls.clear()
for n in range(40):                                        # like holding the down arrow
    tab.view.select_paths([f"{D}/many{n:02d}.txt"])
    app.processEvents()
spin(timeout=0.6)
check(calls == ["many39.txt"], f"40 quick selection changes -> 1 read ({len(calls)})")
check(panel.name.text() == "many39.txt", "shows where you stopped")

calls.clear()
tab.view.select_paths([f"{D}/book.epub"])
spin(timeout=0.3)
check(calls == [] and panel.kind.text() == "EPUB 3 book", "going back: from the cache, no re-read")

print("selection kinds")
tab.view.select_paths([f"{D}/stuff.zip", f"{D}/data.csv", f"{D}/folder"])
spin(timeout=0.3)
check(panel.name.text() == "3 items selected" and panel.kind.text() == "1 folder, 2 files", "multi-selection summary")
check(panel.size_button.isVisible(), "multi-selection: Calculate size")
panel.size_button.click()
check(spin(lambda: "files" in panel.action_result.text() and "…" not in panel.action_result.text(), 5),
      f"Calculate size finishes: {panel.action_result.text()!r}")
tab.view.clearSelection()
spin(lambda: "This folder" in panel.kind.text(), 2)
check("This folder" in panel.kind.text(), "nothing selected: the folder itself")
tab.view.select_paths([f"{D}/picture.png"])
spin(lambda: panel.hash_button.isVisible(), 2)
panel.hash_button.click()
check(spin(lambda: "SHA-256:\n" in panel.action_result.text(), 10), "SHA-256 button")

print("folding")
image_header = lambda: next((w for w in panel.sections.findChildren(QPushButton)  # noqa: E731
                             if w.text().endswith("Image")), None)
check(spin(lambda: image_header() is not None, 5), "image details arrive")
header = image_header()
header.click()
check("Image" in ctx.settings.get("info_panel.collapsed"), "folding a section is remembered")
header.click()
check("Image" not in ctx.settings.get("info_panel.collapsed"), "and unfolding")

print("closing")
ctx.actions.trigger("window.info")
calls.clear()
tab.view.select_paths([f"{D}/comic.cbz"])
spin(timeout=0.4)
check(not main.info_visible and calls == [], "closed again: idle")
check(ctx.settings.get("info_panel.visible") is False, "closed state saved")

metadata.gather = real_gather
shutil.rmtree(D, ignore_errors=True)
print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    sys.exit(1)
print("INFO PANEL TESTS PASSED")

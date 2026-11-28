"""
Info panel providers that need Qt: images and PDFs. They register
themselves with services/metadata when the panel first opens.

    Images   Qt reads the size, format and frame count without decoding the
             picture. With Pillow installed (optional, ``pip install pillow``)
             you also get the camera details: camera, lens, exposure, ISO,
             date taken, GPS position, colour profile, DPI.
    PDF      pdfinfo (poppler-utils) when installed - it reports the most -
             otherwise Qt's own PDF module; the first page is the preview.

Only non-widget Qt classes are used here (QImageReader, QPdfDocument), which
are safe in worker threads.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QSize
from PySide6.QtGui import QImage, QImageReader, QPainter

from ..services.metadata import register
from ..services.metadata import tools
from ..services.metadata.base import Info, InfoProvider, Source

log = logging.getLogger(__name__)

EXIF_EXTS = {"jpg", "jpeg", "jfif", "tif", "tiff", "webp", "heic", "heif", "png", "avif", "dng", "jxl"}
ORIENTATION = {2: "Mirrored", 3: "Rotated 180°", 4: "Flipped", 5: "Mirrored, rotated 90°", 6: "Rotated 90° clockwise",
               7: "Mirrored, rotated 90° anticlockwise", 8: "Rotated 90° anticlockwise"}
MODES = {"1": "Black and white (1-bit)", "L": "Greyscale, 8-bit", "LA": "Greyscale with transparency",
         "P": "Palette (up to 256 colours)", "PA": "Palette with transparency", "RGB": "RGB, 8 bits per channel",
         "RGBA": "RGB with transparency, 8 bits per channel", "CMYK": "CMYK (print)", "YCbCr": "YCbCr",
         "I;16": "Greyscale, 16-bit", "I;16B": "Greyscale, 16-bit", "I": "Greyscale, 32-bit", "F": "32-bit float",
         "LAB": "Lab colour"}
PAPER = [("A3", 297, 420), ("A4", 210, 297), ("A5", 148, 210), ("Letter", 215.9, 279.4), ("Legal", 215.9, 355.6),
         ("Tabloid", 279.4, 431.8), ("B5", 176, 250)]


def paper_name(w_mm: float, h_mm: float) -> str:
    lo, hi = sorted((w_mm, h_mm))
    for name, a, b in PAPER:
        if abs(lo - a) < 3 and abs(hi - b) < 3:
            return f"{name} {'landscape' if w_mm > h_mm else 'portrait'}"
    return ""


# ======================================================================================
# Images
# ======================================================================================
def _reader(src: Source):
    if src.local:
        return QImageReader(src.path), None
    with src.open() as fh:
        data = QByteArray(fh.read())
    buf = QBuffer()
    buf.setData(data)
    buf.open(QBuffer.OpenModeFlag.ReadOnly)
    return QImageReader(buf), (buf, bytes(data))


def _ratio(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            num, den = value
            return num / den if den else 0.0
        except (TypeError, ValueError):
            return 0.0


def _gps(gps: dict) -> str:
    def coord(values, ref) -> Optional[float]:
        try:
            d, m, s = (_ratio(v) for v in values)
        except (TypeError, ValueError):
            return None
        value = d + m / 60 + s / 3600
        return -value if ref in ("S", "W") else value
    lat = coord(gps.get(2), gps.get(1))
    lon = coord(gps.get(4), gps.get(3))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return ""
    return f"{abs(lat):.5f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.5f}° {'E' if lon >= 0 else 'W'}"


def pillow_details(path_or_bytes, info: Info, ext: str) -> bool:
    """Colour, DPI, profile and EXIF via Pillow. False when Pillow isn't installed."""
    try:
        from PIL import Image
    except ImportError:
        return False
    import io
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    try:
        img = Image.open(source)                     # lazy: reads the header, not the pixels
    except Exception:  # noqa: BLE001 - Pillow can't read every format Qt can
        return True
    with img:
        s = info.sections[0]
        s.add("Colour", MODES.get(img.mode, img.mode))
        dpi = img.info.get("dpi")
        if dpi and _ratio(dpi[0]) > 1:
            x, y = round(_ratio(dpi[0])), round(_ratio(dpi[1]))
            s.add("Resolution", f"{x} DPI" if x == y else f"{x} × {y} DPI")
        icc = img.info.get("icc_profile")
        if icc:
            name = "Embedded"
            try:
                from PIL import ImageCms
                name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() or name
            except Exception:  # noqa: BLE001
                pass
            s.add("Colour profile", name)
        if img.info.get("progressive") or img.info.get("progression"):
            s.add("Encoding", "Progressive")
        try:
            exif = img.getexif()
        except Exception:  # noqa: BLE001
            exif = None
        if not exif:
            return True
        sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        tags = dict(exif)
        tags.update(sub or {})
        c = info.section("Camera")
        make, model = str(tags.get(0x010F, "")).strip(), str(tags.get(0x0110, "")).strip()
        c.add("Camera", model if make and model.lower().startswith(make.split()[0].lower()) else
              " ".join(x for x in (make, model) if x))
        c.add("Lens", str(tags.get(0xA434, "")).strip())
        c.add("Taken", str(tags.get(0x9003) or tags.get(0x0132) or "").replace(":", "-", 2))
        exposure = _ratio(tags.get(0x829A))
        if exposure:
            c.add("Exposure", f"1/{round(1 / exposure)} s" if exposure < 1 else f"{exposure:g} s")
        f_number = _ratio(tags.get(0x829D))
        c.add("Aperture", f"f/{f_number:.1f}".replace(".0", "") if f_number else "")
        iso = tags.get(0x8827)
        c.add("ISO", iso[0] if isinstance(iso, tuple) else iso)
        focal = _ratio(tags.get(0x920A))
        if focal:
            eq = tags.get(0xA405)
            c.add("Focal length", f"{focal:g} mm" + (f" (≈ {eq} mm full-frame)" if eq and int(eq) != round(focal) else ""))
        bias = _ratio(tags.get(0x9204))
        c.add("Exposure bias", f"{bias:+.1f} EV" if bias else "")
        flash = tags.get(0x9209)
        if isinstance(flash, int):
            c.add("Flash", "Fired" if flash & 1 else "Did not fire")
        wb = tags.get(0xA403)
        c.add("White balance", {0: "Auto", 1: "Manual"}.get(wb, ""))
        c.add("Orientation", ORIENTATION.get(tags.get(0x0112), ""))
        c.add("Software", str(tags.get(0x0131, "")).strip())
        c.add("Artist", str(tags.get(0x013B, "")).strip())
        c.add("Copyright", str(tags.get(0x8298, "")).strip())
        try:
            gps = exif.get_ifd(0x8825)
        except Exception:  # noqa: BLE001
            gps = {}
        if gps:
            where = _gps(gps)
            if where:
                g = info.section("Location")
                g.add("Position", where)
                alt = _ratio(gps.get(6))
                if alt:
                    g.add("Altitude", f"{-alt if gps.get(5) == 1 else alt:.0f} m")
    return True


class ImageProvider(InfoProvider):
    name = "Images"
    priority = 75
    local_only = False

    def __init__(self) -> None:
        formats = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
        if "jpeg" in formats:
            formats |= {"jpg", "jfif"}
        if "tiff" in formats:
            formats.add("tif")
        self.exts = frozenset(formats - {"pdf"})

    def extract(self, src: Source) -> Optional[Info]:
        reader, keep = _reader(src)
        size = reader.size()
        fmt = bytes(reader.format()).decode().upper() or src.entry.ext.upper()
        fmt = {"JPEG": "JPEG", "TIFF": "TIFF", "SVG": "SVG", "SVGZ": "SVG"}.get(fmt, fmt)
        info = Info(kind=f"{fmt} image")
        s = info.section("Image")
        if size.isValid() and size.width() > 0:
            w, h = size.width(), size.height()
            mp = w * h / 1_000_000
            s.add("Dimensions", f"{w:,} × {h:,} pixels" + (f"  ({mp:.1f} MP)" if mp >= 0.5 else ""))
            s.add("Aspect ratio", tools.aspect(w, h))
        s.add("Format", fmt)
        frames = reader.imageCount()
        if reader.supportsAnimation() and frames > 1:
            s.add("Animation", f"{frames:,} frames" + (", loops forever" if reader.loopCount() < 0 else ""))
        elif frames > 1:
            s.add("Pages / layers", f"{frames:,}")
        texts = [(k, reader.text(k)) for k in reader.textKeys()]
        has_pillow = pillow_details(src.path if src.local else keep[1], info, src.entry.ext)
        if texts:
            t = info.section("Embedded text")
            for key, value in texts[:10]:
                t.add(key, tools.clip(value, 800))
        if not has_pillow and src.entry.ext in EXIF_EXTS:
            info.note = "Install Pillow (pip install pillow) to see camera details: lens, exposure, GPS…"
        info.preview = src.path if src.local else keep[1]
        return info


# ======================================================================================
# PDF
# ======================================================================================
def _qtpdf():
    try:
        from PySide6.QtPdf import QPdfDocument
        return QPdfDocument
    except ImportError:
        return None


def render_pdf_page(path: str, width: int = 640) -> Optional[QImage]:
    """Worker thread: the first page as an image."""
    QPdfDocument = _qtpdf()
    if QPdfDocument is not None:
        doc = QPdfDocument(None)
        try:
            if doc.load(path) != QPdfDocument.Error.None_ or doc.pageCount() < 1:
                return None
            pt = doc.pagePointSize(0)
            height = int(width * pt.height() / pt.width()) if pt.width() > 0 else int(width * 1.414)
            image = doc.render(0, QSize(width, height))
            if image.isNull():
                return None
            page = QImage(image.size(), QImage.Format.Format_RGB32)     # PDF pages are transparent: paper is white
            page.fill(0xFFFFFF)
            painter = QPainter(page)
            painter.drawImage(0, 0, image)
            painter.end()
            return page
        finally:
            doc.close()
    exe = tools.which("pdftoppm")
    if exe:
        data = tools.run([exe, "-jpeg", "-f", "1", "-l", "1", "-scale-to", str(width), path], timeout=15)
        return QImage.fromData(data) if data else None
    return None


def _pdfinfo(path: str, token) -> dict:
    out = tools.run([tools.which("pdfinfo"), "-isodates", path], timeout=10, token=token)
    fields = {}
    for line in out.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _iso(text: str) -> str:
    return text[:16].replace("T", " ") if text else ""


class PdfProvider(InfoProvider):
    name = "PDF"
    exts = frozenset({"pdf", "ai"})
    priority = 75

    def available(self) -> bool:
        return bool(tools.which("pdfinfo") or _qtpdf())

    def missing_hint(self) -> str:
        return "Install poppler-utils (pdfinfo) to see PDF details."

    def extract(self, src: Source) -> Optional[Info]:
        info = Info(kind="PDF document")
        d = info.section("Document")
        p = info.section("Pages")
        if tools.which("pdfinfo"):
            f = _pdfinfo(src.path, src.token)
            if "Pages" not in f and "Incorrect password" in str(f):
                d.add("Protected", "Password needed to open it")
                return info
            d.add("Title", f.get("Title"))
            d.add("Author", f.get("Author"))
            d.add("Subject", f.get("Subject"))
            d.add("Keywords", f.get("Keywords"))
            d.add("Created", _iso(f.get("CreationDate", "")))
            d.add("Modified", _iso(f.get("ModDate", "")))
            d.add("Made with", " / ".join(dict.fromkeys(x for x in (f.get("Creator"), f.get("Producer")) if x)))
            d.add("PDF version", f.get("PDF version"))
            enc = f.get("Encrypted", "no")
            d.add("Encrypted", "" if enc.startswith("no") else enc.replace("yes", "Yes", 1))
            d.add("Tagged (accessible)", "Yes" if f.get("Tagged") == "yes" else "")
            d.add("Form", {"AcroForm": "Fillable form", "XFA": "XFA form"}.get(f.get("Form", ""), ""))
            d.add("JavaScript", "Yes" if f.get("JavaScript") == "yes" else "")
            d.add("Fast web view", "Yes" if f.get("Optimized") == "yes" else "")
            p.add("Pages", f"{int(f['Pages']):,}" if f.get("Pages", "").isdigit() else f.get("Pages"))
            size = f.get("Page size", "")
            if size:
                import re
                m = re.match(r"([\d.]+) x ([\d.]+) pts(?: \((\w+)\))?", size)
                if m:
                    w, h = float(m.group(1)) * 25.4 / 72, float(m.group(2)) * 25.4 / 72
                    p.add("Page size", f"{w:.0f} × {h:.0f} mm" + (f"  ({paper_name(w, h)})" if paper_name(w, h) else ""))
                else:
                    p.add("Page size", size)
        else:
            QPdfDocument = _qtpdf()
            doc = QPdfDocument(None)
            try:
                err = doc.load(src.path)
                if err == QPdfDocument.Error.IncorrectPassword:
                    d.add("Protected", "Password needed to open it")
                    return info
                if err != QPdfDocument.Error.None_:
                    raise OSError(f"Can't read this PDF ({err.name})")
                F = QPdfDocument.MetaDataField
                for label, field in (("Title", F.Title), ("Author", F.Author), ("Subject", F.Subject),
                                     ("Keywords", F.Keywords)):
                    d.add(label, str(doc.metaData(field) or "").strip())
                for label, field in (("Created", F.CreationDate), ("Modified", F.ModificationDate)):
                    value = doc.metaData(field)
                    if hasattr(value, "toString"):
                        value = value.toString("yyyy-MM-dd HH:mm") if value.isValid() else ""
                    d.add(label, value)
                d.add("Made with", " / ".join(dict.fromkeys(
                    x for x in (str(doc.metaData(F.Creator) or ""), str(doc.metaData(F.Producer) or "")) if x)))
                p.add("Pages", f"{doc.pageCount():,}")
                pt = doc.pagePointSize(0)
                if pt.width() > 0:
                    w, h = pt.width() * 25.4 / 72, pt.height() * 25.4 / 72
                    p.add("Page size", f"{w:.0f} × {h:.0f} mm" + (f"  ({paper_name(w, h)})" if paper_name(w, h) else ""))
            finally:
                doc.close()
        path = src.path
        if _qtpdf() or tools.which("pdftoppm"):
            info.preview = lambda token=None: render_pdf_page(path)
        return info


_registered = False


def register_qt_providers() -> None:
    global _registered
    if not _registered:
        _registered = True
        register(ImageProvider())
        register(PdfProvider())

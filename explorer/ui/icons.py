"""
IconProvider - every icon in the app comes from here.

* Folders are drawn by us as vectors (``FolderIconEngine``), so they can be
  any colour (folder colouring) and stay crisp at every zoom level.
* Files use your system's icons (Windows/macOS file icons, or the Linux
  icon theme via the file's MIME type). Where none exists we draw a page
  with a coloured band and the extension (``FileIconEngine``).
* Drives, network locations and "Computer" use Qt's standard icons.

All icons are cached, so asking for the icon of 100,000 files is cheap.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QFileInfo, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QIconEngine, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtCore import QMimeDatabase
from PySide6.QtWidgets import QApplication, QFileIconProvider, QStyle

from ..core import cloud
from ..core.fileinfo import FileEntry
from ..core.platform_info import IS_LINUX
from .theme import shade

CATEGORY_COLORS = {
    "image": "#4caf50", "video": "#ab47bc", "audio": "#ff9800", "archive": "#a1785c",
    "code": "#42a5f5", "document": "#e5534b", "text": "#90a4ae", "executable": "#26a69a",
    "other": "#78909c",
}
# Files whose icon is specific to the FILE (not its type): ask the OS each time.
PER_FILE_ICON_EXTS = {"exe", "lnk", "ico", "app", "desktop", "url", "appimage"}


class _PaintedEngine(QIconEngine):
    """Base for icons drawn with QPainter at whatever size is asked for."""

    def __init__(self) -> None:
        super().__init__()
        self._cache: Dict[Tuple[int, int, int], QPixmap] = {}

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        raise NotImplementedError

    def paint(self, painter: QPainter, rect: QRect, mode, state) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_icon(painter, QRectF(rect), mode == QIcon.Mode.Disabled)
        painter.restore()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        key = (size.width(), size.height(), int(mode.value) if hasattr(mode, "value") else int(mode))
        pm = self._cache.get(key)
        if pm is None:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
            p.end()
            self._cache[key] = pm
        return pm

    def actualSize(self, size: QSize, mode, state) -> QSize:
        return size


class FolderIconEngine(_PaintedEngine):
    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color

    def clone(self):
        return FolderIconEngine(self.color)

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        s = min(r.width(), r.height())
        x0 = r.x() + (r.width() - s) / 2
        y0 = r.y() + (r.height() - s) / 2
        base = QColor(shade(self.color, 0.6) if dim else self.color)

        def pt(fx, fy):
            return QPointF(x0 + s * fx, y0 + s * fy)
        radius = max(1.0, s * 0.05)
        # back plate with the tab
        back = QPainterPath()
        back.moveTo(pt(0.06, 0.24))
        back.quadTo(pt(0.06, 0.16), pt(0.14, 0.16))
        back.lineTo(pt(0.38, 0.16))
        back.lineTo(pt(0.46, 0.25))
        back.lineTo(pt(0.86, 0.25))
        back.quadTo(pt(0.94, 0.25), pt(0.94, 0.33))
        back.lineTo(pt(0.94, 0.80))
        back.lineTo(pt(0.06, 0.80))
        back.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base.darker(135))
        p.drawPath(back)
        # front, with a soft vertical gradient
        front = QRectF(pt(0.06, 0.33), pt(0.94, 0.84))
        grad = QLinearGradient(front.topLeft(), front.bottomLeft())
        grad.setColorAt(0, base.lighter(112))
        grad.setColorAt(1, base)
        p.setBrush(grad)
        p.drawRoundedRect(front, radius, radius)
        if s >= 24:   # highlight line
            p.setPen(QPen(base.lighter(135), max(1.0, s / 64)))
            p.drawLine(pt(0.09, 0.355), pt(0.91, 0.355))


class FileIconEngine(_PaintedEngine):
    def __init__(self, band: str, label: str, page: str) -> None:
        super().__init__()
        self.band, self.label, self.page = band, label, page

    def clone(self):
        return FileIconEngine(self.band, self.label, self.page)

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        s = min(r.width(), r.height())
        x0 = r.x() + (r.width() - s) / 2
        y0 = r.y() + (r.height() - s) / 2

        def pt(fx, fy):
            return QPointF(x0 + s * fx, y0 + s * fy)
        page = QColor(self.page)
        edge = page.darker(150)
        body = QPainterPath()
        body.moveTo(pt(0.2, 0.06))
        body.lineTo(pt(0.6, 0.06))
        body.lineTo(pt(0.82, 0.28))
        body.lineTo(pt(0.82, 0.94))
        body.lineTo(pt(0.2, 0.94))
        body.closeSubpath()
        p.setPen(QPen(edge, max(1.0, s / 48)))
        p.setBrush(page)
        p.drawPath(body)
        fold = QPainterPath()
        fold.moveTo(pt(0.6, 0.06))
        fold.lineTo(pt(0.6, 0.28))
        fold.lineTo(pt(0.82, 0.28))
        fold.closeSubpath()
        p.setBrush(edge)
        p.drawPath(fold)
        band = QColor(self.band)
        if dim:
            band = band.darker(130)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(band)
        p.drawRect(QRectF(pt(0.2, 0.58), pt(0.82, 0.80)))
        if s >= 32 and self.label:
            font = QFont(QApplication.font())
            font.setBold(True)
            font.setPixelSize(max(6, int(s * 0.14)))
            p.setFont(font)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(pt(0.2, 0.58), pt(0.82, 0.80)), Qt.AlignmentFlag.AlignCenter, self.label[:4].upper())


class ViewModeIconEngine(_PaintedEngine):
    """Simple line drawings for the four view modes (used when there is no icon theme)."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def clone(self):
        return ViewModeIconEngine(self.mode)

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        s = min(r.width(), r.height())
        x0, y0 = r.x() + (r.width() - s) / 2, r.y() + (r.height() - s) / 2
        color = QColor("#8c95a6") if dim else QColor("#9aa6b8")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)

        def box(fx, fy, fw, fh):
            p.drawRoundedRect(QRectF(x0 + s * fx, y0 + s * fy, s * fw, s * fh), s * 0.04, s * 0.04)
        if self.mode == "icons":
            for fx in (0.12, 0.56):
                for fy in (0.12, 0.56):
                    box(fx, fy, 0.32, 0.32)
        elif self.mode == "list":
            for fx in (0.1, 0.55):
                for fy in (0.18, 0.44, 0.70):
                    box(fx, fy, 0.08, 0.12)
                    box(fx + 0.12, fy + 0.02, 0.22, 0.08)
        elif self.mode == "details":
            for fy in (0.16, 0.36, 0.56, 0.76):
                box(0.1, fy, 0.1, 0.1)
                box(0.26, fy + 0.02, 0.64, 0.06)
        else:  # tree
            for fx, fy in ((0.1, 0.14), (0.3, 0.36), (0.3, 0.58), (0.1, 0.80)):
                box(fx, fy, 0.1, 0.1)
                box(fx + 0.16, fy + 0.02, 0.74 - fx, 0.06)


class SymbolIconEngine(_PaintedEngine):
    """Simple line icons (back, forward, up, refresh, help) drawn in the
    current theme's text colour, so they are readable on every theme -
    the desktop's icon set follows the DESKTOP theme, which may be light
    while PyExplorer is dark. In the "system" theme the desktop's own icon
    (``native``) is used instead. Colours are read at paint time, so a theme
    switch recolours them without rebuilding anything."""

    def __init__(self, ctx, shape: str, native: QIcon, tint: str = "fg") -> None:
        super().__init__()
        self.ctx = ctx
        self.shape = shape
        self.native = native
        self.tint = tint                        # palette colour to draw with: "fg" or "accent"

    def _use_native(self) -> bool:
        return self.ctx.theme.name == "system" and not self.native.isNull()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        if self._use_native():
            return self.native.pixmap(size, mode, state)
        pal = self.ctx.theme.palette
        key = (size.width(), size.height(), int(mode.value) if hasattr(mode, "value") else int(mode),
               pal.get(self.tint, ""), pal.get("fg_dim", ""))
        pm = self._cache.get(key)
        if pm is None:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
            p.end()
            self._cache[key] = pm
        return pm

    def paint(self, painter: QPainter, rect: QRect, mode, state) -> None:
        if self._use_native():
            self.native.paint(painter, rect, Qt.AlignmentFlag.AlignCenter, mode, state)
            return
        super().paint(painter, rect, mode, state)

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        pal = self.ctx.theme.palette
        color = QColor(pal.get("fg_dim" if dim else self.tint, "#888888"))
        if dim:
            color.setAlphaF(0.45)
        s = min(r.width(), r.height()) / 16.0                   # drawn on a 16x16 grid
        ox, oy = r.x() + (r.width() - 16 * s) / 2, r.y() + (r.height() - 16 * s) / 2

        def pt(x: float, y: float) -> QPointF:
            return QPointF(ox + x * s, oy + y * s)
        pen = QPen(color, max(1.0, 1.5 * s))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if self.shape in ("back", "forward"):
            sign = 1 if self.shape == "back" else -1
            cx = 8
            path.moveTo(pt(cx + 5 * sign, 8))
            path.lineTo(pt(cx - 5 * sign, 8))
            path.moveTo(pt(cx - 1 * sign, 4))
            path.lineTo(pt(cx - 5 * sign, 8))
            path.lineTo(pt(cx - 1 * sign, 12))
        elif self.shape == "up":
            path.moveTo(pt(8, 13))
            path.lineTo(pt(8, 3))
            path.moveTo(pt(4, 7))
            path.lineTo(pt(8, 3))
            path.lineTo(pt(12, 7))
        elif self.shape == "refresh":
            box = QRectF(pt(3, 3), pt(13, 13))
            path.arcMoveTo(box, 60)
            path.arcTo(box, 60, 290)
            tip = pt(8 + 5 * 0.5, 8 - 5 * 0.866)             # end of the arc (60 degrees)
            path.moveTo(tip)
            path.lineTo(tip + QPointF(-3.2 * s, -0.6 * s))
            path.moveTo(tip)
            path.lineTo(tip + QPointF(0.4 * s, 3.1 * s))
        elif self.shape == "search":                         # magnifier
            p.drawEllipse(QRectF(pt(2.5, 2.5), pt(10.5, 10.5)))
            path.moveTo(pt(9.6, 9.6))
            path.lineTo(pt(13.5, 13.5))
        elif self.shape == "rename":                         # a text cursor next to a line of text
            path.moveTo(pt(2.5, 11))
            path.lineTo(pt(8.5, 11))
            path.moveTo(pt(2.5, 7.5))
            path.lineTo(pt(7, 7.5))
            path.moveTo(pt(11.5, 3))
            path.lineTo(pt(11.5, 13))
            path.moveTo(pt(10, 3))
            path.lineTo(pt(13, 3))
            path.moveTo(pt(10, 13))
            path.lineTo(pt(13, 13))
        elif self.shape == "help":
            p.drawEllipse(QRectF(pt(2, 2), pt(14, 14)))
            path.moveTo(pt(6.2, 6.4))
            path.cubicTo(pt(6.2, 4.2), pt(9.8, 4.2), pt(9.8, 6.4))
            path.cubicTo(pt(9.8, 7.8), pt(8, 8), pt(8, 9.6))
            p.drawPath(path)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pt(8, 11.8), 0.95 * s, 0.95 * s)
            return
        p.drawPath(path)


class CloudBadgeEngine(_PaintedEngine):
    """A plain cloud shape: the badge used when the sync app's own icon is not available."""

    def clone(self):
        return CloudBadgeEngine()

    def paint_icon(self, p: QPainter, r: QRectF, dim: bool) -> None:
        s = min(r.width(), r.height())
        x0, y0 = r.x() + (r.width() - s) / 2, r.y() + (r.height() - s) / 2
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)        # overlapping shapes merge (no holes)
        path.addEllipse(QRectF(x0 + s * 0.06, y0 + s * 0.42, s * 0.36, s * 0.36))
        path.addEllipse(QRectF(x0 + s * 0.26, y0 + s * 0.20, s * 0.46, s * 0.46))
        path.addEllipse(QRectF(x0 + s * 0.56, y0 + s * 0.38, s * 0.38, s * 0.38))
        path.addRoundedRect(QRectF(x0 + s * 0.22, y0 + s * 0.52, s * 0.56, s * 0.26), s * 0.05, s * 0.05)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#8c95a6") if dim else QColor("#3d8fe0"))
        p.drawPath(path.simplified())


class BadgedIconEngine(QIconEngine):
    """A file/folder icon with a cloud-state badge in its bottom-left corner.

        ONLINE (cloud only): file icon faded, badge greyed out
        LOCAL  (downloaded): badge in full colour
        PINNED (always kept): full colour + a small dot
    """

    def __init__(self, base: QIcon, badge: QIcon, state: int, ring: str, dot: str) -> None:
        super().__init__()
        self.base, self.badge, self.state, self.ring, self.dot = base, badge, state, ring, dot
        self._cache: Dict[Tuple[int, int, int], QPixmap] = {}

    def clone(self):
        return BadgedIconEngine(self.base, self.badge, self.state, self.ring, self.dot)

    def actualSize(self, size: QSize, mode, state) -> QSize:
        return size

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        key = (size.width(), size.height(), int(mode.value) if hasattr(mode, "value") else int(mode))
        pm = self._cache.get(key)
        if pm is None:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
            p.end()
            self._cache[key] = pm
        return pm

    def paint(self, p: QPainter, rect: QRect, mode, state) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        online = self.state == cloud.ONLINE
        if online:
            p.setOpacity(0.55)
        self.base.paint(p, rect, Qt.AlignmentFlag.AlignCenter, mode, state)
        p.setOpacity(1.0)
        s = min(rect.width(), rect.height())
        b = int(max(9, min(36, s * 0.36)))           # badge size: readable, never dominant
        badge_rect = QRect(rect.left(), rect.bottom() - b + 1, b, b)
        # a soft ring in the background colour keeps the badge readable on any icon
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.ring))
        p.drawEllipse(QRectF(badge_rect).adjusted(-1, -1, 1, 1))
        inner = badge_rect.adjusted(1, 1, -1, -1)
        badge_mode = QIcon.Mode.Disabled if online else QIcon.Mode.Normal
        if online:
            p.setOpacity(0.85)
        self.badge.paint(p, inner, Qt.AlignmentFlag.AlignCenter, badge_mode)
        p.setOpacity(1.0)
        if self.state == cloud.PINNED:
            d = max(4.0, b * 0.36)
            dot = QRectF(badge_rect.right() - d * 0.6, badge_rect.top() - d * 0.2, d, d)
            p.setBrush(QColor(self.ring))
            p.drawEllipse(dot.adjusted(-1, -1, 1, 1))
            p.setBrush(QColor(self.dot))
            p.drawEllipse(dot)
        p.restore()


class IconProvider:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._os = QFileIconProvider()
        self._mime = QMimeDatabase()
        self._folders: Dict[str, QIcon] = {}
        self._files: Dict[str, QIcon] = {}
        self._special: Dict[str, QIcon] = {}
        self._badged: Dict[tuple, QIcon] = {}
        self._apps: Dict[str, QIcon] = {}

    def clear(self) -> None:
        """Forget cached icons (after a theme change)."""
        self._folders.clear()
        self._files.clear()
        self._special.clear()
        self._badged.clear()

    # -- folders -----------------------------------------------------------------
    def folder_color(self, path: str) -> str:
        return self.ctx.folder_colors.get(path) or self.ctx.theme.palette.get("folder", "#e3b04b")

    def folder(self, color: Optional[str] = None) -> QIcon:
        color = color or self.ctx.theme.palette.get("folder", "#e3b04b")
        icon = self._folders.get(color)
        if icon is None:
            icon = self._folders[color] = QIcon(FolderIconEngine(color))
        return icon

    def folder_for(self, path: str) -> QIcon:
        return self.folder(self.folder_color(path))

    # -- special locations -----------------------------------------------------
    def special(self, kind: str) -> QIcon:
        """kind: computer | drive | removable | optical | phone | network | share | trash"""
        icon = self._special.get(kind)
        if icon is None:
            names = {"computer": ("computer", QFileIconProvider.IconType.Computer),
                     "drive": ("drive-harddisk", QFileIconProvider.IconType.Drive),
                     "removable": ("drive-removable-media", QFileIconProvider.IconType.Drive),
                     "network": ("network-server", QFileIconProvider.IconType.Network),
                     "optical": ("media-optical", QFileIconProvider.IconType.Drive),
                     "phone": ("phone", QFileIconProvider.IconType.Drive),
                     "share": ("folder-remote", QFileIconProvider.IconType.Network),
                     "trash": ("user-trash", QFileIconProvider.IconType.Trashcan)}
            theme_name, fallback = names.get(kind, names["drive"])
            icon = QIcon.fromTheme(theme_name)
            if icon.isNull():
                icon = self._os.icon(fallback)
            self._special[kind] = icon
        return icon

    def for_kind(self, kind: str, path: str = "") -> QIcon:
        """Icon for a sidebar item / tab, by its kind."""
        if kind == "cloud":
            root = self.ctx.cloud.find(path) if path else None
            return self.cloud_app(root)
        if kind == "folder":
            return self.folder_for(path) if path else self.folder()
        return self.special(kind)

    # -- cloud ---------------------------------------------------------------------
    def cloud_app(self, root) -> QIcon:
        """The sync app's own icon, or a plain cloud.
        Windows/macOS: read from the installed program (root.app_path).
        Linux: from the icon theme, by the names in root.icon_names."""
        names = tuple(getattr(root, "icon_names", ()) or ()) if root is not None else ()
        key = root.app_path if (root is not None and root.app_path) else (names or "")
        icon = self._apps.get(key)
        if icon is None:
            icon = QIcon()
            if isinstance(key, str) and key and os.path.exists(key):
                icon = self._os.icon(QFileInfo(key))
            for name in names:
                if not icon.isNull():
                    break
                if QIcon.hasThemeIcon(name):
                    icon = QIcon.fromTheme(name)
            if icon.isNull():
                icon = QIcon(CloudBadgeEngine())
            self._apps[key] = icon
        return icon

    def with_cloud_badge(self, base: QIcon, base_key, root, state: int) -> QIcon:
        """``base`` with a cloud-state badge. ``base_key`` identifies base for caching."""
        key = (base_key, root.app_path if root else "", state)
        icon = self._badged.get(key)
        if icon is None:
            pal = self.ctx.theme.palette
            icon = QIcon(BadgedIconEngine(base, self.cloud_app(root), state, pal.get("bg", "#ffffff"),
                                          pal.get("accent", "#3d8fe0")))
            if len(self._badged) > 4000:
                self._badged.clear()
            self._badged[key] = icon
        return icon

    def icon_key(self, entry: FileEntry):
        """A cache key matching what for_entry() returns."""
        kind = entry.extra.get("kind") if entry.extra else None
        if kind == "cloud":                 # cloud storage in the Computer view: its app's icon
            return ("cloud", entry.path)
        if kind and kind != "folder":
            return ("special", kind)
        if entry.is_dir:
            return ("folder", self.folder_color(entry.path))
        if "://" not in entry.path and entry.ext in PER_FILE_ICON_EXTS:
            return ("perfile", entry.path)
        return ("file", entry.ext, entry.category)

    # -- files -------------------------------------------------------------------
    def _painted_file(self, entry: FileEntry) -> QIcon:
        key = "painted:" + entry.category + ":" + entry.ext
        icon = self._files.get(key)
        if icon is None:
            band = CATEGORY_COLORS.get(entry.category, CATEGORY_COLORS["other"])
            icon = self._files[key] = QIcon(FileIconEngine(band, entry.ext, self.ctx.theme.palette.get("page", "#ffffff")))
        return icon

    def file(self, entry: FileEntry) -> QIcon:
        if not self.ctx.settings.get("appearance.system_icons", True):
            return self._painted_file(entry)
        local = "://" not in entry.path
        if local and entry.ext in PER_FILE_ICON_EXTS:
            icon = self._os.icon(QFileInfo(entry.path))
            return icon if not icon.isNull() else self._painted_file(entry)
        key = "sys:" + (entry.ext or ("" if local else "remote"))
        icon = self._files.get(key)
        if icon is None:
            icon = QIcon()
            if IS_LINUX or not local:
                mime = self._mime.mimeTypeForFile(entry.name, QMimeDatabase.MatchMode.MatchExtension)
                icon = QIcon.fromTheme(mime.iconName())
                if icon.isNull():
                    icon = QIcon.fromTheme(mime.genericIconName())
            elif local:
                icon = self._os.icon(QFileInfo(entry.path))   # first file of this type
            if icon.isNull():
                icon = self._painted_file(entry)
            self._files[key] = icon
        return icon

    def for_entry(self, entry: FileEntry) -> QIcon:
        kind = entry.extra.get("kind") if entry.extra else None
        if kind and kind != "folder":
            return self.for_kind(kind, entry.path)
        if entry.is_dir:
            return self.folder_for(entry.path)
        return self.file(entry)

    # -- toolbar / menu icons ------------------------------------------------------
    def view_mode(self, mode: str) -> QIcon:
        """Icon for a view mode: the icon theme's, or a simple drawn one."""
        theme_names = {"icons": "view-list-icons", "list": "view-list-text",
                       "details": "view-list-details", "tree": "view-list-tree"}
        icon = QIcon.fromTheme(theme_names.get(mode, ""))
        return icon if not icon.isNull() else QIcon(ViewModeIconEngine(mode))

    # -- toolbar / menu icons (continued) -------------------------------------------
    def symbol(self, shape: str, theme_name: str = "", fallback: Optional[QStyle.StandardPixmap] = None,
               force: bool = False, tint: str = "fg") -> QIcon:
        """A line icon in the theme's colours (see SymbolIconEngine); the desktop's
        ``theme_name`` icon in the "system" theme unless ``force``.
        shape: back|forward|up|refresh|help|search|rename. tint: "fg" or "accent"."""
        key = f"symbol:{shape}:{force}:{tint}"
        icon = self._special.get(key)
        if icon is None:
            native = QIcon() if force else self.action(theme_name, fallback)
            icon = self._special[key] = QIcon(SymbolIconEngine(self.ctx, shape, native, tint))
        return icon

    def action(self, theme_name: str, fallback: Optional[QStyle.StandardPixmap] = None) -> QIcon:
        """Icon-theme icon by name; else Qt's built-in ``fallback`` (or no icon)."""
        key = "action:" + theme_name
        icon = self._special.get(key)
        if icon is None:
            icon = QIcon.fromTheme(theme_name)
            if icon.isNull() and fallback is not None:
                icon = QApplication.style().standardIcon(fallback)
            self._special[key] = icon
        return icon

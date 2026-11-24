"""
The "how full is this drive" bar, drawn behind a drive's name (sidebar rows,
Computer view). One function so both places look the same.

    paint_usage_bar(painter, rect, usage.fraction, palette, on_top=False)

A faint full-width track in the theme's accent colour, and the used part in a
stronger shade of it (stronger still above FULL, so a nearly full drive
stands out without leaving the theme's colours). Drawn BEFORE the item's text
(``on_top=False``) it sits behind the text; rows whose own background would
cover it (hovered / selected) draw it again on top, more transparent.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath

FULL = 0.9
TRACK_ALPHA = 0.10
USED_ALPHA = 0.32
FULL_ALPHA = 0.50
ON_TOP_FACTOR = 0.6          # over a hover/selection background: lighter, so the text stays readable


def paint_usage_bar(painter: QPainter, rect, fraction: float, palette: dict, on_top: bool = False) -> None:
    accent = QColor(palette.get("accent", "#3d8ee6"))
    r = QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5)
    if r.width() <= 4 or r.height() <= 2:
        return
    radius = min(4.0, r.height() / 2)
    factor = ON_TOP_FACTOR if on_top else 1.0
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    clip = QPainterPath()
    clip.addRoundedRect(r, radius, radius)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    track = QColor(accent)
    track.setAlphaF(TRACK_ALPHA * factor)
    painter.fillRect(r, track)
    used = QColor(accent)
    used.setAlphaF((FULL_ALPHA if fraction >= FULL else USED_ALPHA) * factor)
    painter.fillRect(QRectF(r.x(), r.y(), r.width() * max(0.0, min(1.0, fraction)), r.height()), used)
    painter.restore()

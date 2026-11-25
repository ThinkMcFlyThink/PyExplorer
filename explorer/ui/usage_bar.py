"""
The "how full is this drive" bar: a thin capacity bar under a drive's name
(sidebar rows, the Computer view). One module so every place looks the same.

    rect = bar_rect(text_rect, row_rect)            where it goes in a row
    paint_usage_bar(painter, rect, usage.fraction, palette, selected=False)

A faint full-length track with the used part over it in the theme's accent
colour - in the theme's error colour above FULL, so a nearly full drive
stands out. On a selected row it is drawn in the selection's text colour, so
it stays visible on any selection colour. Rows that show a bar are ROW_EXTRA
pixels taller, which leaves room for it under the text.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter

FULL = 0.9
BAR_HEIGHT = 3
ROW_EXTRA = 8            # extra height of a row with a bar
BOTTOM_GAP = 3           # space under the bar, to the row's edge
RIGHT_GAP = 10           # space after the bar, to the row's right edge


def bar_rect(text_rect, row_rect, text_margin: int = 3) -> QRectF:
    """The bar's place in a row: under the text, from where its letters start (Qt draws them
    ``text_margin`` px inside the text rectangle) to near the row's end."""
    row = QRectF(row_rect)
    left = QRectF(text_rect).left() + text_margin
    return QRectF(left, row.bottom() - BOTTOM_GAP - BAR_HEIGHT, max(0.0, row.right() - RIGHT_GAP - left), BAR_HEIGHT)


def text_margin(style, widget) -> int:
    """How far inside its rectangle an item view draws text (Qt: focus frame margin + 1)."""
    from PySide6.QtWidgets import QStyle
    return style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, widget) + 1


def paint_usage_bar(painter: QPainter, rect, fraction: float, palette: dict, selected: bool = False) -> None:
    r = QRectF(rect)
    if r.width() < 8 or r.height() < 1:
        return
    fraction = max(0.0, min(1.0, fraction))
    if selected:
        used = QColor(palette.get("select_fg", "#ffffff"))
        track = QColor(used)
        track.setAlphaF(0.30)
    else:
        used = QColor(palette.get("error" if fraction >= FULL else "accent", "#3d8ee6"))
        track = QColor(palette.get("border", "#888888"))
    radius = r.height() / 2
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(track)
    painter.drawRoundedRect(r, radius, radius)
    if fraction > 0:
        painter.setBrush(used)
        painter.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * fraction), r.height()), radius, radius)
    painter.restore()

"""SpeedPlot - a small area chart of a transfer's speed over time, in the theme's accent."""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.formatting import human_size


class SpeedPlot(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.samples: List[Tuple[float, float]] = []      # (seconds, bytes per second)
        self.average = 0.0
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(64)

    def set_samples(self, samples: List[Tuple[float, float]], average: float) -> None:
        if samples != self.samples or average != self.average:
            self.samples = list(samples)
            self.average = average
            self.update()

    def paintEvent(self, event) -> None:
        pal = self.ctx.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(pal.get("border", "#555555")), 1))
        p.setBrush(QColor(pal.get("bg", "#202020")))
        p.drawRoundedRect(r, 4, 4)
        dim = QColor(pal.get("fg_dim", "#888888"))
        if len(self.samples) < 2:
            p.setPen(dim)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Speed appears here while copying")
            return
        top = max(s for _, s in self.samples) or 1.0
        t0, t1 = self.samples[0][0], self.samples[-1][0]
        span = (t1 - t0) or 1.0
        plot = r.adjusted(2, 14, -2, -2)

        def pt(t: float, s: float) -> QPointF:
            return QPointF(plot.left() + (t - t0) / span * plot.width(), plot.bottom() - s / top * plot.height())
        line = QPainterPath(pt(*self.samples[0]))
        for t, s in self.samples[1:]:
            line.lineTo(pt(t, s))
        area = QPainterPath(line)
        area.lineTo(QPointF(plot.right(), plot.bottom()))
        area.lineTo(QPointF(plot.left(), plot.bottom()))
        area.closeSubpath()
        accent = QColor(pal.get("accent", "#3d8ee6"))
        fill = QColor(accent)
        fill.setAlphaF(0.25)
        p.fillPath(area, fill)
        p.setPen(QPen(accent, 1.5))
        p.drawPath(line)
        if self.average > 0:                               # dashed line at the average
            y = plot.bottom() - min(1.0, self.average / top) * plot.height()
            pen = QPen(dim, 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(dim)
        font = p.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        p.drawText(r.adjusted(6, 1, -6, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   f"peak {human_size(top)}/s")
        if self.average > 0:
            p.drawText(r.adjusted(6, 1, -6, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                       f"- - average {human_size(self.average)}/s")

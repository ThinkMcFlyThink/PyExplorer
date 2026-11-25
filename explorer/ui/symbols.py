"""
symbols - PyExplorer's line icons, as plain drawing functions.

Every icon is drawn on the same 16 x 16 grid with the same pen (1.5 units,
round caps and joins), so they all share one weight and one visual size and
stay sharp at any size or screen scale:

    @shape("home")
    def _(c: Canvas) -> None:
        c.line((2, 8), (8, 2.5), (14, 8))          # the roof
        ...

    draw(painter, rect, "home", QColor("#333"))     paint one icon
    names()                                         every icon there is

Adding an icon = one decorated function here. Nothing else changes:
``icons.IconProvider.symbol(name)`` turns any name into a QIcon in the
theme's colours (see icons.SymbolIconEngine). No widgets, no caching here.

Guidelines that keep the set consistent: keep shapes inside 1.5..14.5, centre
them on (8, 8), use whole or half units, and prefer outlines to fills.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

Point = Tuple[float, float]
GRID = 16.0
STROKE = 1.5


class Canvas:
    """Draws in grid units (0..16) onto a real rectangle of any size."""

    def __init__(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        self.p = painter
        side = min(rect.width(), rect.height())
        self.s = side / GRID
        self.ox = rect.x() + (rect.width() - side) / 2
        self.oy = rect.y() + (rect.height() - side) / 2
        self.color = QColor(color)
        pen = QPen(self.color, max(1.0, STROKE * self.s))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.pen = pen
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def pt(self, x: float, y: float) -> QPointF:
        return QPointF(self.ox + x * self.s, self.oy + y * self.s)

    def box(self, x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(self.ox + x * self.s, self.oy + y * self.s, w * self.s, h * self.s)

    # -- outlines --------------------------------------------------------------------
    def line(self, *points: Point) -> None:
        """An open line through the points."""
        path = QPainterPath(self.pt(*points[0]))
        for x, y in points[1:]:
            path.lineTo(self.pt(x, y))
        self.p.drawPath(path)

    def poly(self, *points: Point) -> None:
        """A closed outline through the points."""
        path = QPainterPath(self.pt(*points[0]))
        for x, y in points[1:]:
            path.lineTo(self.pt(x, y))
        path.closeSubpath()
        self.p.drawPath(path)

    def rect(self, x: float, y: float, w: float, h: float, r: float = 1.5) -> None:
        self.p.drawRoundedRect(self.box(x, y, w, h), r * self.s, r * self.s)

    def circle(self, cx: float, cy: float, r: float) -> None:
        self.p.drawEllipse(self.pt(cx, cy), r * self.s, r * self.s)

    def arc(self, cx: float, cy: float, r: float, start: float, span: float) -> None:
        """Degrees, Qt's way: 0 = 3 o'clock, positive = counter-clockwise."""
        path = QPainterPath()
        box = self.box(cx - r, cy - r, 2 * r, 2 * r)
        path.arcMoveTo(box, start)
        path.arcTo(box, start, span)
        self.p.drawPath(path)

    def curve(self, start: Point, *segments: Tuple[Point, Point, Point]) -> None:
        """Cubic Bezier segments: each is (control 1, control 2, end)."""
        path = QPainterPath(self.pt(*start))
        for c1, c2, end in segments:
            path.cubicTo(self.pt(*c1), self.pt(*c2), self.pt(*end))
        self.p.drawPath(path)

    # -- fills -------------------------------------------------------------------------
    def dot(self, cx: float, cy: float, r: float = 1.0) -> None:
        self.p.save()
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(self.color)
        self.p.drawEllipse(self.pt(cx, cy), r * self.s, r * self.s)
        self.p.restore()

    def fill_path(self, path: QPainterPath) -> None:
        self.p.save()
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(self.color)
        self.p.drawPath(path)
        self.p.restore()


_SHAPES: Dict[str, Callable[[Canvas], None]] = {}


def shape(*names: str):
    """Register a drawing function under one or more names."""
    def register(fn: Callable[[Canvas], None]) -> Callable[[Canvas], None]:
        for name in names:
            _SHAPES[name] = fn
        return fn
    return register


def names() -> List[str]:
    return sorted(_SHAPES)


def exists(name: str) -> bool:
    return name in _SHAPES


def draw(painter: QPainter, rect: QRectF, name: str, color: QColor) -> None:
    """Paint icon ``name`` centred in ``rect`` (a square of any size)."""
    fn = _SHAPES.get(name)
    if fn is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fn(Canvas(painter, QRectF(rect), color))
    painter.restore()


# ===========================================================================
# Navigation
# ===========================================================================
@shape("back")
def _back(c: Canvas) -> None:
    c.line((13, 8), (3, 8))
    c.line((7, 4), (3, 8), (7, 12))


@shape("forward")
def _forward(c: Canvas) -> None:
    c.line((3, 8), (13, 8))
    c.line((9, 4), (13, 8), (9, 12))


@shape("up")
def _up(c: Canvas) -> None:
    c.line((8, 13), (8, 3))
    c.line((4, 7), (8, 3), (12, 7))


@shape("down")
def _down(c: Canvas) -> None:
    c.line((8, 3), (8, 13))
    c.line((4, 9), (8, 13), (12, 9))


@shape("refresh")
def _refresh(c: Canvas) -> None:
    c.arc(8, 8, 5, 60, 290)
    tip = (8 + 5 * 0.5, 8 - 5 * 0.866)                     # where the arc starts (60 degrees)
    c.line((tip[0] - 3.2, tip[1] - 0.6), tip, (tip[0] + 0.4, tip[1] + 3.1))


@shape("home")
def _home(c: Canvas) -> None:
    c.line((2, 8), (8, 2.5), (14, 8))
    c.line((4, 6.5), (4, 13.5), (12, 13.5), (12, 6.5))
    c.line((6.75, 13.5), (6.75, 10), (9.25, 10), (9.25, 13.5))


@shape("computer")
def _computer(c: Canvas) -> None:
    c.rect(1.75, 2.5, 12.5, 8.75)
    c.line((8, 11.25), (8, 13.5))
    c.line((5, 13.5), (11, 13.5))


@shape("chevron-down")
def _chevron_down(c: Canvas) -> None:
    c.line((4, 6), (8, 10), (12, 6))


@shape("chevron-up")
def _chevron_up(c: Canvas) -> None:
    c.line((4, 10), (8, 6), (12, 10))


@shape("chevron-right")
def _chevron_right(c: Canvas) -> None:
    c.line((6, 4), (10, 8), (6, 12))


@shape("chevron-left")
def _chevron_left(c: Canvas) -> None:
    c.line((10, 4), (6, 8), (10, 12))


# ===========================================================================
# Tabs, windows, layout
# ===========================================================================
@shape("plus", "add")
def _plus(c: Canvas) -> None:
    c.line((8, 3), (8, 13))
    c.line((3, 8), (13, 8))


@shape("minus")
def _minus(c: Canvas) -> None:
    c.line((3, 8), (13, 8))


@shape("close")
def _close(c: Canvas) -> None:
    c.line((4, 4), (12, 12))
    c.line((12, 4), (4, 12))


@shape("check")
def _check(c: Canvas) -> None:
    c.line((3, 8.5), (6.5, 12), (13, 4.5))


@shape("more")
def _more(c: Canvas) -> None:
    for x in (3.5, 8, 12.5):
        c.dot(x, 8, 1.15)


@shape("tab-new")
def _tab_new(c: Canvas) -> None:
    c.rect(2, 2.5, 12, 11)
    c.line((2, 5.75), (14, 5.75))
    c.line((8, 7.75), (8, 11.75))
    c.line((6, 9.75), (10, 9.75))


@shape("sidebar")
def _sidebar(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((6.25, 3), (6.25, 13))


@shape("split")
def _split(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((8, 3), (8, 13))


@shape("terminal")
def _terminal(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((4.75, 6.25), (6.75, 8), (4.75, 9.75))
    c.line((8.25, 10), (11.25, 10))


@shape("statusbar")
def _statusbar(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((2, 10), (14, 10))


@shape("toolbar")
def _toolbar(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((2, 6), (14, 6))


@shape("fullscreen")
def _fullscreen(c: Canvas) -> None:
    c.line((2.5, 6), (2.5, 2.5), (6, 2.5))
    c.line((10, 2.5), (13.5, 2.5), (13.5, 6))
    c.line((13.5, 10), (13.5, 13.5), (10, 13.5))
    c.line((6, 13.5), (2.5, 13.5), (2.5, 10))


@shape("external")
def _external(c: Canvas) -> None:
    c.line((12.5, 9), (12.5, 13.5), (2.5, 13.5), (2.5, 3.5), (7, 3.5))
    c.line((9.5, 2.5), (13.5, 2.5), (13.5, 6.5))
    c.line((13.5, 2.5), (7.5, 8.5))


@shape("power", "quit")
def _power(c: Canvas) -> None:
    c.arc(8, 8.75, 5.25, 125, 290)                          # open at the top
    c.line((8, 2.25), (8, 7.75))


# ===========================================================================
# Files and folders
# ===========================================================================
_FOLDER = ((1.75, 3.5), (6.25, 3.5), (7.75, 5.25), (14.25, 5.25), (14.25, 12.75), (1.75, 12.75))


@shape("folder")
def _folder(c: Canvas) -> None:
    c.poly(*_FOLDER)


@shape("folder-new")
def _folder_new(c: Canvas) -> None:
    c.poly(*_FOLDER)
    c.line((8, 7.25), (8, 10.75))
    c.line((6.25, 9), (9.75, 9))


_PAGE = ((3.5, 1.75), (9.25, 1.75), (12.5, 5), (12.5, 14.25), (3.5, 14.25))


@shape("file")
def _file(c: Canvas) -> None:
    c.poly(*_PAGE)
    c.line((9.25, 1.75), (9.25, 5), (12.5, 5))


@shape("file-new")
def _file_new(c: Canvas) -> None:
    _file(c)
    c.line((8, 7.75), (8, 11.75))
    c.line((6, 9.75), (10, 9.75))


@shape("cut")
def _cut(c: Canvas) -> None:
    c.circle(4.5, 11.5, 2.25)
    c.circle(11.5, 11.5, 2.25)
    c.line((6.1, 9.9), (12, 2.25))
    c.line((9.9, 9.9), (4, 2.25))


@shape("copy", "duplicate")
def _copy(c: Canvas) -> None:
    c.rect(2.25, 5.25, 8.5, 9)
    c.line((5.25, 5.25), (5.25, 3.25), (6.25, 2.25), (12.75, 2.25), (13.75, 3.25), (13.75, 10.25),
           (12.75, 11.25), (10.75, 11.25))


@shape("paste")
def _paste(c: Canvas) -> None:
    c.line((5.75, 3.5), (4, 3.5), (3, 4.5), (3, 13.5), (4, 14.5), (12, 14.5), (13, 13.5), (13, 4.5),
           (12, 3.5), (10.25, 3.5))
    c.rect(5.75, 1.75, 4.5, 3.25, 1)


@shape("rename")
def _rename(c: Canvas) -> None:
    c.line((2.5, 11), (8.5, 11))
    c.line((2.5, 7.5), (7, 7.5))
    c.line((11.5, 3), (11.5, 13))
    c.line((10, 3), (13, 3))
    c.line((10, 13), (13, 13))


def _bin(c: Canvas) -> None:
    c.line((2.5, 4), (13.5, 4))
    c.line((6, 4), (6, 2.25), (10, 2.25), (10, 4))
    c.line((3.75, 4), (4.6, 13.75), (11.4, 13.75), (12.25, 4))


@shape("trash")
def _trash(c: Canvas) -> None:
    _bin(c)
    c.line((6.6, 6.75), (6.75, 11))
    c.line((9.4, 6.75), (9.25, 11))


@shape("delete")
def _delete(c: Canvas) -> None:
    _bin(c)
    c.line((6.25, 7), (9.75, 10.75))
    c.line((9.75, 7), (6.25, 10.75))


@shape("info", "properties", "about")
def _info(c: Canvas) -> None:
    c.circle(8, 8, 6)
    c.dot(8, 5.1, 0.95)
    c.line((8, 7.5), (8, 11.25))


@shape("help")
def _help(c: Canvas) -> None:
    c.circle(8, 8, 6)
    c.curve((6.2, 6.4), ((6.2, 4.2), (9.8, 4.2), (9.8, 6.4)), ((9.8, 7.8), (8, 8), (8, 9.6)))
    c.dot(8, 11.8, 0.95)


@shape("select-all")
def _select_all(c: Canvas) -> None:
    c.rect(2.25, 2.25, 11.5, 11.5, 2)
    c.line((5, 8.25), (7.1, 10.25), (11, 5.75))


@shape("invert-selection")
def _invert_selection(c: Canvas) -> None:
    c.rect(2.25, 2.25, 11.5, 11.5, 2)
    path = QPainterPath(c.pt(8, 2.25))
    path.lineTo(c.pt(12.25, 2.25))
    path.quadTo(c.pt(13.75, 2.25), c.pt(13.75, 3.75))
    path.lineTo(c.pt(13.75, 12.25))
    path.quadTo(c.pt(13.75, 13.75), c.pt(12.25, 13.75))
    path.lineTo(c.pt(8, 13.75))
    path.closeSubpath()
    c.fill_path(path)


@shape("link", "copy-path")
def _link(c: Canvas) -> None:
    """Two chain links at 45 degrees."""
    c.p.save()
    centre = c.pt(8, 8)
    c.p.translate(centre)
    c.p.rotate(-45)
    s = c.s
    for dx in (-2.6, 2.6):
        c.p.drawRoundedRect(QRectF((dx - 3.4) * s, -2.1 * s, 6.8 * s, 4.2 * s), 2.1 * s, 2.1 * s)
    c.p.restore()


# ===========================================================================
# Views and sorting
# ===========================================================================
@shape("view-icons")
def _view_icons(c: Canvas) -> None:
    for x in (2.25, 9.25):
        for y in (2.25, 9.25):
            c.rect(x, y, 4.5, 4.5, 1)


@shape("view-list")
def _view_list(c: Canvas) -> None:
    for y in (4.25, 8, 11.75):
        c.line((2.25, y), (6.75, y))
        c.line((9.25, y), (13.75, y))


@shape("view-details")
def _view_details(c: Canvas) -> None:
    for y in (4.25, 8, 11.75):
        c.dot(3, y, 1.05)
        c.line((5.75, y), (13.75, y))


@shape("view-tree")
def _view_tree(c: Canvas) -> None:
    c.dot(3, 3.75, 1.05)
    c.line((5.75, 3.75), (13.75, 3.75))
    c.line((3, 6.25), (3, 11.75), (5, 11.75))
    c.line((3, 8.25), (5, 8.25))
    c.line((7.5, 8.25), (13.75, 8.25))
    c.line((7.5, 11.75), (13.75, 11.75))


@shape("sort")
def _sort(c: Canvas) -> None:
    c.line((5, 2.5), (5, 13.25))
    c.line((2.5, 10.75), (5, 13.25), (7.5, 10.75))
    c.line((11, 13.5), (11, 2.75))
    c.line((8.5, 5.25), (11, 2.75), (13.5, 5.25))


@shape("filter")
def _filter(c: Canvas) -> None:
    c.poly((2, 3), (14, 3), (9.5, 8.5), (9.5, 13.25), (6.5, 11.75), (6.5, 8.5))


@shape("eye", "hidden")
def _eye(c: Canvas) -> None:
    c.curve((1.5, 8), ((3.5, 3.75), (12.5, 3.75), (14.5, 8)), ((12.5, 12.25), (3.5, 12.25), (1.5, 8)))
    c.circle(8, 8, 2.25)


@shape("eye-off")
def _eye_off(c: Canvas) -> None:
    _eye(c)
    c.line((2.75, 13.25), (13.25, 2.75))


def _magnifier(c: Canvas) -> None:
    c.circle(6.75, 6.75, 4.5)
    c.line((10.1, 10.1), (13.75, 13.75))


@shape("search")
def _search(c: Canvas) -> None:
    _magnifier(c)


@shape("zoom-in")
def _zoom_in(c: Canvas) -> None:
    _magnifier(c)
    c.line((6.75, 4.75), (6.75, 8.75))
    c.line((4.75, 6.75), (8.75, 6.75))


@shape("zoom-out")
def _zoom_out(c: Canvas) -> None:
    _magnifier(c)
    c.line((4.75, 6.75), (8.75, 6.75))


@shape("zoom-reset")
def _zoom_reset(c: Canvas) -> None:
    _magnifier(c)
    c.dot(6.75, 6.75, 1.25)


# ===========================================================================
# Tools
# ===========================================================================
@shape("transfer")
def _transfer(c: Canvas) -> None:
    c.line((2.5, 5.5), (13, 5.5))
    c.line((10, 2.8), (13, 5.5), (10, 8.2))
    c.line((13.5, 10.5), (3, 10.5))
    c.line((6, 7.8), (3, 10.5), (6, 13.2))


@shape("history")
def _history(c: Canvas) -> None:
    c.arc(8, 8, 5.5, 150, -300)
    start = (8 - 5.5 * 0.866, 8 - 5.5 * 0.5)                # where the arc begins (150 degrees)
    c.line((start[0] + 0.2, start[1] + 3.0), start, (start[0] + 2.9, start[1] + 0.6))
    c.line((8, 5), (8, 8), (10.3, 9.6))


@shape("undo", "reset")
def _undo(c: Canvas) -> None:
    c.line((5.5, 2.75), (2.5, 5.75), (5.5, 8.75))
    path = QPainterPath(c.pt(2.5, 5.75))
    path.lineTo(c.pt(9.75, 5.75))
    path.cubicTo(c.pt(12.25, 5.75), c.pt(13.75, 7.5), c.pt(13.75, 9.5))
    path.cubicTo(c.pt(13.75, 11.5), c.pt(12.25, 13.25), c.pt(9.75, 13.25))
    path.lineTo(c.pt(6, 13.25))
    c.p.drawPath(path)


@shape("server", "network", "connect")
def _server(c: Canvas) -> None:
    c.rect(2.25, 2.25, 11.5, 4.75)
    c.rect(2.25, 9, 11.5, 4.75)
    c.dot(5, 4.625, 0.95)
    c.dot(5, 11.375, 0.95)


@shape("key", "logins")
def _key(c: Canvas) -> None:
    c.circle(5, 8, 2.75)
    c.line((7.75, 8), (14, 8))
    c.line((11.25, 8), (11.25, 10.5))
    c.line((13.5, 8), (13.5, 10))


@shape("eraser", "clear")
def _eraser(c: Canvas) -> None:
    c.poly((5.25, 13.25), (2.25, 10.25), (8.75, 3.75), (13.25, 8.25), (8.25, 13.25))
    c.line((5.5, 7), (10, 11.5))
    c.line((8.25, 13.25), (13.75, 13.25))


@shape("keyboard")
def _keyboard(c: Canvas) -> None:
    c.rect(1.5, 3.75, 13, 8.5)
    for x in (4.25, 6.75, 9.25, 11.75):
        c.dot(x, 6.6, 0.8)
    c.line((5, 9.6), (11, 9.6))


def _gear_path(c: Canvas, teeth: int = 8, outer: float = 6.25, inner: float = 4.75) -> QPainterPath:
    path = QPainterPath()
    step = 2 * math.pi / teeth
    half_top, half_root = step * 0.17, step * 0.28
    points = []
    for i in range(teeth):
        a = i * step - math.pi / 2
        points += [(a - half_root, inner), (a - half_top, outer), (a + half_top, outer), (a + half_root, inner)]
    for n, (angle, radius) in enumerate(points):
        pt = c.pt(8 + radius * math.cos(angle), 8 + radius * math.sin(angle))
        if n == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


@shape("settings", "gear")
def _gear(c: Canvas) -> None:
    c.p.drawPath(_gear_path(c))
    c.circle(8, 8, 2)


@shape("sliders", "advanced")
def _sliders(c: Canvas) -> None:
    for y, knob in ((4, 10.25), (8, 5.25), (12, 8.75)):
        c.line((2.25, y), (knob - 1.75, y))
        c.circle(knob, y, 1.75)
        c.line((knob + 1.75, y), (13.75, y))


@shape("appearance", "contrast")
def _appearance(c: Canvas) -> None:
    c.circle(8, 8, 6)
    path = QPainterPath(c.pt(8, 2))
    path.arcTo(c.box(2, 2, 12, 12), 90, 180)
    path.closeSubpath()
    c.fill_path(path)


@shape("pointer", "behaviour")
def _pointer(c: Canvas) -> None:
    c.poly((4, 2), (4, 13), (6.75, 10.4), (8.75, 14.1), (10.6, 13.2), (8.6, 9.5), (12.5, 9.5))


@shape("warning")
def _warning(c: Canvas) -> None:
    c.poly((8, 2.25), (14.5, 13.5), (1.5, 13.5))
    c.line((8, 6.25), (8, 9.5))
    c.dot(8, 11.6, 0.9)


@shape("drive")
def _drive(c: Canvas) -> None:
    c.rect(1.75, 4.25, 12.5, 7.5, 2)
    c.line((4.5, 8), (8.5, 8))
    c.dot(11.25, 8, 1)


@shape("star", "bookmark")
def _star(c: Canvas) -> None:
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = 6.25 if i % 2 == 0 else 2.75
        points.append((8 + radius * math.cos(angle), 8.5 + radius * math.sin(angle)))
    c.poly(*points)


@shape("columns")
def _columns(c: Canvas) -> None:
    c.rect(2, 3, 12, 10)
    c.line((6, 3), (6, 13))
    c.line((10, 3), (10, 13))

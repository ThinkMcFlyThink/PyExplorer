"""
editors - one small editor per kind of setting (see core/settings_schema.py).

    editor = make_editor(setting, ctx, on_change)
    editor.widget          the widget to put in the row
    editor.get()           its value now
    editor.set(value)      show a value (never calls on_change)

``on_change(value)`` is called when the user changes it: straight away for
switches and drop-downs, a moment after typing stops for numbers and text
(so a font size of 13 doesn't apply 1 and then 13).

Switch is the on/off control used for every yes/no setting.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSpinBox, QWidget)

from ...core import formatting
from ...core.settings_schema import Setting

DEBOUNCE_MS = 350


class Switch(QAbstractButton):
    """An on/off switch: a rounded track with a knob that slides. Space toggles it."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._slide)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _slide(self, on: bool) -> None:
        self._anim.stop()
        if not self.isVisible():
            self._pos = 1.0 if on else 0.0
            self.update()
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:          # noqa: N802 - Qt's name
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, event) -> None:
        pal = self.ctx.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1.5, 3.5, 35, 18) if self.width() >= 38 else QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        track.moveCenter(QRectF(self.rect()).center())
        on = QColor(pal.get("accent", "#3d8ee8"))
        off_fill = QColor(pal.get("entry", "#ffffff"))
        off_line = QColor(pal.get("fg_dim", "#888888"))
        t = self._pos
        fill = QColor(int(off_fill.red() + (on.red() - off_fill.red()) * t),
                      int(off_fill.green() + (on.green() - off_fill.green()) * t),
                      int(off_fill.blue() + (on.blue() - off_fill.blue()) * t))
        if not self.isEnabled():
            p.setOpacity(0.45)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            ring = QColor(on)
            ring.setAlphaF(0.35)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ring)
            p.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), track.height() / 2 + 2.5, track.height() / 2 + 2.5)
        line = QColor(off_line) if t < 0.5 else QColor(on)
        p.setPen(line)
        p.setBrush(fill)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        d = track.height() - 6
        x = track.left() + 3 + (track.width() - 6 - d) * t
        knob_off, knob_on = QColor(pal.get("fg_dim", "#888888")), QColor(pal.get("accent_fg", "#ffffff"))
        knob = QColor(int(knob_off.red() + (knob_on.red() - knob_off.red()) * t),
                      int(knob_off.green() + (knob_on.green() - knob_off.green()) * t),
                      int(knob_off.blue() + (knob_on.blue() - knob_off.blue()) * t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, track.top() + 3, d, d))
        p.end()


class Editor:
    def __init__(self, widget: QWidget, get: Callable[[], Any], set_: Callable[[Any], None]) -> None:
        self.widget, self.get, self.set = widget, get, set_


def _quiet(widgets: List[QWidget], fn: Callable[[], None]) -> None:
    for w in widgets:
        w.blockSignals(True)
    try:
        fn()
    finally:
        for w in widgets:
            w.blockSignals(False)


def _debounced(parent: QWidget, fire: Callable[[], None]) -> QTimer:
    timer = QTimer(parent, singleShot=True, interval=DEBOUNCE_MS)
    timer.timeout.connect(fire)
    return timer


# ---------------------------------------------------------------------------
def _bool(s: Setting, ctx, on_change) -> Editor:
    w = Switch(ctx)
    w.setAccessibleName(s.label)
    w.toggled.connect(lambda on: on_change(bool(on)))
    return Editor(w, w.isChecked, lambda v: _quiet([w], lambda: w.setChecked(bool(v))))


def _int(s: Setting, ctx, on_change) -> Editor:
    w = QSpinBox(minimum=s.minimum, maximum=s.maximum, singleStep=s.step)
    w.setSuffix(s.suffix)
    if s.special:
        w.setSpecialValueText(s.special)
    w.setGroupSeparatorShown(s.maximum >= 10_000)
    w.setMinimumWidth(150)
    w.setMaximumWidth(EDITOR_WIDTH)
    w.setAccelerated(True)
    timer = _debounced(w, lambda: on_change(w.value()))
    w.valueChanged.connect(lambda _v: timer.start())
    w.editingFinished.connect(lambda: timer.isActive() and (timer.stop(), on_change(w.value())))
    return Editor(w, w.value, lambda v: _quiet([w], lambda: w.setValue(int(v or 0))))


EDITOR_WIDTH = 260          # editors line up in one column; a longer choice makes its drop-down wider
EDITOR_MAX = 440


def _combo(min_width: int = EDITOR_WIDTH) -> QComboBox:
    w = QComboBox()
    w.setMinimumWidth(min_width)
    w.setMaximumWidth(EDITOR_MAX)
    w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)    # never cut a choice short
    return w


def choice_options(s: Setting, ctx) -> list:
    """The drop-down's (value, label) pairs - from the schema, or from the interface (``source``)."""
    if s.source == "view_modes":
        from ..views.registry import VIEW_TYPES
        return [(mode, label) for mode, (label, _cls, _icon) in VIEW_TYPES.items()]
    if s.source == "date_formats":
        out = []
        for pattern in formatting.DATE_PRESETS:
            example = formatting.example_time(pattern)
            out.append((pattern, f"{example}  (relative)" if pattern == formatting.RELATIVE else example))
        return out
    return list(s.options)


def _choice(s: Setting, ctx, on_change) -> Editor:
    w = _combo()
    for value, label in choice_options(s, ctx):
        w.addItem(label, value)
    w.setEditable(s.editable)
    w.activated.connect(lambda i: on_change(w.itemData(i)))

    def set_(value) -> None:
        def show() -> None:
            index = w.findData(value)
            if index < 0 and value not in (None, ""):
                w.addItem(str(value), value)           # a value of your own (or from an older version)
                index = w.count() - 1
            w.setCurrentIndex(max(0, index))
        _quiet([w], show)

    if s.editable:
        def typed() -> None:
            text = w.currentText().strip()
            if text == w.itemText(w.currentIndex()):
                on_change(w.currentData())
            elif "%" in text:                          # a pattern of your own
                on_change(text)
            else:
                set_(w.currentData())                 # not a pattern: put the choice back
        w.lineEdit().editingFinished.connect(typed)
    return Editor(w, w.currentData, set_)


def _theme(s: Setting, ctx, on_change) -> Editor:
    from ..theme import display_name, grouped
    w = _combo()
    w.setMaxVisibleItems(24)
    w.addItem("System", "system")
    for group, keys in grouped():
        w.insertSeparator(w.count())
        w.addItem(f"{group} themes")                  # a heading, not selectable
        w.model().item(w.count() - 1).setEnabled(False)
        for k in keys:
            w.addItem(display_name(k), k)
    w.activated.connect(lambda i: w.itemData(i) and on_change(w.itemData(i)))
    return Editor(w, w.currentData, lambda v: _quiet([w], lambda: w.setCurrentIndex(max(0, w.findData(v)))))


def _font(s: Setting, ctx, on_change) -> Editor:
    from ..font_picker import FontCombo
    w = FontCombo("", monospace=(s.kind == "monofont"))
    w.setMinimumWidth(EDITOR_WIDTH)
    w.setMaximumWidth(EDITOR_WIDTH)                   # (hundreds of fonts: don't size to the longest name)
    w.activated.connect(lambda _i: on_change(w.family()))

    def set_(value) -> None:
        def show() -> None:
            index = w.findData(value or "")
            if index < 0 and value:
                w.addItem(f"{value} (not installed)", value)
                index = w.count() - 1
            w.setCurrentIndex(max(0, index))
        _quiet([w], show)
    return Editor(w, w.family, set_)


def _text(s: Setting, ctx, on_change) -> Editor:
    w = QLineEdit(clearButtonEnabled=True)
    w.setFixedWidth(EDITOR_WIDTH)
    w.setPlaceholderText("Automatic")
    w.editingFinished.connect(lambda: on_change(w.text().strip()))
    return Editor(w, lambda: w.text().strip(), lambda v: _quiet([w], lambda: w.setText("" if v is None else str(v))))


def _paths(s: Setting, ctx, on_change) -> Editor:
    w = QPlainTextEdit()
    w.setPlaceholderText("/path/to/folder\n…")
    w.setTabChangesFocus(True)
    w.setFixedWidth(EDITOR_WIDTH)
    w.setFixedHeight(w.fontMetrics().lineSpacing() * 4 + 14)

    def value() -> list:
        return [line.strip() for line in w.toPlainText().splitlines() if line.strip()]
    timer = _debounced(w, lambda: on_change(value()))
    timer.setInterval(800)
    w.textChanged.connect(timer.start)
    return Editor(w, value, lambda v: _quiet([w], lambda: w.setPlainText("\n".join(v or []))))


def _columns(s: Setting, ctx, on_change) -> Editor:
    from ..models.file_model import COLUMN_TITLES
    from ..views.details import OPTIONAL_COLUMNS
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    boxes = {}
    for col in OPTIONAL_COLUMNS:
        cb = QCheckBox(COLUMN_TITLES[col], box)
        cb.toggled.connect(lambda _on: on_change(value()))
        boxes[col] = cb
        row.addWidget(cb)

    def value() -> list:
        return ["name"] + [c for c in OPTIONAL_COLUMNS if boxes[c].isChecked()]

    def set_(v) -> None:
        _quiet(list(boxes.values()), lambda: [cb.setChecked(c in (v or [])) for c, cb in boxes.items()])
    return Editor(box, value, set_)


def _action(s: Setting, ctx, on_change) -> Editor:
    w = QPushButton(s.button or s.label, objectName="DangerButton" if s.danger else "")
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    w.setMinimumWidth(140)
    w.clicked.connect(lambda: on_change(None))
    return Editor(w, lambda: None, lambda v: None)


_FACTORIES = {"bool": _bool, "int": _int, "choice": _choice, "theme": _theme, "font": _font, "monofont": _font,
              "text": _text, "paths": _paths, "columns": _columns, "action": _action}


def make_editor(s: Setting, ctx, on_change: Callable[[Any], None]) -> Optional[Editor]:
    factory = _FACTORIES.get(s.kind)
    return factory(s, ctx, on_change) if factory else None

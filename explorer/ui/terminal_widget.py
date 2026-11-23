"""
TerminalWidget - draws a terminal screen and turns key presses into the
bytes a shell expects.

The hard part - understanding the escape codes programs send (colours,
cursor movement, clearing, scrolling regions) - is done by the ``pyte``
terminal emulator library. We just:
  * feed the shell's output into pyte (``feed``),
  * paint pyte's character grid (plus scroll-back history) with QPainter,
  * translate keys (arrows, Ctrl+C, Tab...) into terminal byte sequences,
  * support mouse selection, copy (Ctrl+Shift+C) and paste (Ctrl+Shift+V).

It knows nothing about shells: ``on_input(text)`` and ``on_resize(cols, rows)``
are set by the owner (terminal_pane.py).
"""
from __future__ import annotations

from typing import Callable, Optional, Tuple

import pyte
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter
from PySide6.QtWidgets import QAbstractScrollArea

DECCKM = 1 << 5          # "application cursor keys" mode (pyte stores private modes shifted by 5)

ANSI_DARK = {
    "black": "#2e3436", "red": "#ef5350", "green": "#8bc34a", "brown": "#e5c07b", "yellow": "#e5c07b",
    "blue": "#61afef", "magenta": "#c678dd", "cyan": "#56b6c2", "white": "#d3d7cf",
    "brightblack": "#6b7280", "brightred": "#ff6e6e", "brightgreen": "#a5e075", "brightbrown": "#ffd479",
    "brightyellow": "#ffd479", "brightblue": "#82c4ff", "brightmagenta": "#e09cf0", "brightcyan": "#7fdbe6",
    "brightwhite": "#ffffff",
}
ANSI_LIGHT = dict(ANSI_DARK, black="#1f2329", white="#9aa0a6", brown="#b8860b", yellow="#b8860b",
                  green="#3c8d2f", cyan="#138a96", blue="#1f6fd1", red="#c62828", magenta="#8e44ad")

KEYS = {
    Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r", Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Backtab: "\x1b[Z", Qt.Key.Key_Escape: "\x1b", Qt.Key.Key_Delete: "\x1b[3~",
    Qt.Key.Key_Insert: "\x1b[2~", Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
    Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
}
ARROWS = {Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C", Qt.Key.Key_Left: "D"}
F_KEYS = {getattr(Qt.Key, f"Key_F{i}") for i in range(1, 25)}

Cell = Tuple[int, int]          # (absolute line, column)


class TerminalWidget(QAbstractScrollArea):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.on_input: Callable[[str], None] = lambda text: None
        self.on_resize: Callable[[int, int], None] = lambda cols, rows: None
        self.line_mode = False                 # simple-shell back-end: we edit and echo the line
        self._line = ""
        self.cols, self.rows = 100, 24
        history = int(ctx.settings.get("terminal.scrollback", 5000))
        self.screen = pyte.HistoryScreen(self.cols, self.rows, history=history, ratio=0.5)
        self.stream = pyte.Stream(self.screen)
        self._sel_start: Optional[Cell] = None
        self._sel_end: Optional[Cell] = None
        self._follow = True                    # stick to the bottom while output arrives
        self._repaint = QTimer(self, singleShot=True, interval=16, timeout=self._apply_output)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.update_style()

    # ==================================================================
    # Look
    # ==================================================================
    def update_style(self) -> None:
        font = self.ctx.theme.mono_font()
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        fm = QFontMetrics(font)
        self.cell_w = max(1, fm.horizontalAdvance("M"))
        self.cell_h = max(1, fm.height())
        self.ascent = fm.ascent()
        pal = self.ctx.theme.palette
        self.bg = QColor(pal.get("terminal_bg", "#14161a"))
        self.fg = QColor(pal.get("terminal_fg", "#d3d9e3"))
        self.sel_bg = QColor(pal.get("select", "#2b4a7a"))
        if "ansi_red" in pal:                  # the theme's own 16 terminal colours
            self.ansi = {name: pal[f"ansi_{name}"] for name in ANSI_DARK if f"ansi_{name}" in pal}
            self.ansi["brown"], self.ansi["brightbrown"] = self.ansi["yellow"], self.ansi["brightyellow"]
        else:
            self.ansi = ANSI_LIGHT if self.bg.lightness() > 128 else ANSI_DARK
        self._color_cache = {}
        self._resize_screen()
        self.viewport().update()

    def _color(self, name: str, default: QColor) -> QColor:
        if name == "default":
            return default
        color = self._color_cache.get(name)
        if color is None:
            if name in self.ansi:
                color = QColor(self.ansi[name])
            elif len(name) == 6:
                color = QColor("#" + name)
            else:
                color = default
            self._color_cache[name] = color
        return color

    # ==================================================================
    # Output
    # ==================================================================
    def feed(self, text: str) -> None:
        """Shell output (UI thread). Painting is batched to ~60 fps."""
        self.stream.feed(text)
        if not self._repaint.isActive():
            self._repaint.start()

    def clear(self) -> None:
        self.screen.reset()
        self.screen.history.top.clear()
        self.screen.history.bottom.clear()
        self._sel_start = self._sel_end = None
        self._update_scrollbar()
        self.viewport().update()

    def _apply_output(self) -> None:
        self._update_scrollbar()
        self.viewport().update()

    def _history_len(self) -> int:
        return len(self.screen.history.top)

    def _update_scrollbar(self) -> None:
        bar = self.verticalScrollBar()
        bar.blockSignals(True)
        bar.setRange(0, self._history_len())
        bar.setPageStep(self.rows)
        if self._follow:
            bar.setValue(bar.maximum())
        bar.blockSignals(False)

    def _on_scrolled(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum()
        self.viewport().update()

    def _line_at(self, absolute: int):
        """Line ``absolute`` of history + screen (0 = oldest history line)."""
        hist = self.screen.history.top
        if absolute < len(hist):
            return hist[absolute]
        y = absolute - len(hist)
        return self.screen.buffer[y] if 0 <= y < self.rows else None

    # ==================================================================
    # Painting
    # ==================================================================
    def paintEvent(self, event) -> None:
        p = QPainter(self.viewport())
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.fillRect(self.viewport().rect(), self.bg)
        p.setFont(self.font())
        first = self.verticalScrollBar().value()
        hist = self._history_len()
        sel = self._selection_range()
        bold_font = QFont(self.font())
        bold_font.setBold(True)
        for row in range(self.rows + 1):
            absolute = first + row
            line = self._line_at(absolute)
            if line is None:
                continue
            y = row * self.cell_h
            self._paint_line(p, line, absolute, y, sel, bold_font)
        # cursor
        cur = self.screen.cursor
        cursor_abs = hist + cur.y
        if pyte.modes.DECTCEM in self.screen.mode and first <= cursor_abs <= first + self.rows:
            rect = QRect(cur.x * self.cell_w, (cursor_abs - first) * self.cell_h, self.cell_w, self.cell_h)
            if self.hasFocus():
                p.fillRect(rect, self.fg)
                ch = self.screen.buffer[cur.y][cur.x].data
                if ch.strip():
                    p.setPen(self.bg)
                    p.drawText(rect.x(), rect.y() + self.ascent, ch)
            else:
                p.setPen(self.fg)
                p.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_line(self, p: QPainter, line, absolute: int, y: int, sel, bold_font: QFont) -> None:
        cols = self.cols
        run_text, run_start, run_key = [], 0, None
        sel_from, sel_to = self._selection_cols(absolute, sel)

        def flush(end_col: int) -> None:
            if not run_text:
                return
            fg_name, bg_name, bold, reverse, underline, selected = run_key
            fg = self._color(fg_name, self.fg)
            bg = self._color(bg_name, self.bg)
            if bold and fg_name in self.ansi and not fg_name.startswith("bright"):
                fg = self._color("bright" + fg_name, fg)
            if reverse:
                fg, bg = bg, fg
            if selected:
                bg = self.sel_bg
            x = run_start * self.cell_w
            width = (end_col - run_start) * self.cell_w
            if bg != self.bg:
                p.fillRect(x, y, width, self.cell_h, bg)
            text = "".join(run_text)
            if text.strip():
                p.setFont(bold_font if bold else self.font())
                p.setPen(fg)
                p.drawText(x, y + self.ascent, text)
                if underline:
                    p.drawLine(x, y + self.cell_h - 2, x + width, y + self.cell_h - 2)

        for col in range(cols):
            ch = line[col]
            selected = sel_from <= col < sel_to
            key = (ch.fg, ch.bg, ch.bold, ch.reverse, ch.underscore, selected)
            if key != run_key:
                flush(col)
                run_text, run_start, run_key = [], col, key
            run_text.append(ch.data or " ")
        flush(cols)

    # ==================================================================
    # Size
    # ==================================================================
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_screen()

    def _resize_screen(self) -> None:
        cols = max(20, self.viewport().width() // self.cell_w)
        rows = max(3, self.viewport().height() // self.cell_h)
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.screen.resize(rows, cols)
            self.on_resize(cols, rows)
            self._update_scrollbar()

    # ==================================================================
    # Keyboard
    # ==================================================================
    def event(self, event) -> bool:
        # Claim keys before the window's shortcuts see them, EXCEPT F-keys
        # (F3 split, F4 close terminal...) and Ctrl+Shift app shortcuts.
        if event.type() == QEvent.Type.ShortcutOverride:
            mods = event.modifiers()
            ctrl_shift = (mods & Qt.KeyboardModifier.ControlModifier) and (mods & Qt.KeyboardModifier.ShiftModifier)
            if event.key() in F_KEYS or (ctrl_shift and event.key() not in (Qt.Key.Key_C, Qt.Key.Key_V)):
                return super().event(event)
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)        # don't let Tab move the focus away
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        key, mods, text = event.key(), event.modifiers(), event.text()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy_selection()
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or event.matches(QKeySequence.StandardKey.Paste) and not ctrl:
            self.paste()
            return
        if shift and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):     # scroll back through history
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (-1 if key == Qt.Key.Key_PageUp else 1) * self.rows // 2)
            return
        self._scroll_to_bottom()
        if self.line_mode:
            self._line_key(key, ctrl, text)
            return
        seq = None
        if key in ARROWS:
            seq = ("\x1bO" if DECCKM in self.screen.mode else "\x1b[") + ARROWS[key]
            if ctrl:
                seq = "\x1b[1;5" + ARROWS[key]
        elif key in KEYS:
            seq = KEYS[key]
        elif ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            seq = chr(key - Qt.Key.Key_A + 1)
        elif ctrl and key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_Backslash, Qt.Key.Key_BracketRight, Qt.Key.Key_Space):
            seq = {Qt.Key.Key_BracketLeft: "\x1b", Qt.Key.Key_Backslash: "\x1c",
                   Qt.Key.Key_BracketRight: "\x1d", Qt.Key.Key_Space: "\x00"}[key]
        elif text:
            seq = text
        if seq:
            if alt and not seq.startswith("\x1b"):
                seq = "\x1b" + seq
            self.on_input(seq)

    def _line_key(self, key, ctrl: bool, text: str) -> None:
        """Simple-shell mode: edit the line locally, send it on Enter."""
        if ctrl and key == Qt.Key.Key_C:
            self.on_input("\x03")
            self._line = ""
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            line, self._line = self._line, ""
            self.feed("\r\n")
            self.on_input(line + "\n")
        elif key == Qt.Key.Key_Backspace:
            if self._line:
                self._line = self._line[:-1]
                self.feed("\b \b")
        elif text and text.isprintable():
            self._line += text
            self.feed(text)

    def inputMethodEvent(self, event) -> None:
        if event.commitString():
            self.on_input(event.commitString())

    def _scroll_to_bottom(self) -> None:
        self._follow = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() // 40
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - steps)

    # ==================================================================
    # Selection, copy, paste
    # ==================================================================
    def _cell_at(self, pos: QPoint) -> Cell:
        row = max(0, pos.y() // self.cell_h)
        col = max(0, min(self.cols, round(pos.x() / self.cell_w)))
        return self.verticalScrollBar().value() + row, col

    def _selection_range(self):
        if self._sel_start is None or self._sel_end is None or self._sel_start == self._sel_end:
            return None
        return tuple(sorted((self._sel_start, self._sel_end)))

    def _selection_cols(self, absolute: int, sel) -> Tuple[int, int]:
        if not sel:
            return 0, 0
        (l1, c1), (l2, c2) = sel
        if absolute < l1 or absolute > l2:
            return 0, 0
        start = c1 if absolute == l1 else 0
        end = c2 if absolute == l2 else self.cols
        return start, end

    def selected_text(self) -> str:
        sel = self._selection_range()
        if not sel:
            return ""
        (l1, _), (l2, _) = sel
        out = []
        for absolute in range(l1, l2 + 1):
            line = self._line_at(absolute)
            if line is None:
                continue
            start, end = self._selection_cols(absolute, sel)
            out.append("".join(line[c].data for c in range(start, end)).rstrip())
        return "\n".join(out)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_start = self._sel_end = self._cell_at(event.position().toPoint())
            self.viewport().update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste(QGuiApplication.clipboard().text(QGuiApplication.clipboard().Mode.Selection))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._sel_start is not None:
            self._sel_end = self._cell_at(event.position().toPoint())
            self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:
        text = self.selected_text()
        if text:   # Linux "primary selection": select = copy for middle-click paste
            clipboard = QGuiApplication.clipboard()
            if clipboard.supportsSelection():
                clipboard.setText(text, clipboard.Mode.Selection)

    def mouseDoubleClickEvent(self, event) -> None:
        """Select the word under the mouse."""
        line_no, col = self._cell_at(event.position().toPoint())
        line = self._line_at(line_no)
        if line is None:
            return
        chars = "".join(line[c].data or " " for c in range(self.cols))
        col = min(col, self.cols - 1)
        start = end = col
        while start > 0 and not chars[start - 1].isspace():
            start -= 1
        while end < self.cols and not chars[end].isspace():
            end += 1
        self._sel_start, self._sel_end = (line_no, start), (line_no, end)
        self.viewport().update()

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self, text: Optional[str] = None) -> None:
        text = QGuiApplication.clipboard().text() if text is None else text
        if not text:
            return
        if self.line_mode:
            first = text.splitlines()[0] if text.splitlines() else ""
            self._line += first
            self.feed(first)
        else:
            self.on_input(text.replace("\r\n", "\r").replace("\n", "\r"))

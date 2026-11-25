"""
TerminalPane - the F4 terminal at the bottom of the window (like Dolphin).

It follows you: whenever the active tab moves to another local folder, the
shell runs `cd` there (turn off with the "terminal.sync_cd" setting).

Wiring only: the shell comes from services/terminal.py, the drawing from
terminal_widget.py. Shortcuts inside: Ctrl+Shift+C copy, Ctrl+Shift+V paste,
Shift+PgUp/PgDn scroll back. F-keys still reach the app (F4 hides it).
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from ..core.events import Topics
from ..services.platform_ops import open_external_terminal
from ..services.terminal import create_backend
from .terminal_widget import TerminalWidget


class TerminalPane(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.backend = None
        self.cwd: Optional[str] = None
        self.shown = False
        self._pending_cd: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget(self, objectName="TerminalBar")       # themes draw it like a tab strip
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 2, 4, 2)
        self.title = QLabel("Terminal", bar, objectName="Dim")
        row.addWidget(self.title, 1)
        SP = QStyle.StandardPixmap
        for text, icon, tip, slot in (
                ("Clear", ctx.icons.symbol("eraser", "edit-clear", SP.SP_DialogResetButton), "Clear the screen",
                 self._clear),
                ("Restart", ctx.icons.symbol("refresh", "view-refresh", SP.SP_BrowserReload), "Restart the shell",
                 self.restart),
                ("External", ctx.icons.symbol("external", "utilities-terminal", SP.SP_DesktopIcon),
                 "Open your system terminal here", self._external)):
            b = QToolButton(bar, text=text, toolTip=tip, autoRaise=True)
            b.setIcon(icon)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addWidget(bar)
        self.term = TerminalWidget(ctx, self)
        self.term.on_input = self._send
        self.term.on_resize = lambda cols, rows: self.backend.resize(cols, rows) if self.backend else None
        layout.addWidget(self.term, 1)
        self._unsubs = [ctx.bus.subscribe(Topics.THEME_CHANGED, lambda **_: self.term.update_style()),
                        ctx.bus.subscribe(Topics.ACTIVE_LOCATION_CHANGED, self._on_location)]
        self.destroyed.connect(lambda *_: [u() for u in self._unsubs])

    # -- lifecycle -----------------------------------------------------------
    def on_shown(self) -> None:
        """The pane became visible: start the shell lazily, catch up on cd."""
        self.shown = True
        tab = self.ctx.commands.active_tab()
        folder = tab.current_folder if tab else None
        if folder and self._is_local(folder):
            self._pending_cd = folder
        if self.backend is None:
            self.start(self._pending_cd or self.cwd)
        elif self._pending_cd and self._pending_cd != self.cwd:
            self._cd(self._pending_cd)
        self._pending_cd = None
        self.term.setFocus()

    def on_hidden(self) -> None:
        self.shown = False

    def start(self, cwd: Optional[str]) -> None:
        cwd = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
        post = self.ctx.dispatcher.post
        self.backend = create_backend(lambda text: post(self._output, text),
                                      lambda code: post(self._exited, code),
                                      self.ctx.settings.get("terminal.shell", ""))
        self.term.line_mode = self.backend.line_mode
        self.cwd = cwd
        try:
            self.backend.start(cwd, self.term.cols, self.term.rows)
        except OSError as exc:
            self._output(f"Could not start the shell: {exc}\r\n")
            self.backend = None
        self._set_title()

    def restart(self) -> None:
        if self.backend is not None:
            self.backend.close()
            self.backend = None
        self.term.clear()
        self.start(self.cwd)
        self.term.setFocus()

    def shutdown(self) -> None:
        if self.backend is not None:
            self.backend.close()
            self.backend = None

    def _exited(self, code: int) -> None:
        self.backend = None
        self._output(f"\r\n[shell exited with code {code} — press Restart]\r\n")

    def _clear(self) -> None:
        self.term.clear()
        if self.backend is not None and not self.backend.line_mode:
            self.backend.write("\x0c")          # Ctrl+L: ask the shell to redraw its prompt

    def _external(self) -> None:
        try:
            open_external_terminal(self.cwd or os.path.expanduser("~"), self.ctx.settings.get("terminal.external", ""))
        except OSError as exc:
            self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=str(exc), level="error")

    def _set_title(self) -> None:
        name = self.backend.name if self.backend else "Terminal"
        self.title.setText(f"{name}  —  {self.cwd}")

    # -- io -------------------------------------------------------------------
    def _send(self, text: str) -> None:
        if self.backend is None:
            return
        if self.backend.line_mode and text == "\x03":
            self.backend.interrupt()
        else:
            self.backend.write(text)

    def _output(self, text: str) -> None:
        self.term.feed(text)

    # -- following the active folder ------------------------------------------
    def _is_local(self, path: str) -> bool:
        try:
            return self.ctx.vfs.resolve(path).is_local
        except OSError:
            return False

    def _on_location(self, path: str = "", **_) -> None:
        if not self.ctx.settings.get("terminal.sync_cd", True) or not path or not self._is_local(path):
            return
        if self.shown and self.backend is not None:
            if path != self.cwd:
                self._cd(path)
        else:
            self._pending_cd = path

    def _cd(self, path: str) -> None:
        self.cwd = path
        self._set_title()
        if self.backend is not None:
            self.backend.change_directory(path)

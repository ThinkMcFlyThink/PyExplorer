"""
TransferUI - where the transfer panel lives, and keeping everything that
shows progress up to date.

* ONE TransferPanel for the whole app, shown in a pop-up window (default) or
  in a Transfers tab (Settings -> Transfers -> "Show transfers in"); the
  panel's button moves it between the two at any time.
* Shows itself when a transfer starts (TRANSFERS_CHANGED from the engine).
* While anything runs, a timer refreshes the panel, the tab's progress
  shading, the window title, and the taskbar/dock progress (ui/taskbar.py) -
  in both window and tab mode.
* Warns when a transfer did not start because it would not fit; reports how
  each transfer ended in the status bar; optionally hides the window when all
  is done (off by default).
* Asks before quitting while transfers are running.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt, QTimer

from ...core import transfer_model as tm
from ...core.events import Topics
from ...core.formatting import human_size
from ..taskbar import create_taskbar_progress
from .panel import TransferPanel
from .window import TransferWindow

TICK_MS = 250


class TransferUI:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.panel: Optional[TransferPanel] = None
        self.window: Optional[TransferWindow] = None
        self.tab = None
        self.in_tab = ctx.settings.get("transfers.show_in", "window") == "tab"
        self.timer = QTimer(ctx.main)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.taskbar = create_taskbar_progress(ctx.main, windows=lambda: [ctx.main, self.window])
        self._unsub = ctx.bus.subscribe(Topics.TRANSFERS_CHANGED, self._on_event)

    # ==================================================================
    # Where the panel lives
    # ==================================================================
    def _ensure_panel(self) -> TransferPanel:
        if self.panel is None:
            self.panel = TransferPanel(self.ctx)
            self.panel.mode_requested.connect(self.toggle_mode)
        self.panel.set_mode_label(self.in_tab)
        return self.panel

    def show(self, transfer: Optional[tm.Transfer] = None, activate: bool = True) -> None:
        panel = self._ensure_panel()
        if self.in_tab:
            self._show_tab(activate)
        else:
            self._show_window(activate)
        if transfer is not None:
            panel.select(transfer)
        panel.refresh()

    def _show_window(self, activate: bool) -> None:
        if self.window is None:
            self.window = TransferWindow(self.ctx)
        if self.panel.parent() is not self.window:
            self.window.set_panel(self.panel)
        if not self.window.isVisible():
            self.window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
            self.window.show()
        self.window.raise_()
        if activate:
            self.window.activateWindow()

    def _show_tab(self, activate: bool) -> None:
        if self.tab is None:
            area = self.ctx.main.panel_area
            area.active_panel.open_utility("transfers", activate=activate)     # -> adopt_tab
        elif activate:
            self.tab.panel.activate(self.tab.panel.tab_widget.indexOf(self.tab))

    def adopt_tab(self, tab) -> None:
        """A Transfers tab was created (by show(), the menu, or session restore): the panel moves in."""
        if self.tab is not None and self.tab is not tab:
            old = self.tab
            self.tab = None
            old.panel.close_tab(old, remember=False)
        self.tab = tab
        self.in_tab = True
        tab.set_panel(self._ensure_panel())
        if self.window is not None:
            self.window.hide()
        self._tick()

    def tab_closing(self, tab) -> None:
        if tab is not self.tab:
            return
        self.tab = None
        if self.panel is not None:
            self.panel.setParent(None)                 # keep it (and its history) for next time
        self.in_tab = self.ctx.settings.get("transfers.show_in", "window") == "tab"

    def toggle_mode(self) -> None:
        """The panel's "Open as tab / in a window" button."""
        if self.in_tab:
            tab = self.tab
            self.in_tab = False
            self._show_window(True)                    # moves the panel out of the tab first
            if tab is not None:
                self.tab = None
                tab.panel.close_tab(tab, remember=False)
        else:
            self.in_tab = True
            if self.window is not None:
                self.window.hide()
            self._show_tab(True)
        if self.panel is not None:
            self.panel.set_mode_label(self.in_tab)

    # ==================================================================
    # Events from the engine
    # ==================================================================
    def _on_event(self, event: str, transfer: tm.Transfer) -> None:
        if event == "started":
            # Appear, but don't take the keyboard away from what the user is doing (Ctrl+J focuses it).
            self.show(transfer, activate=False)
            if not self.timer.isActive():
                self.timer.start()
        elif event == "no_space":
            from .. import dialogs
            parent = self.window if self.window is not None and self.window.isVisible() else self.ctx.main
            dialogs.show_errors(parent, "Not enough space" if transfer.state == tm.NO_SPACE and
                                not transfer.done_bytes else "The drive is full", [transfer.message])
        elif event == "finished":
            self.ctx.transfer_log.dirty = True
            self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=self.describe_end(transfer),
                                 level="info" if transfer.ok else "error")
            self._tick()
            if not self.ctx.transfer_log.active() and transfer.ok and \
                    self.ctx.settings.get("transfers.close_when_done", False) and \
                    self.window is not None and not self.in_tab:
                self.window.hide()

    @staticmethod
    def describe_end(t: tm.Transfer) -> str:
        verb = "Moved" if t.kind == "move" else "Copied"
        c = t.counts()
        good = sum(c.get(k, 0) for k in tm.GOOD)
        text = f"{verb} {good:,} file{'s' if good != 1 else ''} ({human_size(t.done_bytes)})"
        if t.done_bytes:
            text += f" at {human_size(t.average_speed)}/s"
        if t.state == tm.NO_SPACE:
            return t.message.split(". ")[0] + (f" - {text}" if good else "")
        if t.state == tm.CANCELLED:
            return f"Cancelled - {text}"
        if t.failed:
            return f"{text} - {t.failed:,} failed (see Transfers)"
        return text + (" - all verified" if t.verify and good else "")

    # ==================================================================
    # Progress everywhere (timer)
    # ==================================================================
    def overall(self) -> Tuple[Optional[float], bool, bool]:
        """(progress 0..1 or None, all paused, something failed) over running transfers."""
        active = self.ctx.transfer_log.active() if self.ctx.transfer_log else []
        if not active:
            return None, False, False
        total = sum(t.total_bytes for t in active)
        progress = (sum(t.done_bytes for t in active) / total) if total else \
            sum(t.progress for t in active) / len(active)
        return (max(0.0, min(1.0, progress)), all(t.state == tm.PAUSED for t in active),
                any(t.failed for t in active))

    def _tick(self) -> None:
        progress, paused, error = self.overall()
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh()
        self.taskbar.set(progress, paused=paused, error=error)
        if self.window is not None:
            self.window.set_progress_title(progress)
        if self.tab is not None:
            self.tab.panel.tab_updated(self.tab)
            self.tab.panel.tab_bar.update()
        if progress is None and self.timer.isActive():
            self.timer.stop()

    def confirm_quit(self) -> bool:
        active = self.ctx.transfer_log.active()
        if not active:
            return True
        from .. import dialogs
        n = len(active)
        if not dialogs.confirm(self.ctx.main, "Transfers running",
                               f"{n} transfer{'s are' if n != 1 else ' is'} still running.\n\n"
                               "Quit anyway? Files not copied yet will be marked \"not copied\".",
                               "Cancel transfers && quit", danger=True):
            return False
        for t in active:
            self.ctx.transfers.cancel(t)
        return True

    def shutdown(self) -> None:
        self.timer.stop()
        self.taskbar.set(None)
        self._unsub()

"""TransfersTab - the transfer panel as a utility tab in the main window. Its
tab is shaded like a progress bar while transfers run (see TabBar.paintEvent
in ui/panel.py, which reads ``progress``)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QVBoxLayout

from ..utility.base import UtilityTab


class TransfersTab(UtilityTab):
    KIND = "transfers"
    TITLE = "Transfers"
    SHAPE = "transfer"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        super().__init__(panel, ctx, state)
        self.header.hide()                     # the panel has its own bar; the tab itself says what it is
        ctx.transfer_ui.adopt_tab(self)

    def build(self, body: QVBoxLayout) -> None:
        self._body = body

    def set_panel(self, panel) -> None:
        self._body.addWidget(panel)
        panel.show()

    @property
    def progress(self) -> Optional[float]:
        """0..1 while something is being copied, else None."""
        return self.ctx.transfer_ui.overall()[0] if self.ctx.transfer_ui else None

    @property
    def title(self) -> str:
        p = self.progress
        return self.TITLE if p is None else f"{self.TITLE} {p * 100:.0f} %"

    def focus_view(self) -> None:
        panel = self.ctx.transfer_ui.panel if self.ctx.transfer_ui else None
        (panel.list if panel is not None else self).setFocus()

    def shutdown(self) -> None:
        if self.ctx.transfer_ui:
            self.ctx.transfer_ui.tab_closing(self)
        super().shutdown()

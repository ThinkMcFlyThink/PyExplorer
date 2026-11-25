"""TransferWindow - the transfer panel in its own window (the default; see
Settings -> Transfers). Closing it only hides it: transfers keep going."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget


class TransferWindow(QWidget):
    def __init__(self, ctx) -> None:
        # A child window of the main window: it stays with it (and on Windows shares its taskbar button).
        super().__init__(ctx.main, Qt.WindowType.Window)
        self.ctx = ctx
        self.setObjectName("TransferWindow")
        self.setWindowTitle("Transfers")
        # (no setWindowIcon with our painted icons: Qt keeps window icons until after Python has
        #  shut down, and a Python icon engine destroyed then crashes the exit. The app icon is used.)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._layout = layout
        geometry = ctx.settings.get("transfers.window_geometry") or ""
        if not geometry or not self.restoreGeometry(QByteArray.fromBase64(geometry.encode())):
            self.resize(900, 640)

    def set_panel(self, panel) -> None:
        self._layout.addWidget(panel)
        panel.show()

    def set_progress_title(self, progress) -> None:
        self.setWindowTitle("Transfers" if progress is None else f"{progress * 100:.0f} % · Transfers")

    def closeEvent(self, event) -> None:
        self.ctx.settings.set("transfers.window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        super().closeEvent(event)

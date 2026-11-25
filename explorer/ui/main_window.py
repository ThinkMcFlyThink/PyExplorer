"""
MainWindow - lays out the pieces:

    ┌ menu bar / toolbar ────────────────────────────────────────┐
    │ sidebar  │  panel area (1 or 2 FilePanels, each with tabs)  │
    │ (dock)   │──────────────────────────────────────────────────│
    │          │  terminal (dock, F4)                             │
    └ status bar ─────────────────────────────────────────────────┘

The sidebar and terminal are QDockWidgets: they can be resized, moved to
another edge or floated, and Qt remembers their layout between sessions.

This class owns very little logic: showing/hiding things, and saving the
window + session on exit. The toolbar is its own piece (toolbar.py).
"""
from __future__ import annotations

import base64
import logging

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

from .. import APP_NAME
from ..core.events import Topics
from .menubar import build_menubar
from .panel_area import PanelArea
from .sidebar import Sidebar
from .statusbar import StatusBar
from .terminal_pane import TerminalPane
from .toolbar import MainToolbar

log = logging.getLogger(__name__)


def _b64(data: QByteArray) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def _unb64(text: str) -> QByteArray:
    try:
        return QByteArray(base64.b64decode(text.encode("ascii")))
    except Exception:  # noqa: BLE001
        return QByteArray()


class MainWindow(QMainWindow):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        ctx.main = self
        self.on_exit = None                  # set by app.py: saves everything
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)

        self.panel_area = PanelArea(ctx, self)
        self.setCentralWidget(self.panel_area)

        self.sidebar = Sidebar(ctx)
        self.sidebar_dock = QDockWidget("Places", self, objectName="SidebarDock")
        self.sidebar_dock.setWidget(self.sidebar)
        self.sidebar_dock.setTitleBarWidget(QWidget())          # no title bar: cleaner look
        self.sidebar_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sidebar_dock)
        self.resizeDocks([self.sidebar_dock], [230], Qt.Orientation.Horizontal)

        self.terminal = TerminalPane(ctx)
        self.terminal_dock = QDockWidget("Terminal", self, objectName="TerminalDock")
        self.terminal_dock.setWidget(self.terminal)
        self.terminal_dock.setTitleBarWidget(QWidget())
        self.terminal_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable |
                                       QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminal_dock)
        self.resizeDocks([self.terminal_dock], [240], Qt.Orientation.Vertical)
        self.terminal_dock.hide()

        self.setStatusBar(StatusBar(ctx, self))
        self._unsubs = [ctx.bus.subscribe(Topics.ACTIVE_LOCATION_CHANGED, self._on_location)]
        self.destroyed.connect(lambda *_: [u() for u in self._unsubs])

    # -- built after actions exist (they need ctx.main) --------------------------
    def build_chrome(self) -> None:
        build_menubar(self, self.ctx)
        self.toolbar = MainToolbar(self.ctx, self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.apply_chrome()
        self.ctx.actions.sync_checks()

    def apply_chrome(self) -> None:
        """Show / hide the toolbar, status bar and sidebar as the settings say."""
        s = self.ctx.settings
        self.toolbar.setVisible(bool(s.get("appearance.show_toolbar", True)))
        self.statusBar().setVisible(bool(s.get("appearance.show_statusbar", True)))
        if self.isVisible():                            # (before the first show, restore() decides)
            self.sidebar_dock.setVisible(bool(s.get("appearance.show_sidebar", True)))
        self.ctx.actions.sync_checks()

    def createPopupMenu(self):                          # noqa: N802 - right-click on the toolbar or a bar
        return self.toolbar.context_menu(self)

    def on_theme_changed(self) -> None:
        for panel in self.panel_area.panels:
            panel.refresh_tab_icons()

    def _on_location(self, path: str = "", tab=None, **_) -> None:
        """The window title names the current folder (handy in the taskbar and window switcher)."""
        title = getattr(tab, "title", "") if tab is not None else ""
        self.setWindowTitle(f"{title} — {APP_NAME}" if title else APP_NAME)

    # -- terminal / sidebar ----------------------------------------------------
    @property
    def terminal_visible(self) -> bool:
        return self.terminal_dock.isVisible()

    def set_terminal_visible(self, visible: bool) -> None:
        self.terminal_dock.setVisible(visible)
        if visible:
            self.terminal.on_shown()
        else:
            self.terminal.on_hidden()
            tab = self.ctx.commands.active_tab()
            if tab:
                tab.focus_view()
        self.ctx.settings.set("terminal.visible", visible)
        self.ctx.actions.sync_checks()

    @property
    def sidebar_visible(self) -> bool:
        return self.sidebar_dock.isVisible()

    def set_sidebar_visible(self, visible: bool) -> None:
        self.sidebar_dock.setVisible(visible)
        self.ctx.settings.set("appearance.show_sidebar", visible)
        self.ctx.actions.sync_checks()

    # -- session ---------------------------------------------------------------
    def restore(self, session) -> None:
        s = self.ctx.settings
        if s.get("window.geometry"):
            self.restoreGeometry(_unb64(s.get("window.geometry")))
        if s.get("window.state"):
            self.restoreState(_unb64(s.get("window.state")))
        self.panel_area.restore(session)
        self.sidebar_dock.setVisible(bool(s.get("appearance.show_sidebar", True)))
        self.terminal_dock.setVisible(False)
        if s.get("terminal.visible"):
            self.set_terminal_visible(True)
        self.ctx.actions.sync_checks()

    def session_state(self) -> dict:
        state = self.panel_area.get_state()
        state["closed_tabs"] = self.ctx.closed_tabs[-10:]
        return state

    def save_window_state(self) -> None:
        self.ctx.settings.set("window.geometry", _b64(self.saveGeometry()))
        self.ctx.settings.set("window.state", _b64(self.saveState()))

    confirm_close = None          # set by app.py: e.g. "transfers are running - quit anyway?"

    def closeEvent(self, event) -> None:
        if self.confirm_close is not None and not self.confirm_close():
            event.ignore()
            return
        try:
            self.save_window_state()
            if self.on_exit:
                self.on_exit()
        except Exception:  # noqa: BLE001 - never block closing the window
            log.exception("Error while saving on exit")
        super().closeEvent(event)

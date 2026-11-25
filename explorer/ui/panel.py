"""
FilePanel - a tab bar plus its tabs. The window shows one panel, or two side
by side in split ("commander") mode - see panel_area.py.

New tabs always open in the panel where the action happened, because every
tab knows its own panel (``tab.panel``) and calls ``tab.panel.open_tab``.

TabBar extras: middle-click closes, double-click on empty space opens a new
tab, drag a tab sideways to reorder, drag it down/away and drop it on the
other panel's tab bar to move it there. While dragging files, hovering over
a tab switches to it so you can drop into that folder.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, QMimeData, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QDrag, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QMenu, QTabBar, QToolButton, QVBoxLayout

from ..core.events import Topics
from ..core.platform_info import home
from .models.file_model import INTERNAL_MIME
from . import utility
from .tab import BrowserTab
from .tab_container import TabContainer

MAX_CLOSED = 25
TAB_MIME = "application/x-pyexplorer-tab"


class TabBar(QTabBar):
    def __init__(self, panel: "FilePanel") -> None:
        super().__init__()
        self.panel = panel
        self.setMovable(True)
        self.setTabsClosable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setUsesScrollButtons(True)
        self.setAcceptDrops(True)
        self._press_pos = None

    def paintEvent(self, event) -> None:
        """Normal tabs as the theme draws them; UTILITY tabs (Search, Rename...)
        get an accent tint and a marker stripe on the left, so they never look
        like a folder."""
        super().paintEvent(event)
        tabs = self.panel.tab_widget
        painter = None
        for i in range(self.count()):
            if not getattr(tabs.widget(i), "is_utility", False):
                continue
            if painter is None:
                painter = QPainter(self)
                accent = QColor(self.panel.ctx.theme.palette.get("accent", "#3d8ee8"))
            rect = self.tabRect(i)
            tint = QColor(accent)
            tint.setAlphaF(0.22 if i == self.currentIndex() else 0.13)
            painter.fillRect(rect.adjusted(0, 0, -1, 0), tint)
            progress = getattr(tabs.widget(i), "progress", None)       # e.g. the Transfers tab while copying
            if progress is not None:
                done = QColor(accent)
                done.setAlphaF(0.38)
                painter.fillRect(QRect(rect.left(), rect.top(), int((rect.width() - 1) * progress), rect.height()), done)
            painter.fillRect(QRect(rect.left(), rect.top(), 3, rect.height()), accent)
        if painter is not None:
            painter.end()

    def mousePressEvent(self, event) -> None:
        self.panel.set_active_panel()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.panel.close_tab(index)
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.tabAt(event.position().toPoint()) < 0:
            self.panel.open_tab(None, activate=True)
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Dragged far below/above the bar: turn it into a "move tab to other panel" drag.
        if self._press_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            y = event.position().y()
            if y < -30 or y > self.height() + 30:
                index = self.tabAt(self._press_pos)
                self._press_pos = None
                # End Qt's own "reorder" drag cleanly before starting ours.
                release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(event.position()),
                                      event.globalPosition(), Qt.MouseButton.LeftButton,
                                      Qt.MouseButton.NoButton, event.modifiers())
                super().mouseReleaseEvent(release)
                if index >= 0:
                    self._drag_tab(index)
                return
        super().mouseMoveEvent(event)

    def _drag_tab(self, index: int) -> None:
        mime = QMimeData()
        mime.setData(TAB_MIME, f"{id(self.panel)}:{index}".encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.tabIcon(index).pixmap(24, 24))
        drag.exec(Qt.DropAction.MoveAction)

    # -- drops: tabs from the other panel, or files hovering over a tab ---------
    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(TAB_MIME) or mime.hasFormat(INTERNAL_MIME) or mime.hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(TAB_MIME):
            event.acceptProposedAction()
            return
        index = self.tabAt(event.position().toPoint())      # files: switch to the hovered tab
        if index >= 0 and index != self.currentIndex():
            self.panel.activate(index, focus=False)
        event.ignore()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if not mime.hasFormat(TAB_MIME):
            event.ignore()
            return
        panel_id, _, index = bytes(mime.data(TAB_MIME)).decode().partition(":")
        source = next((p for p in self.panel.area.panels if str(id(p)) == panel_id), None)
        if source is not None and source is not self.panel:
            source.move_tab_to_panel(int(index), self.panel)
            event.acceptProposedAction()

    def contextMenuEvent(self, event) -> None:
        index = self.tabAt(event.pos())
        panel = self.panel
        cmds = panel.ctx.commands
        menu = QMenu(self)
        menu.addAction("New tab").triggered.connect(lambda: panel.open_tab(None, activate=True))
        if index >= 0:
            tab = panel.tabs[index]
            menu.addAction("Duplicate tab").triggered.connect(
                lambda: panel.open_tab(None, state=tab.get_state(), index=index + 1))
            menu.addAction("Move to other panel").triggered.connect(lambda: cmds.move_tab_to_other_panel(tab))
            menu.addSeparator()
            menu.addAction("Close tab").triggered.connect(lambda: panel.close_tab(tab))
            menu.addAction("Close other tabs").triggered.connect(lambda: panel.close_others(tab))
            menu.addAction("Close tabs to the right").triggered.connect(lambda: panel.close_right_of(tab))
        menu.addSeparator()
        reopen = menu.addAction("Reopen closed tab")
        reopen.setEnabled(bool(panel.ctx.closed_tabs))
        reopen.triggered.connect(cmds.reopen_closed_tab)
        menu.exec(event.globalPos())


class FilePanel(QFrame):
    def __init__(self, ctx, area) -> None:
        super().__init__()
        self.setObjectName("PanelFrame")
        self.setProperty("active", False)
        self.setProperty("marked", False)
        self.ctx = ctx
        self.area = area
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar = TabBar(self)
        self.new_tab_button = plus = QToolButton(self, autoRaise=True, toolTip="New tab (Ctrl+T)")
        plus.setObjectName("NewTabButton")
        plus.setIcon(ctx.icons.symbol("plus", "list-add"))
        plus.clicked.connect(lambda: self.open_tab(None, activate=True))
        self.tab_widget = TabContainer(self.tab_bar, plus, self)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self._on_current_changed)
        layout.addWidget(self.tab_widget)
        self._plus = plus
        unsub = ctx.bus.subscribe(Topics.THEME_CHANGED, lambda **_: self.tab_widget.fit_plus_button())
        self.destroyed.connect(lambda *_: unsub())

    # -- queries --------------------------------------------------------------
    @property
    def tabs(self) -> List[BrowserTab]:
        return [self.tab_widget.widget(i) for i in range(self.tab_widget.count())]

    @property
    def active_index(self) -> int:
        return self.tab_widget.currentIndex()

    @property
    def active_tab(self) -> Optional[BrowserTab]:
        return self.tab_widget.currentWidget()

    def is_active_panel(self) -> bool:
        return self.area.active_panel is self

    def set_highlight(self, active: bool, split: bool) -> None:
        """The active panel: its current tab gets the accent line (themes). With the
        "system" theme, the active side of a split gets a line along its top instead."""
        marked = active and split
        if self.property("active") != active or self.property("marked") != marked:
            self.setProperty("active", active)
            self.setProperty("marked", marked)
            self.style().unpolish(self)
            self.style().polish(self)
            self.tab_bar.update()

    # -- tab management --------------------------------------------------------
    def open_tab(self, path: Optional[str] = None, state: Optional[dict] = None,
                 activate: bool = True, index: Optional[int] = None) -> BrowserTab:
        if state is None:
            if path is None:      # "new tab": the current tab's folder (like Dolphin), or home (Settings -> Behaviour)
                current = self.active_tab
                same = self.ctx.settings.get("behaviour.new_tab_folder", "current") != "home"
                path = current.current_folder if same and current and current.current_folder else home()
            state = {"path": path}
        if utility.is_utility_state(state):
            tab = utility.create(state["utility"], self, self.ctx, state)
        else:
            tab = BrowserTab(self, self.ctx, state)
        self._insert(tab, index, activate)
        return tab

    def open_utility(self, kind: str, state: Optional[dict] = None, activate: bool = True):
        """Open a utility tab (Search, Rename...) right after the current tab."""
        return self.open_tab(state=dict(state or {}, utility=kind), activate=activate)

    def _insert(self, tab: BrowserTab, index: Optional[int], activate: bool) -> None:
        tab.panel = self
        if index is None:              # next to the current tab, or at the end (Settings -> Behaviour)
            at_end = self.ctx.settings.get("behaviour.new_tab_position", "next") == "end"
            index = self.tab_widget.count() if at_end or not self.tab_widget.count() else self.active_index + 1
        index = self.tab_widget.insertTab(index, tab, tab.icon, tab.title)
        self.tab_widget.setTabToolTip(index, tab.path)
        if activate or self.tab_widget.count() == 1:
            self.activate(index, focus=activate)

    def activate(self, index: int, focus: bool = True) -> None:
        if 0 <= index < self.tab_widget.count():
            self.tab_widget.setCurrentIndex(index)
            self.set_active_panel(force_event=True)
            if focus:      # keyboard focus and "active panel" must always agree
                self.active_tab.focus_view()

    def cycle_tab(self, delta: int) -> None:
        count = self.tab_widget.count()
        if count:
            self.activate((self.active_index + delta) % count)

    def _index_of(self, index_or_tab) -> int:
        return index_or_tab if isinstance(index_or_tab, int) else self.tab_widget.indexOf(index_or_tab)

    def close_tab(self, index_or_tab, remember: bool = True) -> None:
        index = self._index_of(index_or_tab)
        if not 0 <= index < self.tab_widget.count():
            return
        tab = self.tab_widget.widget(index)
        if remember:
            self.ctx.closed_tabs.append(tab.get_state())
            del self.ctx.closed_tabs[:-MAX_CLOSED]
        self.tab_widget.removeTab(index)
        tab.shutdown()
        tab.deleteLater()
        if self.tab_widget.count() == 0:
            if self.area.is_split:
                self.area.remove_panel(self)      # last tab of a split side closes that side
            else:
                self.open_tab(home())

    def close_others(self, keep: BrowserTab) -> None:
        for tab in [t for t in self.tabs if t is not keep]:
            self.close_tab(tab)

    def close_right_of(self, tab: BrowserTab) -> None:
        index = self.tab_widget.indexOf(tab)
        for other in self.tabs[index + 1:]:
            self.close_tab(other)

    def take_tab(self, index_or_tab) -> Optional[BrowserTab]:
        """Remove a tab WITHOUT destroying it (it is moving to another panel)."""
        index = self._index_of(index_or_tab)
        if not 0 <= index < self.tab_widget.count():
            return None
        tab = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        return tab

    def add_existing(self, tab: BrowserTab, activate: bool = True) -> None:
        self._insert(tab, self.tab_widget.count(), activate)

    def move_tab_to_panel(self, index_or_tab, other: "FilePanel") -> None:
        tab = self.take_tab(index_or_tab)
        if tab is None:
            return
        other.add_existing(tab, activate=True)
        other.set_active_panel(force_event=True)
        tab.focus_view()
        if self.tab_widget.count() == 0:
            if self.area.is_split:
                self.area.remove_panel(self)
            else:
                self.open_tab(home())

    # -- notifications from tabs ---------------------------------------------
    def tab_updated(self, tab: BrowserTab) -> None:
        """A tab's folder/title changed."""
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self.tab_widget.setTabText(index, tab.title)
            self.tab_widget.setTabIcon(index, tab.icon)
            self.tab_widget.setTabToolTip(index, tab.path)
        if tab is self.active_tab and self.is_active_panel():
            self.ctx.bus.publish(Topics.ACTIVE_LOCATION_CHANGED, path=tab.path, tab=tab)

    def refresh_tab_icons(self) -> None:
        for i, tab in enumerate(self.tabs):
            self.tab_widget.setTabIcon(i, tab.icon)

    def _on_current_changed(self, index: int) -> None:
        tab = self.active_tab
        if tab is not None and self.is_active_panel():
            self.ctx.bus.publish(Topics.ACTIVE_LOCATION_CHANGED, path=tab.path, tab=tab)
            self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=tab)

    def set_active_panel(self, force_event: bool = False) -> None:
        self.area.set_active(self, force_event)

    # -- session ---------------------------------------------------------------
    def get_state(self) -> dict:
        return {"active": self.active_index, "tabs": [t.get_state() for t in self.tabs]}

    def restore(self, state: dict) -> None:
        for tab_state in state.get("tabs") or []:
            if tab_state.get("path") or utility.is_utility_state(tab_state):
                self.open_tab(state=tab_state, activate=False, index=self.tab_widget.count())
        if not self.tabs:
            self.open_tab(home())
        self.activate(int(state.get("active", 0) or 0), focus=False)

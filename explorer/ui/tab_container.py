"""
TabContainer - a tab bar with a "+" button next to it, above the pages.

It offers the same small set of methods as Qt's QTabWidget (count, widget,
insertTab, removeTab, setCurrentIndex...), so FilePanel uses it the same way.
We lay it out ourselves instead of using QTabWidget because QTabWidget lets
each visual style position its "corner" button differently - on some (like
Linux Mint's) the "+" ended up smaller than the tabs and a few pixels low.
Here the "+" is always a square exactly as tall as the tabs.

    ┌ tab ┬ tab ┬ tab ┐┌─┐
    │                  ││+│   <- "TabStrip": QTabBar (stretches) + square button
    ├──────────────────┴┴─┤
    │  current page       │   <- QStackedWidget
    └─────────────────────┘
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget


class TabContainer(QWidget):
    currentChanged = Signal(int)
    tabCloseRequested = Signal(int)

    def __init__(self, tab_bar: QTabBar, plus_button: QToolButton, parent=None) -> None:
        super().__init__(parent)
        self._pages: List[QWidget] = []       # in the same order as the tabs
        self._bar = tab_bar
        self._plus = plus_button
        self._stack = QStackedWidget(self)
        # The strip behind the tabs: themes style "QWidget#TabStrip" (background +
        # a line along the bottom that the current tab opens into).
        strip = QWidget(self, objectName="TabStrip")
        strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top = QHBoxLayout(strip)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addWidget(tab_bar, 1, Qt.AlignmentFlag.AlignBottom)
        top.addWidget(plus_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(strip)
        layout.addWidget(self._stack, 1)
        tab_bar.currentChanged.connect(self._on_current)
        tab_bar.tabCloseRequested.connect(self.tabCloseRequested)
        tab_bar.tabMoved.connect(self._on_moved)

    # -- QTabWidget-like API -------------------------------------------------
    def tabBar(self) -> QTabBar:
        return self._bar

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> Optional[QWidget]:
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def indexOf(self, page) -> int:
        try:
            return self._pages.index(page)
        except ValueError:
            return -1

    def currentIndex(self) -> int:
        return self._bar.currentIndex()

    def currentWidget(self) -> Optional[QWidget]:
        return self.widget(self.currentIndex())

    def setCurrentIndex(self, index: int) -> None:
        self._bar.setCurrentIndex(index)

    def insertTab(self, index: int, page: QWidget, icon: QIcon, text: str) -> int:
        index = max(0, min(index, len(self._pages)))
        self._pages.insert(index, page)          # before the bar: it may emit currentChanged
        self._stack.addWidget(page)
        self._bar.insertTab(index, icon, text)
        if self._bar.currentIndex() == index:
            self._stack.setCurrentWidget(page)
        self._relayout()
        return index

    def removeTab(self, index: int) -> None:
        if not 0 <= index < len(self._pages):
            return
        page = self._pages.pop(index)
        self._stack.removeWidget(page)
        self._bar.removeTab(index)
        self._relayout()

    def setTabText(self, index: int, text: str) -> None:
        self._bar.setTabText(index, text)

    def setTabIcon(self, index: int, icon: QIcon) -> None:
        self._bar.setTabIcon(index, icon)

    def setTabToolTip(self, index: int, tip: str) -> None:
        self._bar.setTabToolTip(index, tip)

    # -- internals -------------------------------------------------------------
    def _on_current(self, index: int) -> None:
        page = self.widget(index)
        if page is not None:
            self._stack.setCurrentWidget(page)
        self.currentChanged.emit(index)

    def _on_moved(self, src: int, dst: int) -> None:
        """Tabs were dragged into a new order: keep our page list in step."""
        self._pages.insert(dst, self._pages.pop(src))

    def _relayout(self) -> None:
        """Make the layout re-measure the tab bar.

        QTabBar only tells its layout "my size changed" while it is visible.
        At startup the saved tabs are all added BEFORE the window appears, so
        without this the layout keeps the size of an empty bar (0 pixels tall)
        and the tabs stay invisible until something else forces a re-measure.
        """
        self._bar.updateGeometry()
        self.fit_plus_button()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._relayout()          # first appearance: measure the tabs added while hidden

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_plus_button()

    def fit_plus_button(self) -> None:
        """Square, and exactly as tall as the tabs."""
        height = self._bar.sizeHint().height()
        if height > 0 and (self._plus.width(), self._plus.height()) != (height, height):
            self._plus.setFixedSize(height, height)

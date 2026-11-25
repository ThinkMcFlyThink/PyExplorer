"""
ToolbarPage - choose the toolbar's buttons and their order (Settings -> Toolbar).

    Available                       On the toolbar
    ┌───────────────────┐  [Add ›]  ┌───────────────────┐  [Up]
    │ Back              │ [‹ Remove]│ New ▾             │  [Down]
    │ Forward           │           │ ── Separator      │
    │ …                 │           │ Cut               │  [Reset]

Double-click an item to add or remove it; drag items within the right-hand list
to reorder them. Every change shows on the real toolbar straight away (settings
"toolbar.items"; the layout itself is ui/toolbar.py's).
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from .. import toolbar

ID_ROLE = Qt.ItemDataRole.UserRole
REPEATABLE = (toolbar.SEPARATOR, toolbar.SPACER)


class ToolbarPage(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("AVAILABLE", self, objectName="SettingGroup"), 0, 0)
        grid.addWidget(QLabel("ON THE TOOLBAR", self, objectName="SettingGroup"), 0, 2)
        self.available = self._list()
        self.current = self._list()
        self.current.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.current.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.current.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._save))
        self.available.itemDoubleClicked.connect(lambda _i: self.add())
        self.current.itemDoubleClicked.connect(lambda _i: self.remove())
        grid.addWidget(self.available, 1, 0)
        grid.addWidget(self.current, 1, 2)
        middle = QVBoxLayout()
        middle.addStretch(1)
        self.add_btn = QPushButton("Add  ›", self)
        self.add_btn.clicked.connect(self.add)
        self.remove_btn = QPushButton("‹  Remove", self)
        self.remove_btn.clicked.connect(self.remove)
        middle.addWidget(self.add_btn)
        middle.addWidget(self.remove_btn)
        middle.addStretch(1)
        grid.addLayout(middle, 1, 1)
        side = QVBoxLayout()
        self.up_btn = QPushButton("Move up", self)
        self.up_btn.clicked.connect(lambda: self.move(-1))
        self.down_btn = QPushButton("Move down", self)
        self.down_btn.clicked.connect(lambda: self.move(1))
        reset = QPushButton("Reset", self, toolTip="Back to PyExplorer's default toolbar")
        reset.clicked.connect(self.reset)
        for b in (self.up_btn, self.down_btn):
            side.addWidget(b)
        side.addStretch(1)
        side.addWidget(reset)
        grid.addLayout(side, 1, 3)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        for lst in (self.available, self.current):
            lst.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.refresh()

    def _list(self) -> QListWidget:
        lst = QListWidget(self, objectName="SettingsList")
        lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lst.setMinimumHeight(320)
        lst.setIconSize(QSize(16, 16))
        return lst

    @staticmethod
    def _item(item_id: str, label: str, icon) -> QListWidgetItem:
        item = QListWidgetItem(icon, label)
        item.setData(ID_ROLE, item_id)
        return item

    def refresh(self) -> None:
        on = toolbar.items(self.ctx.settings)
        catalogue = {item_id: (label, icon) for item_id, label, icon in toolbar.catalogue(self.ctx)}
        keep_current = self.current.currentRow()
        self.current.blockSignals(True)
        self.current.clear()
        for item_id in on:
            if item_id in catalogue:
                label, icon = catalogue[item_id]
                self.current.addItem(self._item(item_id, label, icon))
        self.current.blockSignals(False)
        self.available.clear()
        for item_id, (label, icon) in catalogue.items():
            if item_id in REPEATABLE or item_id not in on:
                self.available.addItem(self._item(item_id, label, icon))
        if 0 <= keep_current < self.current.count():
            self.current.setCurrentRow(keep_current)
        self._update_buttons()

    def ids(self) -> List[str]:
        return [self.current.item(i).data(ID_ROLE) for i in range(self.current.count())]

    def _save(self, select: int = -1) -> None:
        ids = self.ids()
        self.ctx.settings.set("toolbar.items", [] if ids == toolbar.DEFAULT else ids)
        self.refresh()
        if select >= 0:
            self.current.setCurrentRow(min(select, self.current.count() - 1))

    def _update_buttons(self) -> None:
        row = self.current.currentRow()
        self.add_btn.setEnabled(self.available.currentItem() is not None)
        self.remove_btn.setEnabled(row >= 0)
        self.up_btn.setEnabled(row > 0)
        self.down_btn.setEnabled(0 <= row < self.current.count() - 1)

    # -- editing -------------------------------------------------------------------
    def add(self) -> None:
        item = self.available.currentItem()
        if item is None:
            return
        row = self.current.currentRow()
        at = row + 1 if row >= 0 else self.current.count()
        self.current.insertItem(at, self._item(item.data(ID_ROLE), item.text(), item.icon()))
        self._save(select=at)

    def remove(self) -> None:
        row = self.current.currentRow()
        if row >= 0:
            self.current.takeItem(row)
            self._save(select=row)

    def move(self, delta: int) -> None:
        row = self.current.currentRow()
        target = row + delta
        if 0 <= row < self.current.count() and 0 <= target < self.current.count():
            self.current.insertItem(target, self.current.takeItem(row))
            self._save(select=target)

    def reset(self) -> None:
        self.ctx.settings.set("toolbar.items", [])
        self.refresh()

    def filter(self, query: str) -> bool:
        """Shown in search results only for the page's own words ("toolbar", "buttons"…), which
        the Settings tab checks itself - not for every command that could be a button."""
        return not query

    def on_theme(self) -> None:
        self.refresh()

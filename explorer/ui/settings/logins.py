"""
LoginsPage - the network logins PyExplorer remembers (Settings -> Network).

Only servers and user names are listed; passwords stay in the system keyring
(services/credential_store.py) and are never shown. Select one or more and
press Forget.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.settings_schema import matches


class LoginsPage(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.store = ctx.credentials
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("SAVED LOGINS", self, objectName="SettingGroup"))
        self.table = QTableWidget(0, 2, self, objectName="SettingsTable")
        self.table.setHorizontalHeaderLabels(["Server", "User"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.where = QLabel(f"Passwords are kept in {self.store.where()}, never in PyExplorer's own files.", self,
                            objectName="SettingHelp")
        self.where.setWordWrap(True)
        row.addWidget(self.where, 1)
        self.forget_btn = QPushButton("Forget", self)
        self.forget_btn.clicked.connect(self.forget)
        row.addWidget(self.forget_btn)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        saved = self.store.saved()
        self.table.setRowCount(len(saved))
        for row, (key, user) in enumerate(saved):
            self.table.setItem(row, 0, QTableWidgetItem(key))
            self.table.setItem(row, 1, QTableWidgetItem(user))
        rows = max(3, len(saved))
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() +
                                  rows * self.table.verticalHeader().defaultSectionSize() + 4)
        if not saved:
            self.table.setRowCount(0)
        self._update()

    def _update(self) -> None:
        self.forget_btn.setEnabled(bool(self.table.selectedIndexes()))

    def forget(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            item = self.table.item(row, 0)
            if item is not None:
                self.store.delete(item.text())
        self.refresh()

    def filter(self, query: str) -> bool:
        return not query or matches(query, "saved logins passwords keyring forget",
                                    *(self.table.item(r, 0).text() for r in range(self.table.rowCount())
                                      if self.table.item(r, 0)))

    def on_theme(self) -> None:
        pass

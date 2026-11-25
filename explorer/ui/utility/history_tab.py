"""
HistoryTab - what was done to your files, and Undo (Ctrl+H).

    ┌ History ──────────── 42 actions · kept between sessions   [Undo] [Clear]  [✓] Keep ┐
    │ Show [Everything ▾]   Find [invoice           ]                                    │
    │ Action          │ Item                   │ Details                  │ When  │ Status│
    │ ▸ Renamed       │ 34 items               │ in Photos                │ 14:32 │       │
    │ ▸ Moved         │ report.pdf             │ Downloads → Documents    │ 14:30 │       │
    │   Moved to trash│ old.txt                │ from Desktop             │ 14:12 │ Can't │
    │ ▾ Copied        │ 2 items                │ to Backup                │ 13:58 │ Undone│
    │     a.jpg …     │                        │ /home/me/Backup/a.jpg    │       │       │

One row per action (expand it to see every file). Undo works on whole
actions or on single files, newest first. Deletes are listed but can't be
undone. The list and the undo logic live elsewhere (core/file_history.py,
FileOperations.undo, Commands.undo_history); this file is only the widgets.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QPushButton, QTreeView, QVBoxLayout)

from ...core.events import Topics
from ...core.file_history import Record, can_undo_kind
from ...core.formatting import human_time, items_label
from ...core.platform_info import home
from .base import UtilityTab


def _base(path: Optional[str]) -> str:
    """Last part of a local path or a URI (cheap: no file system lookups)."""
    if not path:
        return ""
    trimmed = path.rstrip("/\\")
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    return trimmed[cut + 1:] if cut >= 0 else trimmed


def _parent(path: Optional[str]) -> str:
    if not path:
        return ""
    trimmed = path.rstrip("/\\")
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    return trimmed[:cut] or trimmed[:cut + 1] if cut >= 0 else ""


def _folder_name(folder: str) -> str:
    return _base(folder) or folder


# ---------------------------------------------------------------------------
# The list: actions, each with its files as children
# ---------------------------------------------------------------------------
class HistoryModel(QAbstractItemModel):
    COLUMNS = ["Action", "Item", "Details", "When", "Status"]
    TOP = 0                                   # internalId of an action row; files use record row + 1

    def __init__(self, tab: "HistoryTab") -> None:
        super().__init__(tab)
        self.tab = tab
        self.rows: List[Record] = []
        self._summary: Dict[str, tuple] = {}

    def set_records(self, records: List[Record]) -> None:
        self.beginResetModel()
        self.rows = records
        self._summary = {}
        self.endResetModel()

    # -- structure -----------------------------------------------------------------
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not parent.isValid():
            return self.createIndex(row, column, self.TOP) if 0 <= row < len(self.rows) else QModelIndex()
        if parent.internalId() == self.TOP and 0 <= parent.row() < len(self.rows):
            rec = self.rows[parent.row()]
            if 0 <= row < len(rec.items) and len(rec.items) > 1:
                return self.createIndex(row, column, parent.row() + 1)
        return QModelIndex()

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        if not index.isValid() or index.internalId() == self.TOP:
            return QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, self.TOP)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self.rows)
        if parent.internalId() == self.TOP and parent.column() == 0:
            n = len(self.rows[parent.row()].items)
            return n if n > 1 else 0               # a single file is shown in the action's own row
        return 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def record_at(self, index: QModelIndex):
        """(record, item index or None for the whole action)."""
        if not index.isValid():
            return None, None
        if index.internalId() == self.TOP:
            return self.rows[index.row()], None
        return self.rows[index.internalId() - 1], index.row()

    # -- text ------------------------------------------------------------------------
    def _record_summary(self, rec: Record) -> tuple:
        """(item text, details text) for an action row - cached: records can hold 100k files."""
        cached = self._summary.get(rec.id)
        if cached is not None:
            return cached
        items = rec.items
        first = items[0]
        if len(items) == 1:
            item = self._item_text(rec, first)
        else:
            item = items_label(len(items))
        src_dirs = {_parent(it.get("src")) for it in items if it.get("src")}
        dst_dirs = {_parent(it.get("dst")) for it in items if it.get("dst")}

        def where(dirs: Set[str]) -> str:
            return _folder_name(next(iter(dirs))) if len(dirs) == 1 else f"{len(dirs):,} folders"
        if rec.kind == "rename":
            details = f"in {where(src_dirs)}"
        elif rec.kind == "move":
            details = f"{where(src_dirs)}  →  {where(dst_dirs)}"
        elif rec.kind == "copy":
            details = f"to {where(dst_dirs)}" if src_dirs != dst_dirs else f"in {where(dst_dirs)} (duplicate)"
        elif rec.kind == "create":
            details = f"in {where(dst_dirs)}"
        else:
            details = f"from {where(src_dirs)}"
        if rec.source:
            details += f"   ·  {rec.source}"
        self._summary[rec.id] = (item, details)
        return item, details

    @staticmethod
    def _item_text(rec: Record, it: dict) -> str:
        if rec.kind == "rename":
            return f"{_base(it.get('src'))}  →  {_base(it.get('dst'))}"
        if rec.kind in ("move", "copy") and _base(it.get("src")) != _base(it.get("dst")):
            return f"{_base(it.get('src'))}  →  {_base(it.get('dst'))}"
        return _base(it.get("dst") or it.get("src"))

    @staticmethod
    def _item_paths(rec: Record, it: dict) -> str:
        if rec.kind in ("rename", "move", "copy"):
            return f"{it.get('src')}  →  {it.get('dst')}"
        return it.get("dst") or it.get("src") or ""

    @staticmethod
    def item_status(rec: Record, it: dict) -> str:
        if not can_undo_kind(rec.kind):
            return "Can't be undone"
        if it.get("undone"):
            return "Undone"
        return it.get("blocked") or ""

    @staticmethod
    def record_status(rec: Record) -> str:
        if not can_undo_kind(rec.kind):
            text = "Can't be undone"
        elif rec.undone:
            text = "Undone" if len(rec.items) == len([i for i in rec.items if i.get("undone")]) else "Undone (some can't be)"
        elif rec.partly_undone:
            text = "Partly undone"
        elif not rec.pending():
            text = rec.items[0].get("blocked") if len(rec.items) == 1 else "Can't be undone"
        else:
            text = ""
        if rec.session_only:
            text = (text + " · " if text else "") + "this session only"
        return text

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rec, item_no = self.record_at(index)
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if item_no is None:
                if col == 0:
                    return rec.label
                if col in (1, 2):
                    return self._record_summary(rec)[col - 1]
                if col == 3:
                    return human_time(rec.time)
                return self.record_status(rec)
            it = rec.items[item_no]
            if col == 1:
                return self._item_text(rec, it)
            if col == 2:
                return self._item_paths(rec, it)
            if col == 4:
                return self.item_status(rec, it)
            return ""
        if role == Qt.ItemDataRole.ToolTipRole:
            if item_no is None:
                if len(rec.items) == 1:
                    tip = self._item_paths(rec, rec.items[0])
                    note = rec.items[0].get("note") or rec.items[0].get("blocked")
                    tip += f"\n{note}" if note else ""
                else:
                    tip = f"{rec.label} {items_label(len(rec.items))} - expand to see them"
                if rec.session_only:
                    tip += "\nToo large to keep after closing: kept for this session only"
                if rec.undo_label and rec.pending():
                    tip += f"\nUndo: {rec.undo_label.lower()}"
                return tip
            it = rec.items[item_no]
            note = it.get("note") or it.get("blocked")
            return self._item_paths(rec, it) + (f"\n{note}" if note else "")
        if role == Qt.ItemDataRole.ForegroundRole:
            done = rec.undone if item_no is None else bool(rec.items[item_no].get("undone"))
            if done or (col == 4 and self.data(index)):
                return QColor(self.tab.ctx.theme.palette.get("fg_dim", "#888888"))
        if role == Qt.ItemDataRole.FontRole and item_no is None and col == 0:
            font = self.tab.font()
            font.setBold(True)
            return font
        return None


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
SHOW = (("all", "Everything"), ("undoable", "What can be undone"), ("rename", "Renames"), ("move", "Moves"),
        ("copy", "Copies"), ("create", "New items"), ("deleted", "Deletes"))


class HistoryTab(UtilityTab):
    KIND = "history"
    TITLE = "History"
    SHAPE = "history"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        self._busy = False
        super().__init__(panel, ctx, state)
        self._unsubs.append(ctx.bus.subscribe(Topics.HISTORY_CHANGED, lambda **_: self.reload()))
        self._unsubs.append(ctx.bus.subscribe(Topics.FORMAT_CHANGED, lambda **_: self.tree.viewport().update()))
        self.reload()

    # ==================================================================
    # Widgets
    # ==================================================================
    def build(self, body: QVBoxLayout) -> None:
        self.undo_btn = QPushButton("Undo", self)
        self.undo_btn.setToolTip("Undo the selected actions or files (Ctrl+Z), newest first")
        self.undo_btn.clicked.connect(self.undo_selected)
        self.clear_btn = QPushButton("Clear history", self)
        self.clear_btn.clicked.connect(self.clear)
        self.keep_cb = QCheckBox("Keep after closing", self)
        self.keep_cb.setToolTip("Remember this list between sessions (very large actions are kept for this session only).\n"
                                "Off: the history is forgotten when PyExplorer closes.")
        self.keep_cb.setChecked(bool(self.ctx.settings.get("history.keep", True)))
        self.keep_cb.toggled.connect(lambda on: self.ctx.settings.set("history.keep", bool(on)))
        for w in (self.undo_btn, self.clear_btn, self.keep_cb):
            self.header.buttons.addWidget(w)

        bar = QFrame(self, objectName="UtilityPanel")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(6)
        row.addWidget(QLabel("Show", self, objectName="UtilitySection"))
        self.show_combo = QComboBox(self)
        for value, label in SHOW:
            self.show_combo.addItem(label, value)
        self.show_combo.currentIndexChanged.connect(lambda _: self.reload())
        row.addWidget(self.show_combo)
        row.addSpacing(10)
        row.addWidget(QLabel("Find", self, objectName="UtilitySection"))
        self.find_edit = QLineEdit(self, placeholderText="part of a file or folder name", clearButtonEnabled=True)
        self.find_edit.setMinimumWidth(self.find_edit.fontMetrics().horizontalAdvance(
            self.find_edit.placeholderText()) + 48)          # the whole hint fits
        self.find_edit.setMaximumWidth(360)
        self.find_edit.textChanged.connect(lambda _: self.reload())
        row.addWidget(self.find_edit)
        row.addStretch(1)
        body.addWidget(bar)

        self.model = HistoryModel(self)
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.doubleClicked.connect(self._open_location)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        h = self.tree.header()
        h.setStretchLastSection(False)
        for col, width in enumerate((170, 300, 360, 130, 190)):
            self.tree.setColumnWidth(col, width)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        body.addWidget(self.tree, 1)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_selected,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # ==================================================================
    # Filling the list
    # ==================================================================
    def _matches(self, rec: Record, show: str, needle: str) -> bool:
        if show == "undoable" and not rec.pending():
            return False
        if show == "deleted" and rec.kind not in ("trash", "delete"):
            return False
        if show not in ("all", "undoable", "deleted") and rec.kind != show:
            return False
        if needle:
            return any(needle in (it.get("src") or "").lower() or needle in (it.get("dst") or "").lower()
                       for it in rec.items)
        return True

    def reload(self) -> None:
        # keep what was expanded / selected across the refresh
        expanded = {self.model.rows[r].id for r in range(len(self.model.rows))
                    if self.tree.isExpanded(self.model.index(r, 0))}
        selected = self._selected_keys()
        history = self.ctx.history
        history.update_session_only()
        show = self.show_combo.currentData() or "all"
        needle = self.find_edit.text().strip().lower()
        self.model.set_records([r for r in history.records() if self._matches(r, show, needle)])
        sel = self.tree.selectionModel()
        for r, rec in enumerate(self.model.rows):
            top = self.model.index(r, 0)
            if rec.id in expanded:
                self.tree.setExpanded(top, True)
            for key in selected.get(rec.id, ()):
                index = top if key is None else self.model.index(key, 0, top)
                if index.isValid():
                    sel.select(index, sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
        self._update_subtitle()
        self._update_buttons()

    def _update_subtitle(self) -> None:
        history = self.ctx.history
        total = len(history)
        text = f"{total:,} action{'s' if total != 1 else ''}"
        if len(self.model.rows) != total:
            text += f" ({len(self.model.rows):,} shown)"
        if self.ctx.settings.get("history.keep", True):
            big = sum(1 for r in history.records() if r.session_only)
            text += " · kept between sessions"
            if big:
                text += f" ({big} too large or too old to keep)"
        else:
            text += " · forgotten when PyExplorer closes"
        self.header.set_subtitle(text)
        self.status_note = text
        # (not self.updated(): that announces a location change, which would e.g. cd the terminal home)
        self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=self)

    # ==================================================================
    # Selection, undo
    # ==================================================================
    def _selected_keys(self) -> Dict[str, list]:
        """{record id: [None (the whole action) and/or item indexes]}"""
        keys: Dict[str, list] = {}
        for index in self.tree.selectionModel().selectedRows(0):
            rec, item_no = self.model.record_at(index)
            if rec is not None:
                keys.setdefault(rec.id, []).append(item_no)
        return keys

    def _selection_to_undo(self):
        """(record ids, {record id: [item indexes]} for records where only some files are picked)."""
        ids, indexes = [], {}
        for rec_id, keys in self._selected_keys().items():
            ids.append(rec_id)
            if None not in keys:
                indexes[rec_id] = [k for k in keys if k is not None]
        return ids, indexes

    def _undoable(self, ids, indexes) -> int:
        n = 0
        for rec_id in ids:
            rec = self.ctx.history.get(rec_id)
            if rec is not None:
                pending = rec.pending()
                n += len([i for i in pending if i in indexes[rec_id]]) if rec_id in indexes else len(pending)
        return n

    def _update_buttons(self) -> None:
        ids, indexes = self._selection_to_undo()
        n = self._undoable(ids, indexes)
        self.undo_btn.setEnabled(not self._busy and n > 0)
        self.undo_btn.setText(f"Undo {items_label(n)}" if n and not self._busy else
                              ("Undoing…" if self._busy else "Undo"))
        self.clear_btn.setEnabled(len(self.ctx.history) > 0)

    def undo_selected(self) -> None:
        if self._busy:
            return
        ids, indexes = self._selection_to_undo()
        n = self._undoable(ids, indexes)
        if not n:
            return
        removing = [r for r in (self.ctx.history.get(i) for i in ids) if r and r.kind in ("copy", "create")]
        if removing:
            from .. import dialogs
            what = "the copies" if all(r.kind == "copy" for r in removing) else "the new items"
            if not dialogs.confirm(self, "Undo", f"Undoing this removes {what} again "
                                   "(to the trash where there is one).\n\nContinue?", "Undo"):
                return
        self._busy = True
        self._update_buttons()

        def done() -> None:
            self._busy = False
            self._update_buttons()
        self.ctx.commands.undo_history(ids, indexes, on_done=done)

    def clear(self) -> None:
        from .. import dialogs
        if dialogs.confirm(self, "Clear history", "Forget the whole history?\n"
                           "(Your files are not touched - only this list is cleared.)", "Clear history"):
            self.ctx.history.clear()

    def forget_selected(self) -> None:
        self.ctx.history.remove([rid for rid, keys in self._selected_keys().items() if None in keys])

    # ==================================================================
    # Going to the files
    # ==================================================================
    def _current_path(self, rec: Record, it: dict) -> Optional[str]:
        """Where this item is now (as far as we know)."""
        if rec.kind in ("trash", "delete"):
            return None
        if it.get("undone"):
            return it.get("src") if rec.kind in ("rename", "move") else None
        return it.get("dst")

    def _open_location(self, index: QModelIndex) -> None:
        rec, item_no = self.model.record_at(index)
        if rec is None or (item_no is None and len(rec.items) > 1):
            return                                   # (double-click on an action just expands it)
        it = rec.items[item_no or 0]
        path = self._current_path(rec, it)
        if path:
            self.navigate(_parent(path) or path, select=[path])
        else:
            folder = _parent(it.get("src") or it.get("dst"))
            if folder:
                self.navigate(folder)

    def _context_menu(self, pos: QPoint) -> None:
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        if not self.tree.selectionModel().isRowSelected(index.row(), index.parent()):
            self.tree.selectionModel().select(index, self.tree.selectionModel().SelectionFlag.ClearAndSelect |
                                              self.tree.selectionModel().SelectionFlag.Rows)
        rec, item_no = self.model.record_at(index)
        menu = QMenu(self)
        ids, indexes = self._selection_to_undo()
        n = self._undoable(ids, indexes)
        label = rec.undo_label if len(ids) == 1 and rec.undo_label else "Undo"
        act = menu.addAction(f"{label} ({items_label(n)})" if n else label, self.undo_selected)
        act.setEnabled(n > 0 and not self._busy)
        act.setShortcut(QKeySequence("Ctrl+Z"))
        menu.addSeparator()
        it = rec.items[item_no or 0]
        if item_no is not None or len(rec.items) == 1:
            path = self._current_path(rec, it)
            menu.addAction("Open containing folder", lambda: self._open_location(index)).setEnabled(
                bool(path or _parent(it.get("src") or it.get("dst"))))
            menu.addAction("Copy path", lambda: self.ctx.commands.copy_text(path or it.get("src") or ""))
        if any(None in keys for keys in self._selected_keys().values()):
            menu.addAction("Remove from history", self.forget_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ==================================================================
    # Tab protocol
    # ==================================================================
    @property
    def path(self) -> str:
        return home()

    def focus_view(self) -> None:
        self.tree.setFocus()

    def get_state(self) -> dict:
        return {"utility": self.KIND, "show": self.show_combo.currentData(), "find": self.find_edit.text()}

    def restore(self, state: dict) -> None:
        for i in range(self.show_combo.count()):
            if self.show_combo.itemData(i) == state.get("show"):
                self.show_combo.setCurrentIndex(i)
        self.find_edit.setText(state.get("find") or "")

    def _on_theme(self) -> None:
        super()._on_theme()
        self.tree.viewport().update()

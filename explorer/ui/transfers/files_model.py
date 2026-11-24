"""TransferFilesModel - one row per file/folder of a transfer, with its state:
a green tick when it arrived (and was verified), a red cross when it failed."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ...core import transfer_model as tm
from ...core.formatting import human_size

# state -> (mark, palette colour)
MARKS = {
    tm.VERIFIED: ("✓", "ok"), tm.COPIED: ("✓", "ok"), tm.MOVED: ("✓", "ok"),
    tm.FAILED: ("✗", "error"), tm.VERIFY_FAILED: ("✗", "error"),
    tm.COPYING: ("↻", "accent"), tm.VERIFYING: ("⋯", "accent"),
    tm.PENDING: ("•", "fg_dim"), tm.SKIPPED: ("↷", "ansi_yellow"), tm.NOT_COPIED: ("–", "ansi_yellow"),
}
FILTERS = (("all", "All"), ("problems", "Problems"), ("done", "Done"), ("waiting", "Not done yet"))


def wanted(state: str, which: str) -> bool:
    if which == "problems":
        return state in tm.BAD or state in (tm.NOT_COPIED, tm.SKIPPED)
    if which == "done":
        return state in tm.GOOD
    if which == "waiting":
        return state not in tm.FINAL
    return True


class TransferFilesModel(QAbstractTableModel):
    COLUMNS = ["", "Name", "Size", "Status"]

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.transfer: Optional[tm.Transfer] = None
        self.which = "all"
        self._rows: Optional[List[int]] = None        # filtered row -> file index (None = all)
        self._count = 0

    def set_transfer(self, transfer: Optional[tm.Transfer]) -> None:
        self.beginResetModel()
        self.transfer = transfer
        self._rebuild()
        self.endResetModel()

    def set_filter(self, which: str) -> None:
        self.beginResetModel()
        self.which = which
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        files = self.transfer.files if self.transfer else []
        self._rows = None if self.which == "all" else [i for i, f in enumerate(files) if wanted(f.state, self.which)]
        self._count = len(files) if self._rows is None else len(self._rows)

    def refresh(self) -> None:
        """Called on a timer while the transfer runs: new rows or new states."""
        old = self._count
        old_rows = self._rows
        self._rebuild()
        if self._count != old or (old_rows is not None and old_rows != self._rows):
            if self._count > old and (self._rows is None or (old_rows or []) == self._rows[:old]):
                self.beginInsertRows(QModelIndex(), old, self._count - 1)    # (keeps the scroll position)
                self.endInsertRows()
            else:
                self.beginResetModel()
                self.endResetModel()
        if self._count:
            self.dataChanged.emit(self.index(0, 0), self.index(self._count - 1, len(self.COLUMNS) - 1))

    def file_at(self, row: int):
        if self.transfer is None or row < 0 or row >= self._count:
            return None
        return self.transfer.files[row if self._rows is None else self._rows[row]]

    def row_of_current(self) -> int:
        """Row of the file being copied now (for "follow")."""
        if self.transfer is None:
            return -1
        for row in range(self._count):
            f = self.file_at(row)
            if f is not None and f.state in (tm.COPYING, tm.VERIFYING):
                return row
        return -1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        f = self.file_at(index.row())
        if f is None:
            return None
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return MARKS.get(f.state, ("?", ""))[0]
            if col == 1:
                return f.rel + ("/" if f.is_dir else "")
            if col == 2:
                return "" if f.is_dir else human_size(f.size)
            label = tm.STATE_LABELS.get(f.state, f.state)
            return f"{label}: {f.error}" if f.error else label
        if role == Qt.ItemDataRole.ForegroundRole and col in (0, 3):
            colour = MARKS.get(f.state, ("", "fg_dim"))[1]
            if col == 3 and f.state in tm.GOOD:
                colour = "fg_dim"
            return QColor(self.ctx.theme.palette.get(colour, "#888888"))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0:
                return int(Qt.AlignmentFlag.AlignCenter)
            if col == 2:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [f"From: {f.src}", f"To: {f.dst}", tm.STATE_LABELS.get(f.state, f.state)]
            if f.error:
                lines.append(f.error)
            return "\n".join(lines)
        if role == Qt.ItemDataRole.FontRole and col == 0:
            font = self.ctx.app.font() if self.ctx.app else None
            if font is not None:
                font.setBold(True)
            return font
        return None

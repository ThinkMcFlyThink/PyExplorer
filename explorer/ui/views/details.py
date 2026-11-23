"""
DetailsView - one row per item with sortable, resizable columns.
TreeView    - the same columns, plus expandable folders (Konqueror/Dolphin style).

Click a column header to sort, drag its edge to resize, right-click it to
choose which columns are shown. Widths and columns are global settings.

Why two different Qt classes? Details uses QTableView, which only ever looks
at the rows on screen - a 100,000-file folder refreshes in milliseconds.
A tree needs QTreeView, which has to visit every row to lay them out, so
it is slower on gigantic folders (still fine for normal ones).
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView, QTreeView

from ...core.events import Topics
from ..models.file_model import COLUMN_TITLES
from .base import ViewBehaviour

OPTIONAL_COLUMNS = ["size", "type", "modified", "status"]


class _ColumnsMixin:
    """Header behaviour shared by DetailsView and TreeView."""

    def setup_columns(self) -> None:
        header = self.column_header()
        header.setSectionsMovable(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        bold = QFont()                  # ONLY "bold" is set: typeface + size still follow the app font
        bold.setBold(True)
        header.setFont(bold)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        header.sectionClicked.connect(self._on_header_clicked)
        header.sectionResized.connect(self._on_section_resized)
        self._applying_widths = False
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save_widths)
        unsub = self.ctx.bus.subscribe(Topics.SORT_CHANGED, lambda **_: self.update_sort_indicator())
        self.destroyed.connect(lambda *_: unsub())

    def column_header(self) -> QHeaderView:
        raise NotImplementedError

    def columns(self) -> List[str]:
        return self.model().columns if self.model() is not None else []

    def apply_column_widths(self) -> None:
        widths = self.ctx.settings.get("view.columns", {})
        self._applying_widths = True
        for i, col in enumerate(self.columns()):
            self.column_header().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(i, int(widths.get(col, 120)))
        self._applying_widths = False
        self.update_sort_indicator()

    def update_sort_indicator(self) -> None:
        if self.model() is None:
            return
        key = self.ctx.settings.get("view.sort_key", "name")
        order = Qt.SortOrder.DescendingOrder if self.ctx.settings.get("view.sort_reverse") else Qt.SortOrder.AscendingOrder
        cols = self.columns()
        self.column_header().setSortIndicator(cols.index(key) if key in cols else -1, order)

    def _on_header_clicked(self, section: int) -> None:
        cols = self.columns()
        if 0 <= section < len(cols) and cols[section] not in ("location", "status"):
            self.host.on_view_sort(cols[section])
        QTimer.singleShot(0, self.update_sort_indicator)    # undo Qt's own indicator flip

    def _on_section_resized(self, *_) -> None:
        if not self._applying_widths:
            self._save_timer.start()

    def _save_widths(self) -> None:
        widths = dict(self.ctx.settings.get("view.columns", {}))
        cols = self.columns()
        for i, col in enumerate(cols[:-1]):          # the last column stretches; don't store it
            widths[col] = self.columnWidth(i)
        self.ctx.settings.set("view.columns", widths)

    def _header_menu(self, pos) -> None:
        menu = QMenu(self)
        visible = list(self.ctx.settings.get("view.visible_columns", ["name"] + OPTIONAL_COLUMNS))
        for col in OPTIONAL_COLUMNS:
            action = menu.addAction(COLUMN_TITLES[col])
            action.setCheckable(True)
            action.setChecked(col in visible)
            action.triggered.connect(lambda checked, c=col: self.ctx.commands.toggle_column(c))
        menu.exec(self.column_header().mapToGlobal(pos))


class DetailsView(_ColumnsMixin, ViewBehaviour, QTableView):
    ZOOM_SIZES = [16, 20, 24, 32, 48, 64]
    mode = "details"
    full_row_hover = True

    def __init__(self, ctx, host, zoom: int = 1, parent=None) -> None:
        QTableView.__init__(self, parent)
        self.setup_behaviour(ctx, host, zoom)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setCornerButtonEnabled(False)
        self.setTabKeyNavigation(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)      # constant row height = fast
        self.setup_columns()

    def column_header(self) -> QHeaderView:
        return self.horizontalHeader()

    def configure_model(self, model) -> None:
        model.set_tree_enabled(False)
        model.thumbnail_size = 0
        self.connect_model(model.modelReset, self.apply_column_widths)
        self.apply_column_widths()

    def apply_zoom(self) -> None:
        super().apply_zoom()
        line = QFontMetrics(self.font()).height()
        self.verticalHeader().setDefaultSectionSize(max(self.icon_px + 6, line + 8))

    def moveCursor(self, action, modifiers):
        """Left/Right don't wander between columns (a row is one item), and
        Home/End jump to the first/last item rather than the row's ends."""
        CA = QAbstractItemView.CursorAction
        if action in (CA.MoveLeft, CA.MoveRight):
            return self.currentIndex()
        model = self.model()
        if action in (CA.MoveHome, CA.MoveEnd) and model is not None and model.rowCount() > 0:
            return model.index(0 if action == CA.MoveHome else model.rowCount() - 1, 0)
        index = super().moveCursor(action, modifiers)
        return index.siblingAtColumn(0) if index.isValid() else index


class TreeView(_ColumnsMixin, ViewBehaviour, QTreeView):
    ZOOM_SIZES = [16, 20, 24, 32, 48, 64]
    mode = "tree"

    def __init__(self, ctx, host, zoom: int = 1, parent=None) -> None:
        QTreeView.__init__(self, parent)
        self.setup_behaviour(ctx, host, zoom)
        self.setUniformRowHeights(True)          # big speed-up for big folders
        self.setAllColumnsShowFocus(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setExpandsOnDoubleClick(False)      # double-click opens, the arrow expands
        self.setAnimated(False)
        self.setSortingEnabled(False)            # the tab sorts (folders-first, natural order)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setIndentation(18)
        self.setup_columns()
        self._expanded = set()
        self.expanded.connect(self._on_expanded)
        self.collapsed.connect(self._on_collapsed)

    def column_header(self) -> QHeaderView:
        return self.header()

    def configure_model(self, model) -> None:
        model.thumbnail_size = 0
        model.set_tree_enabled(True)
        self.connect_model(model.modelReset, self.apply_column_widths)
        self.connect_model(model.rowsInserted, self._on_rows_inserted)
        self.apply_column_widths()

    def apply_zoom(self) -> None:
        super().apply_zoom()
        self.doItemsLayout()

    # -- expanded folders ----------------------------------------------------------
    def _on_expanded(self, index) -> None:
        e = self.entry_at(index)
        if e:
            self._expanded.add(e.path)

    def _on_collapsed(self, index) -> None:
        e = self.entry_at(index)
        if e:
            self._expanded.discard(e.path)

    def _on_rows_inserted(self, parent, first: int, last: int) -> None:
        """Children arrived: re-open any sub-folders that were open before."""
        if not self._expanded:
            return
        model = self.model()
        for row in range(first, last + 1):
            idx = model.index(row, 0, parent)
            e = model.entry(idx)
            if e is not None and e.path in self._expanded and not self.isExpanded(idx):
                self.expand(idx)

    def reset_tree(self) -> None:
        """The tab navigated elsewhere."""
        self._expanded.clear()

    def expand_paths(self, paths) -> None:
        model = self.model()
        for path in sorted(paths, key=len):             # parents before children
            idx = model.index_for_path(path)
            if idx.isValid():
                self.expand(idx)                         # loads the folder in the background if needed
            else:
                self._expanded.add(path)                 # parent not loaded yet; opened when it arrives

    # -- state across refreshes & sessions -------------------------------------
    def save_extra_state(self, state: dict) -> None:
        state["expanded"] = set(self._expanded)

    def restore_extra_state(self, state: dict) -> None:
        wanted = state.get("expanded") or set()
        self.expand_paths(wanted)
        self._expanded |= set(wanted)

    def get_state(self) -> dict:
        return {"expanded": sorted(self._expanded)}

    def set_state(self, state: dict) -> None:
        self.expand_paths((state or {}).get("expanded", []))

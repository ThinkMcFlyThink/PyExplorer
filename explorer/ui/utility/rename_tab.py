"""
RenameTab - the bulk Rename utility (Ctrl+F2), in the spirit of Bulk Rename Utility.

    ┌ Rename ───────────────────────── in Downloads · 34 items             [Undo last rename] ┐
    │ Show [Files and folders ▾]  Only [*.jpg     ]  [ ] Include subfolders   Tick: all none ⇄ │
    │ ✓ │ Current name          │ New name                  │ Status                         │
    │ ✓ │ IMG_0042.JPG          │ Rome 0042 001.jpg         │                                │
    │ ✓ │ IMG_0043.JPG          │ Rome 0043 002.jpg         │                                │
    ├──────────────────────────────────────────────────────────────────────────────────────────┤
    │ [Replace] [Remove] [Add] [Move/Copy] [Case & Name] [Numbering] [Extension]   (cards)      │
    ├──────────────────────────────────────────────────────────────────────────────────────────┤
    │ 2 of 34 will be renamed                                   [Reset rules]  [Rename 2 items] │

Two ways in:
  * a FOLDER: everything in it (optionally its sub-folders too), filtered by
    type and a name pattern - opened with Ctrl+F2 in a folder tab;
  * a LIST of files: exactly the items selected when you pressed Ctrl+F2 / F2
    (also search results from many folders).

The rules and the checks are core/rename_rules.py (pure, tested); the safe
renaming (swaps, chains, undo) is services/bulk_rename.py. This file is only
the widgets.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QSplitter, QTableView, QToolButton, QVBoxLayout, QWidget)

from ...core.events import Topics
from ...core.platform_info import home
from ...core.rename_rules import PlanRow, RenameItem, RenameRules, plan
from ...core.sorting import natural_key
from ...services.bulk_rename import rename_all, undo_pairs
from .base import UtilityTab

PREVIEW_DELAY_MS = 60


# ---------------------------------------------------------------------------
# The Before / After table
# ---------------------------------------------------------------------------
class PreviewModel(QAbstractTableModel):
    COLUMNS = ["", "Current name", "New name", "Folder"]     # a problem's reason: tooltip on New name

    def __init__(self, tab: "RenameTab") -> None:
        super().__init__(tab)
        self.tab = tab
        self.rows: List[PlanRow] = []
        self.show_folder = False

    def set_rows(self, rows: List[PlanRow]) -> None:
        if len(rows) == len(self.rows) and self.rows:
            self.rows = rows
            self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.COLUMNS) - 1))
        else:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        col = index.column()
        pal = self.tab.ctx.theme.palette
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if row.item.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return row.item.name
            if col == 2:
                return row.new
            if col == 3:
                return row.item.parent
        if role == Qt.ItemDataRole.DecorationRole and col == 1:
            return self.tab.ctx.icons.folder() if row.item.is_dir else None
        if role == Qt.ItemDataRole.ForegroundRole:
            if row.status == "error" and col == 2:
                return QColor(pal.get("error", "#e05252"))
            if row.status in ("same", "skip") and col == 2:
                return QColor(pal.get("fg_dim", "#888888"))
            if row.status == "rename" and col == 2:
                return QColor(pal.get("accent", "#3d8ee8"))
            if col == 3:
                return QColor(pal.get("fg_dim", "#888888"))
        if role == Qt.ItemDataRole.FontRole and col == 2 and row.status == "rename":
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 2:
                return row.message or {"same": "Unchanged", "skip": "Not ticked: keeps its name"}.get(row.status)
            return row.item.path if col in (1, 3) else None
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            self.rows[index.row()].item.checked = Qt.CheckState(value) == Qt.CheckState.Checked
            self.tab.schedule_preview()
            return True
        return False


class CardGrid(QWidget):
    """The rule cards, in as many columns as fit the scroll area around them
    (re-flowed whenever its width changes). ``on_reflow`` is called after a
    re-flow, when the height the cards need may have changed."""
    CARD_WIDTH = 270

    def __init__(self, cards: List[QWidget], scroll: QScrollArea, on_reflow=lambda: None) -> None:
        super().__init__(scroll)
        self.scroll = scroll
        self.on_reflow = on_reflow
        scroll.viewport().installEventFilter(self)
        self.cards = cards
        for card in cards:                           # never squash a card: scroll instead
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(10, 8, 10, 8)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._columns = 0
        self._place(1)

    def _place(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards:
            self.grid.removeWidget(card)
        for c in range(8):
            self.grid.setColumnStretch(c, 0)
        for k, card in enumerate(self.cards):
            self.grid.addWidget(card, k // columns, k % columns, Qt.AlignmentFlag.AlignTop)
        for c in range(columns):
            self.grid.setColumnStretch(c, 1)

    def needed_height(self) -> int:
        """Height that shows every card without a scroll bar."""
        self.grid.activate()
        return self.grid.minimumSize().height()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            # The scroll AREA's width (not the viewport's) decides, so a scroll bar
            # coming or going can't flip the number of columns back and forth.
            before = self._columns
            self._place(max(1, min(4, (self.scroll.width() - 32) // self.CARD_WIDTH)))
            if self._columns != before:
                QTimer.singleShot(0, self.on_reflow)
        return False


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
class RenameTab(UtilityTab):
    KIND = "rename"
    TITLE = "Rename"
    SHAPE = "rename"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        self.folder: Optional[str] = None
        self.files: List[str] = []
        self.items: List[RenameItem] = []
        self.rows: List[PlanRow] = []
        self.rule_widgets: Dict[str, QWidget] = {}
        self._taken: Dict[str, Set[str]] = {}       # parent folder -> names in it (for "already exists")
        self._initial: Optional[Set[str]] = None    # opened with a selection: tick only these (once)
        self._unticked: Set[str] = set()            # what the user unticked (kept across reloads)
        self._undo: List = []
        self._history_id: Optional[str] = None     # the History tab's record of the last rename
        self._busy = False
        self._loading = False
        self._rule_error = ""
        self._restoring = True
        super().__init__(panel, ctx, state)
        self._timer = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS, timeout=self._preview)
        self._restoring = False
        self.reload()

    # ==================================================================
    # Widgets
    # ==================================================================
    def build(self, body: QVBoxLayout) -> None:
        self.undo_btn = QPushButton("Undo last rename", self)
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        self.header.buttons.addWidget(self.undo_btn)
        self._unsubs.append(self.ctx.bus.subscribe(Topics.HISTORY_CHANGED, lambda **_: self._history_changed()))

        # -- which items ------------------------------------------------------
        bar = QFrame(self, objectName="UtilityPanel")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(6)
        self.show_label = QLabel("Show", self, objectName="UtilitySection")
        self.show_combo = QComboBox(self)
        for value, label in (("all", "Files and folders"), ("files", "Files only"), ("folders", "Folders only")):
            self.show_combo.addItem(label, value)
        self.show_combo.currentIndexChanged.connect(lambda _: self.reload())
        self.mask_label = QLabel("Only", self, objectName="UtilitySection")
        self.mask_edit = QLineEdit(self, placeholderText="e.g. *.jpg;*.png", clearButtonEnabled=True)
        self.mask_edit.setMaximumWidth(220)
        self.mask_edit.textChanged.connect(lambda _: self._apply_mask())
        self.sub_cb = QCheckBox("Include subfolders", self)
        self.sub_cb.toggled.connect(lambda _: self.reload())
        for w in (self.show_label, self.show_combo, self.mask_label, self.mask_edit, self.sub_cb):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(QLabel("Tick", self, objectName="UtilitySection"))
        for text, tip, fn in (("All", "Tick every item", lambda: self.tick("all")),
                              ("None", "Untick every item", lambda: self.tick("none")),
                              ("Invert", "Swap ticked and unticked", lambda: self.tick("invert")),
                              ("Selected", "Tick only the rows selected in the list", lambda: self.tick("selected"))):
            b = QToolButton(self, text=text, toolTip=tip, autoRaise=True)
            b.clicked.connect(fn)
            row.addWidget(b)
        body.addWidget(bar)

        # -- before / after table + rules, one above the other ---------------------------
        split = QSplitter(Qt.Orientation.Vertical, self)
        split.setChildrenCollapsible(False)
        self.model = PreviewModel(self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 8)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 30)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 340)
        self.table.setColumnWidth(2, 380)
        h.setStretchLastSection(True)
        bold = QFont()
        bold.setBold(True)
        h.setFont(bold)
        self.table.setColumnHidden(3, True)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.table, activated=self._toggle_selected)
        split.addWidget(self.table)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Compact cards, Bulk Rename Utility style, in the order the rules apply.
        self.cards = CardGrid([self._card_case(), self._card_replace(), self._card_remove(), self._card_move(),
                               self._card_add(), self._card_numbering(), self._card_extension(), self._card_order()],
                              scroll, on_reflow=self._fit_rules)
        scroll.setWidget(self.cards)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(120)
        self.rules_scroll = scroll
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)                 # the list takes whatever the rules don't need
        split.setStretchFactor(1, 0)
        split.setCollapsible(0, False)
        split.splitterMoved.connect(self._user_moved_split)
        self.split = split
        self._user_split = False
        body.addWidget(split, 1)

        # -- footer --------------------------------------------------------------------------------
        footer = QFrame(self, objectName="UtilityFooter")
        frow = QHBoxLayout(footer)
        frow.setContentsMargins(12, 6, 12, 6)
        self.summary_label = QLabel("", footer)
        frow.addWidget(self.summary_label, 1)
        reset = QPushButton("Reset rules", footer)
        reset.clicked.connect(self.reset_rules)
        self.rename_btn = QPushButton("Rename", footer)
        self.rename_btn.setDefault(True)
        self.rename_btn.clicked.connect(self.apply)
        frow.addWidget(reset)
        frow.addWidget(self.rename_btn)
        body.addWidget(footer)

    def _fit_rules(self) -> None:
        """The rules panel: exactly as tall as its cards (no scroll bar, no empty
        space) - unless the window is too short, then the list keeps ~5 rows and
        the rules scroll. Re-done when the cards re-flow into more/fewer columns."""
        need = self.cards.needed_height()
        self.rules_scroll.setMaximumHeight(need)
        total = self.split.height()
        if total <= 0:
            return
        min_list = self.table.verticalHeader().defaultSectionSize() * 6 + self.table.horizontalHeader().height()
        rules = max(self.rules_scroll.minimumHeight(), min(need, total - min_list - self.split.handleWidth()))
        if self.split.sizes()[1] == rules:
            return
        self.split.setSizes([total - rules - self.split.handleWidth(), rules])

    def _user_moved_split(self, *_) -> None:
        self._user_split = True                     # from now on the user's position stays

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._user_split:
            QTimer.singleShot(0, self._fit_rules)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_split:                     # a taller window: show more of the rules, up to all of them
            QTimer.singleShot(0, self._fit_rules)

    # -- rule cards ----------------------------------------------------------------------------------
    def _card(self, title: str):
        box = QGroupBox(title, self)
        form = QGridLayout(box)
        form.setContentsMargins(8, 12, 8, 8)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(4)
        form.setRowStretch(20, 1)                  # keep the rows together at the top
        return box, form

    def _text(self, attr: str, placeholder: str = "") -> QLineEdit:
        w = QLineEdit(self, placeholderText=placeholder)
        w.setMinimumWidth(40)
        w.textChanged.connect(self.schedule_preview)
        self.rule_widgets[attr] = w
        return w

    def _check(self, attr: str, text: str) -> QCheckBox:
        w = QCheckBox(text, self)
        w.toggled.connect(self.schedule_preview)
        self.rule_widgets[attr] = w
        return w

    def _number(self, attr: str, lo: int, hi: int, value: int = 0, special: str = "") -> QSpinBox:
        w = QSpinBox(self, minimum=lo, maximum=hi, value=value)
        w.setMaximumWidth(72)
        if special:
            w.setSpecialValueText(special)                 # shown at the minimum, e.g. "off"
        w.valueChanged.connect(self.schedule_preview)
        self.rule_widgets[attr] = w
        return w

    def _choice(self, attr: str, choices) -> QComboBox:
        w = QComboBox(self)
        for value, label in choices:
            w.addItem(label, value)
        w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        w.setMinimumContentsLength(5)
        w.currentIndexChanged.connect(self.schedule_preview)
        self.rule_widgets[attr] = w
        return w

    def _card_replace(self) -> QGroupBox:
        box, f = self._card("Replace")
        f.addWidget(QLabel("Find"), 0, 0)
        f.addWidget(self._text("replace_find", "text to find"), 0, 1)
        f.addWidget(QLabel("With"), 1, 0)
        f.addWidget(self._text("replace_with", "replacement (may be empty)"), 1, 1)
        f.addWidget(self._check("replace_case", "Match case"), 2, 1)
        regex = self._check("replace_regex", "Regular expression")
        regex.setToolTip("Python regular expression; use \\1, \\2 in With for the groups")
        f.addWidget(regex, 3, 1)
        return box

    def _card_remove(self) -> QGroupBox:
        box, f = self._card("Remove")
        f.addWidget(QLabel("First"), 0, 0)
        f.addWidget(self._number("remove_first", 0, 999, 0, "–"), 0, 1)
        f.addWidget(QLabel("Last"), 0, 2)
        f.addWidget(self._number("remove_last", 0, 999, 0, "–"), 0, 3)
        f.addWidget(QLabel("From"), 1, 0)
        f.addWidget(self._number("remove_from", 0, 999, 0, "–"), 1, 1)
        f.addWidget(QLabel("To"), 1, 2)
        f.addWidget(self._number("remove_to", 0, 999, 0, "–"), 1, 3)
        f.addWidget(QLabel("Crop"), 2, 0)
        f.addWidget(self._choice("crop_mode", [("none", "—"), ("before", "Before"), ("after", "After")]), 2, 1)
        f.addWidget(self._text("crop_text", "text"), 2, 2, 1, 2)
        f.addWidget(QLabel("Chars"), 3, 0)
        f.addWidget(self._text("remove_chars", "e.g. _-()"), 3, 1, 1, 3)
        f.addWidget(self._check("remove_digits", "Digits"), 4, 0, 1, 2)
        f.addWidget(self._check("remove_trim", "Trim ends"), 4, 2, 1, 2)
        f.addWidget(self._check("remove_double_spaces", "Double spaces"), 5, 0, 1, 2)
        return box

    def _card_add(self) -> QGroupBox:
        box, f = self._card("Add")
        f.addWidget(QLabel("Prefix"), 0, 0)
        f.addWidget(self._text("add_prefix", "at the start"), 0, 1, 1, 3)
        f.addWidget(QLabel("Insert"), 1, 0)
        f.addWidget(self._text("add_insert", "text"), 1, 1)
        f.addWidget(QLabel("at"), 1, 2)
        f.addWidget(self._number("add_insert_pos", -99, 999, 1), 1, 3)
        f.addWidget(QLabel("Suffix"), 2, 0)
        f.addWidget(self._text("add_suffix", "before the extension"), 2, 1, 1, 3)
        words = self._check("add_word_space", "Space between words")
        words.setToolTip("HolidayPhotoRome → Holiday Photo Rome")
        f.addWidget(words, 3, 0, 1, 4)
        return box

    def _card_move(self) -> QGroupBox:
        box, f = self._card("Move / Copy")
        f.addWidget(self._choice("move_mode", [("none", "—"), ("move", "Move"), ("copy", "Copy")]), 0, 0)
        f.addWidget(self._choice("move_what", [("first", "first"), ("last", "last")]), 0, 1)
        f.addWidget(self._number("move_count", 0, 999, 0, "N"), 0, 2)
        f.addWidget(QLabel("chars to"), 1, 0)
        f.addWidget(self._choice("move_to", [("end", "the end"), ("start", "the start"), ("position", "pos.")]), 1, 1)
        f.addWidget(self._number("move_pos", -99, 999, 1), 1, 2)
        f.addWidget(QLabel("Separator"), 2, 0)
        f.addWidget(self._text("move_sep", "e.g.  - "), 2, 1, 1, 2)
        return box

    def _card_case(self) -> QGroupBox:
        box, f = self._card("Case && Name")
        f.addWidget(QLabel("Case"), 0, 0)
        f.addWidget(self._choice("case_mode", [("same", "Same"), ("lower", "lower case"), ("upper", "UPPER CASE"),
                                               ("title", "Title Case"), ("sentence", "Sentence case"),
                                               ("invert", "iNVERT cASE")]), 0, 1, 1, 2)
        f.addWidget(QLabel("Name"), 1, 0)
        f.addWidget(self._choice("name_mode", [("keep", "Keep"), ("remove", "Remove"), ("fixed", "Replace with"),
                                               ("reverse", "Reverse")]), 1, 1)
        f.addWidget(self._text("name_text", "new name"), 1, 2)
        return box

    def _card_numbering(self) -> QGroupBox:
        box, f = self._card("Numbering")
        f.addWidget(QLabel("Mode"), 0, 0)
        f.addWidget(self._choice("num_mode", [("none", "—"), ("prefix", "Prefix"), ("suffix", "Suffix"),
                                              ("insert", "Insert at")]), 0, 1)
        f.addWidget(self._number("num_pos", -99, 999, 1), 0, 2)
        f.addWidget(QLabel("Start"), 1, 0)
        f.addWidget(self._number("num_start", -99999, 999999, 1), 1, 1)
        f.addWidget(QLabel("Step"), 1, 2)
        f.addWidget(self._number("num_step", -999, 999, 1), 1, 3)
        f.addWidget(QLabel("Digits"), 2, 0)
        f.addWidget(self._number("num_pad", 1, 10, 1), 2, 1)
        f.addWidget(QLabel("Sep."), 2, 2)
        sep = self._text("num_sep", "")
        sep.setText(" ")
        f.addWidget(sep, 2, 3)
        return box

    def _card_extension(self) -> QGroupBox:
        box, f = self._card("Extension")
        f.addWidget(self._choice("ext_mode", [("same", "Same"), ("lower", "lower case"), ("upper", "UPPER CASE"),
                                              ("title", "Title"), ("remove", "Remove"), ("fixed", "New:"),
                                              ("extra", "Add extra:")]), 0, 0)
        f.addWidget(self._text("ext_text", "e.g. jpg"), 1, 0)
        f.setRowStretch(2, 1)
        return box

    def _card_order(self) -> QGroupBox:
        box, f = self._card("How it works")
        # (No word wrap: a label whose height depends on its width upsets the card grid's layout.)
        hint = QLabel("Rules change the name without its extension,\n"
                      "in this order: Name → Replace → Case →\n"
                      "Remove → Move → Add → Numbering → Extension.\n"
                      "Positions count from 1, negative from the end.\n"
                      "Space ticks / unticks the selected rows.", self, objectName="Dim")
        f.addWidget(hint, 0, 0)
        f.setRowStretch(1, 1)
        return box

    # ==================================================================
    # Rules <-> widgets
    # ==================================================================
    def read_rules(self) -> RenameRules:
        values = {}
        for attr, w in self.rule_widgets.items():
            if isinstance(w, QLineEdit):
                values[attr] = w.text()
            elif isinstance(w, QCheckBox):
                values[attr] = w.isChecked()
            elif isinstance(w, QSpinBox):
                values[attr] = w.value()
            elif isinstance(w, QComboBox):
                values[attr] = w.currentData()
        return RenameRules.from_dict(values)

    def write_rules(self, rules: RenameRules) -> None:
        data = rules.to_dict()
        for attr, w in self.rule_widgets.items():
            w.blockSignals(True)
            value = data.get(attr)
            if isinstance(w, QLineEdit):
                w.setText(str(value or ""))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QSpinBox):
                w.setValue(int(value or 0))
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(value)))
            w.blockSignals(False)
        self.schedule_preview()

    def reset_rules(self) -> None:
        self.write_rules(RenameRules())

    # ==================================================================
    # Session
    # ==================================================================
    def get_state(self) -> dict:
        return {"utility": self.KIND, "folder": self.folder, "files": self.files,
                "rules": self.read_rules().to_dict(), "show": self.show_combo.currentData(),
                "mask": self.mask_edit.text(), "recursive": self.sub_cb.isChecked(),
                "checked": [i.path for i in self.items if i.checked][:5000] if self._unticked else []}

    def restore(self, state: dict) -> None:
        self.files = list(state.get("files") or [])
        self.folder = None if self.files else (state.get("folder") or home())
        self._initial = set(state.get("checked") or [])
        for w in (self.show_combo, self.mask_edit, self.sub_cb):
            w.blockSignals(True)
        self.show_combo.setCurrentIndex(max(0, self.show_combo.findData(state.get("show", "all"))))
        self.mask_edit.setText(state.get("mask", ""))
        self.sub_cb.setChecked(bool(state.get("recursive")))
        for w in (self.show_combo, self.mask_edit, self.sub_cb):
            w.blockSignals(False)
        folder_mode = self.folder is not None
        for w in (self.show_label, self.show_combo, self.mask_label, self.mask_edit, self.sub_cb):
            w.setVisible(folder_mode)
        self.write_rules(RenameRules.from_dict(state.get("rules")))

    # ==================================================================
    # Loading the items (worker thread)
    # ==================================================================
    def reload(self) -> None:
        """Read the folder (or the given files) again, keeping the ticks."""
        if self._restoring or self._busy:
            return
        if self.items:
            self._unticked = {i.path for i in self.items if not i.checked}
        self._loading = True
        self.summary_label.setText("Reading…")
        folder, files = self.folder, list(self.files)
        show = self.show_combo.currentData()
        recursive = self.sub_cb.isChecked()
        vfs = self.ctx.vfs

        def work():
            items: List[RenameItem] = []
            taken: Dict[str, Set[str]] = {}
            if folder is not None:
                fs = vfs.resolve(folder)
                todo = [folder]
                while todo:
                    current = todo.pop(0)
                    try:
                        entries = list(fs.list_dir(current))
                    except OSError:
                        continue
                    taken[current] = {e.name for e in entries}
                    entries.sort(key=lambda e: (not e.is_dir, natural_key(e.name)))
                    for e in entries:
                        if (show == "files" and e.is_dir) or (show == "folders" and not e.is_dir):
                            pass
                        else:
                            items.append(RenameItem(e.path, e.name, current, e.is_dir))
                        if recursive and e.is_dir and not e.is_link:
                            todo.append(e.path)
            else:
                for path in files:
                    fs = vfs.resolve(path)
                    parent = fs.parent(path) or ""
                    if parent not in taken:
                        try:
                            taken[parent] = {e.name for e in fs.list_dir(parent)}
                        except OSError:
                            taken[parent] = set()
                    name = fs.basename(path)
                    if name in taken[parent]:
                        try:
                            is_dir = fs.stat(path).is_dir
                        except OSError:
                            is_dir = False
                        items.append(RenameItem(path, name, parent, is_dir))
            return items, taken

        self.ctx.runner.submit(work, pool="io", on_done=self._loaded,
                               on_error=lambda exc: self.summary_label.setText(f"Could not read: {exc}"))

    def _loaded(self, result) -> None:
        items, taken = result
        self._loading = False
        if self._initial:                            # opened with a selection: only those are ticked
            for item in items:
                item.checked = item.path in self._initial
            self._initial = None
        else:
            for item in items:
                item.checked = item.path not in self._unticked
        self.items = items
        self._taken = taken
        self._apply_mask(preview=False)
        self.model.show_folder = self.folder is None or self.sub_cb.isChecked()
        self.table.setColumnHidden(3, not self.model.show_folder)
        self._preview()

    def _apply_mask(self, preview: bool = True) -> None:
        """"Only" box: untick (and hide) items whose names don't match the patterns."""
        patterns = [p.strip() for p in self.mask_edit.text().replace(",", ";").split(";") if p.strip()]
        self._visible = [i for i in self.items
                         if not patterns or any(fnmatch.fnmatch(i.name.lower(), p.lower()) for p in patterns)]
        if preview:
            self._preview()

    # ==================================================================
    # Preview
    # ==================================================================
    def schedule_preview(self, *_) -> None:
        if not self._restoring:
            self._timer.start()

    def _preview(self) -> None:
        rules = self.read_rules()
        items = getattr(self, "_visible", self.items)
        taken = self._taken
        try:
            self.rows = plan(items, rules, exists=lambda parent, name: name in taken.get(parent, ()))
            self._rule_error = ""
        except Exception as exc:                 # noqa: BLE001 - a half-typed regex
            self._rule_error = f"Replace: not a valid regular expression ({exc})"
            self.rows = [PlanRow(i, i.name, "same") for i in items]
        self.model.set_rows(self.rows)
        self._update_summary()
        self.updated()

    def _update_summary(self) -> None:
        total = len(self.rows)
        renames = sum(1 for r in self.rows if r.status == "rename")
        errors = sum(1 for r in self.rows if r.status == "error")
        ticked = sum(1 for r in self.rows if r.status != "skip")
        if self._rule_error:
            text = self._rule_error
        elif not total:
            text = "Nothing to rename here" if not self._loading else "Reading…"
        else:
            text = f"{renames:,} of {ticked:,} ticked item{'s' if ticked != 1 else ''} will be renamed"
            if errors:
                text += f"  ·  {errors} can't be renamed (shown in red - point at one to see why) and will be left as they are"
        self.summary_label.setText(text)
        self.rename_btn.setText(f"Rename {renames:,} item{'s' if renames != 1 else ''}" if renames else "Rename")
        self.rename_btn.setEnabled(renames > 0 and not self._busy and not self._rule_error)
        where = self.folder if self.folder else f"{len(self.files):,} chosen items"
        self.header.set_subtitle(f"in {where}  ·  {total:,} items" if self.folder else where)

    # ==================================================================
    # Ticking
    # ==================================================================
    def tick(self, how: str) -> None:
        selected = {self.model.rows[i.row()].item.path for i in self.table.selectionModel().selectedRows()}
        for row in self.model.rows:
            if how == "all":
                row.item.checked = True
            elif how == "none":
                row.item.checked = False
            elif how == "invert":
                row.item.checked = not row.item.checked
            elif how == "selected":
                row.item.checked = row.item.path in selected
        self._preview()

    def _toggle_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        new_state = not self.model.rows[rows[0].row()].item.checked
        for index in rows:
            self.model.rows[index.row()].item.checked = new_state
        self._preview()

    # ==================================================================
    # Renaming
    # ==================================================================
    def apply(self) -> None:
        pairs = []
        for row in self.rows:
            if row.status == "rename":
                fs = self.ctx.vfs.resolve(row.item.path)
                pairs.append((row.item.path, fs.join(row.item.parent, row.new)))
        if pairs:
            self._run(pairs, undo=False)

    def undo(self) -> None:
        if self._undo:
            self._run(undo_pairs(self._undo), undo=True)

    def _run(self, pairs, undo: bool) -> None:
        self._busy = True
        self.rename_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self.summary_label.setText(("Undoing" if undo else "Renaming") + f" {len(pairs):,} items…")
        vfs = self.ctx.vfs
        self.ctx.runner.submit(lambda: rename_all(vfs, pairs), pool="ops",
                               on_done=lambda res: self._renamed(res, undo),
                               on_error=lambda exc: self._renamed(None, undo, exc))

    def _renamed(self, result, undo: bool, exc: Optional[BaseException] = None) -> None:
        self._busy = False
        if result is None:
            self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=f"Rename failed: {exc}", level="error")
            self.reload()
            return
        if undo:
            if self._history_id:
                self.ctx.history.mark_undone_pairs(self._history_id, undo_pairs(result.done))
            self._history_id = None
        elif result.done:
            dirs = {i.path for i in self.items if i.is_dir}
            rec = self.ctx.history.record("rename", [{"src": old, "dst": new, "dir": old in dirs}
                                                     for old, new in result.done], source="Rename tab")
            self._history_id = rec.id if rec else None
        self._undo = [] if undo else list(result.done)
        moved = dict(result.done)
        if self.files:                                         # list mode: follow the files to their new names
            self.files = [moved.get(p, p) for p in self.files]
        self._unticked = {moved.get(i.path, i.path) for i in self.items if not i.checked}
        self.items = []                                        # (so reload() keeps the mapped ticks above)
        parents = sorted({os.path.dirname(old) for old, _ in result.done} |
                         {self.ctx.vfs.resolve(old).parent(old) or "" for old, _ in result.done})
        self.ctx.bus.publish(Topics.FS_CHANGED, paths=[p for p in parents if p])
        for old, new in result.done:
            self.ctx.folder_colors.rename(old, new)
        done, failed = len(result.done), len(result.errors)
        verb = "Undone" if undo else "Renamed"
        text = f"{verb} {done:,} item{'s' if done != 1 else ''}"
        if failed:
            text += f" - {failed} failed: " + "; ".join(f"{os.path.basename(p)}: {m}" for p, m in result.errors[:3])
        self.ctx.bus.publish(Topics.STATUS_MESSAGE, text=text, level="error" if failed else "info")
        self.undo_btn.setEnabled(bool(self._undo))
        self.undo_btn.setToolTip(f"Rename the {len(self._undo):,} items back" if self._undo else "")
        self.reload()

    def _history_changed(self) -> None:
        """Undone from the History tab (or history cleared): our Undo button no longer applies."""
        if self._undo and not self._busy:
            rec = self.ctx.history.get(self._history_id) if self._history_id else None
            if rec is None or rec.partly_undone or rec.undone:
                self._undo = []
                self._history_id = None
                self.undo_btn.setEnabled(False)
                self.undo_btn.setToolTip("")

    # ==================================================================
    # Tab protocol
    # ==================================================================
    @property
    def title(self) -> str:
        if self.folder:
            name = os.path.basename(self.folder.rstrip("/\\")) or self.folder
            return f"Rename: {name}"
        return f"Rename: {len(self.files)} items"

    @property
    def path(self) -> str:
        return self.folder or (os.path.dirname(self.files[0]) if self.files else home())

    def watched_folders(self) -> List[str]:
        return [self.folder] if self.folder else []

    def on_folder_changed(self, folder: str) -> None:
        if not self._busy and (folder == self.folder or (self.folder is None and folder in self._taken)):
            self.reload()

    def refresh(self, soft: bool = True) -> None:
        self.reload()

    def summary(self) -> str:
        return self.summary_label.text()

    def focus_view(self) -> None:
        self.table.setFocus()

    def selected_entries(self):
        return []                                  # the table is not a file view (no copy/delete from here)

    def shutdown(self) -> None:
        self._timer.stop()
        super().shutdown()

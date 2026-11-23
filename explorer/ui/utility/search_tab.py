"""
SearchTab - the Search utility (Ctrl+F).

    ┌ Search ─────────────────────────────── in Downloads · index: 1.2M items, 3 min old  [Update] ┐
    │ Look in   [Downloads ×] [Music ×]  [+ Folder…] [+ Drive ▾]        [ ] Whole computer       │
    │ Name      [holiday ext:jpg_________________________________]  Aa  ?                        │
    │ Contains  [______________________________________________]  Aa  .*  W                      │
    │ ▸ Filters: type, extensions, size, date modified, hidden files, excluded folders           │
    ├─────────────────────────────────────────────────────────────────────────────────────────────┤
    │ results: the normal file views (Details / List / Icons / Tree) - open, drag, copy, delete,  │
    │ Open With, right-click... all work on search results                                        │
    └ 1,234 results in 0.03 s                                                                     ┘

Names (and every filter) are searched as you type, from the index - see
services/search.py. "Contains" reads files, so it runs when you press Enter
(or the Search button).

This module only builds widgets and turns them into a SearchSpec; the
searching is services/search.py, the result list is the same model + views
the folder tabs use.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from operator import attrgetter
from typing import List, Optional

from PySide6.QtCore import QDate, QDateTime, QEvent, QObject, Qt, QTime, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ...core.events import Topics
from ...core.fileinfo import FileEntry
from ...core.folder_model import FolderModel, filter_entries
from ...core.formatting import human_time
from ...core.platform_info import home
from ...core.query import QUERY_HELP, QueryError
from ...core.search_spec import DAY, SIZE_UNITS, SearchSpec, parse_extensions, parse_names
from ...core.sorting import key_func
from ...services.file_index import is_inside
from ..models.file_model import FileModel
from ..views.registry import view_class
from .base import UtilityTab

LIVE_DELAY_MS = 150                      # pause in typing before a name search runs
DATE_CHOICES = [("any", "Any time"), ("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"),
                ("365d", "Last year"), ("after", "After…"), ("before", "Before…"), ("between", "Between…")]
SIZE_CHOICES = [("any", "Any size"), ("min", "At least"), ("max", "At most"), ("between", "Between")]
KIND_CHOICES = [("all", "Files and folders"), ("files", "Files only"), ("folders", "Folders only")]


class _Keys(QObject):
    """Enter / Down / Escape in the text fields."""

    def __init__(self, tab: "SearchTab") -> None:
        super().__init__(tab)
        self.tab = tab

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.tab.focus_view()
                return True
            if key == Qt.Key.Key_Escape and isinstance(obj, QLineEdit) and obj.text():
                obj.clear()
                return True
        return False


class LocationChip(QFrame):
    """One "Look in" folder as a single pill: [ Downloads × ] (the × removes it)."""

    def __init__(self, label: str, path: str, on_remove, parent=None) -> None:
        super().__init__(parent, objectName="LocationChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setToolTip(path)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 1, 3, 1)
        row.setSpacing(2)
        self.label = QLabel(label, self, objectName="ChipLabel")
        close = QToolButton(self, text="×", toolTip="Don't search here", objectName="ChipClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(lambda: on_remove(path))
        row.addWidget(self.label)
        row.addWidget(close)


class SearchTab(UtilityTab):
    KIND = "search"
    TITLE = "Search"
    SHAPE = "search"
    search_mode = True                   # results come from many folders: show "Location"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        s = ctx.settings
        self.spec = SearchSpec()
        self.listing = FolderModel(s.get("view.sort_key"), s.get("view.sort_reverse"),
                                   s.get("view.folders_first"), True)
        self.qmodel = FileModel(ctx)
        self.qmodel.loader = self.load_children
        self.qmodel.prepare = self.prepare_children
        self.qmodel.rename_handler = lambda entry, name: ctx.commands.rename_to(self, entry.path, name)
        self.view_mode: Optional[str] = None
        self._token = None
        self._running = False
        self._incoming: List[FileEntry] = []
        self._flush_pending = False
        self._stats = None
        self._progress = ""
        self._error = ""
        self._select_after_load: Optional[List[str]] = None
        self._keep: Optional[dict] = None
        self._restoring = True
        super().__init__(panel, ctx, state)
        self._timer = QTimer(self, singleShot=True, interval=LIVE_DELAY_MS, timeout=self._live_search)
        bus = ctx.bus
        self._unsubs += [
            bus.subscribe(Topics.VIEW_MODE_CHANGED, lambda mode: self.ensure_view()),
            bus.subscribe(Topics.ZOOM_CHANGED, self._on_zoom),
            bus.subscribe(Topics.SORT_CHANGED, self._on_sort),
            bus.subscribe(Topics.SETTINGS_CHANGED, self._on_setting),
            bus.subscribe(Topics.INDEX_UPDATED, self._on_index_updated),
            bus.subscribe(Topics.FOLDER_COLORS_CHANGED, lambda **_: self._refresh_look()),
            bus.subscribe(Topics.CLIPBOARD_CHANGED, lambda **_: self._refresh_look()),
        ]
        self._restoring = False
        self.ensure_view()
        self._update_header()
        if not self.spec.is_empty:
            QTimer.singleShot(0, self.run)

    # ==================================================================
    # Widgets
    # ==================================================================
    def build(self, body: QVBoxLayout) -> None:
        keys = _Keys(self)
        self.update_btn = QPushButton("Update index", self, toolTip="Read the disk again now (the index also "
                                      "updates by itself in the background)")
        self.update_btn.clicked.connect(self._update_index)
        self.header.buttons.addWidget(self.update_btn)

        panel = QFrame(self, objectName="UtilityPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        # -- Look in -------------------------------------------------------------
        grid.addWidget(self._section("Look in"), 0, 0)
        look = QHBoxLayout()
        look.setSpacing(4)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(4)
        look.addLayout(self.chips)
        self.add_folder_btn = QToolButton(self, text="+ Folder…", autoRaise=True, toolTip="Add a folder to search in")
        self.add_folder_btn.clicked.connect(self._choose_folder)
        self.add_drive_btn = QToolButton(self, text="+ Drive", autoRaise=True, toolTip="Add a drive, share or cloud folder",
                                         popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.drive_menu = QMenu(self)
        self.drive_menu.aboutToShow.connect(self._fill_drive_menu)
        self.add_drive_btn.setMenu(self.drive_menu)
        look.addWidget(self.add_folder_btn)
        look.addWidget(self.add_drive_btn)
        look.addStretch(1)
        self.whole_cb = QCheckBox("Whole computer", self, toolTip="Every local drive (network drives too if "
                                  "allowed in Preferences → Search)")
        self.whole_cb.toggled.connect(self._on_whole_toggled)
        look.addWidget(self.whole_cb)
        grid.addLayout(look, 0, 1, 1, 4)

        # -- Name ------------------------------------------------------------------
        grid.addWidget(self._section("Name"), 1, 0)
        self.name_edit = QLineEdit(self, clearButtonEnabled=True)
        self.name_edit.setPlaceholderText("Part of the name - also *.jpg, ext:pdf, !word, size:>10mb, dm:7d…  "
                                          "(? for help)")
        self.name_edit.textChanged.connect(self._schedule)
        self.name_edit.returnPressed.connect(self.run)
        self.name_edit.installEventFilter(keys)
        grid.addWidget(self.name_edit, 1, 1, 1, 3)
        name_opts = QHBoxLayout()
        self.case_btn = self._toggle("Aa", "Match case in names")
        self.case_btn.toggled.connect(self._schedule)
        help_btn = QToolButton(self, text="?", autoRaise=True, toolTip="Name search syntax")
        help_btn.clicked.connect(lambda: self._show_help(help_btn))
        name_opts.addWidget(self.case_btn)
        name_opts.addWidget(help_btn)
        name_opts.addStretch(1)
        grid.addLayout(name_opts, 1, 4)

        # -- Contains -------------------------------------------------------------------
        grid.addWidget(self._section("Contains"), 2, 0)
        self.content_edit = QLineEdit(self, clearButtonEnabled=True)
        self.content_edit.setPlaceholderText("Text inside the files - press Enter to search")
        self.content_edit.returnPressed.connect(self.run)
        self.content_edit.textChanged.connect(self._on_content_typed)
        self.content_edit.installEventFilter(keys)
        grid.addWidget(self.content_edit, 2, 1, 1, 3)
        content_opts = QHBoxLayout()
        self.ccase_btn = self._toggle("Aa", "Match case in the text")
        self.regex_btn = self._toggle(".*", "Regular expression")
        self.word_btn = self._toggle("W", "Whole words only")
        for b in (self.ccase_btn, self.regex_btn, self.word_btn):
            b.toggled.connect(lambda _=False: self._schedule() if self.content_edit.text() else None)
            content_opts.addWidget(b)
        self.search_btn = QPushButton("Search", self)
        self.search_btn.setDefault(False)
        self.search_btn.clicked.connect(self._search_or_stop)
        content_opts.addWidget(self.search_btn)
        grid.addLayout(content_opts, 2, 4)

        # -- Filters (fold away) -----------------------------------------------------------
        # They live on the same grid as Look in / Name / Contains, so every label lines up:
        #   col 0 label | col 1 field | col 2 label | col 3 field | col 4 buttons
        self.filters_btn = QToolButton(self, text="▸ Filters", autoRaise=True, checkable=True,
                                       toolTip="Type, extensions, size, date, hidden files, excluded folders")
        self.filters_btn.toggled.connect(self._show_filters)
        grid.addWidget(self.filters_btn, 3, 0)
        self.filter_summary = QLabel("", self, objectName="Dim")
        grid.addWidget(self.filter_summary, 3, 1, 1, 4)

        self.kind_combo = self._combo(KIND_CHOICES)
        self.ext_edit = QLineEdit(self, placeholderText="e.g. jpg, png, pdf", clearButtonEnabled=True)
        self.ext_edit.textChanged.connect(self._schedule)
        self.exclude_edit = QLineEdit(self, placeholderText="folders to skip, e.g. node_modules, .git",
                                      clearButtonEnabled=True)
        self.exclude_edit.textChanged.connect(self._schedule)

        self.size_combo = self._combo(SIZE_CHOICES)
        self.size_a, self.size_a_unit = self._size_inputs()
        self.size_and = QLabel("and")
        self.size_b, self.size_b_unit = self._size_inputs()
        self.date_combo = self._combo(DATE_CHOICES)
        self.date_a = self._date_input(-30)
        self.date_and = QLabel("and")
        self.date_b = self._date_input(0)
        self.hidden_cb = QCheckBox("Include hidden files", self)
        self.hidden_cb.toggled.connect(self._schedule)
        reset = QPushButton("Reset filters", self)
        reset.clicked.connect(self._reset_filters)

        def row_of(*widgets) -> QWidget:
            box = QWidget(self)
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            for w in widgets:
                h.addWidget(w)
            h.addStretch(1)
            return box

        right_label = lambda text: self._section(text, indent=True)   # noqa: E731
        self._filter_widgets = [
            (self._section("Type"), 4, 0), (row_of(self.kind_combo), 4, 1),
            (right_label("Size"), 4, 2),
            (row_of(self.size_combo, self.size_a, self.size_a_unit, self.size_and, self.size_b, self.size_b_unit), 4, 3),
            (self._section("Extensions"), 5, 0), (self.ext_edit, 5, 1),
            (right_label("Modified"), 5, 2), (row_of(self.date_combo, self.date_a, self.date_and, self.date_b), 5, 3),
            (self._section("Exclude"), 6, 0), (self.exclude_edit, 6, 1),
            (row_of(self.hidden_cb), 6, 2, 1, 2), (reset, 6, 4),
        ]
        for widget, *where in self._filter_widgets:
            grid.addWidget(widget, *where)
            widget.hide()
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 3)
        body.addWidget(panel)
        self._update_filter_inputs()

        # -- results ---------------------------------------------------------------------
        self.view_slot = QVBoxLayout()
        self.view_slot.setContentsMargins(0, 0, 0, 0)
        body.addLayout(self.view_slot, 1)
        footer = QFrame(self, objectName="UtilityFooter")
        frow = QHBoxLayout(footer)
        frow.setContentsMargins(12, 3, 12, 3)
        self.footer_label = QLabel("", footer, objectName="Dim")
        frow.addWidget(self.footer_label, 1)
        body.addWidget(footer)

    def _section(self, text: str, indent: bool = False) -> QLabel:
        label = QLabel(text, self, objectName="UtilitySection")
        if indent:                                   # a label in the middle of a row: some air before it
            label.setContentsMargins(14, 0, 0, 0)
        return label

    def _toggle(self, text: str, tip: str) -> QToolButton:
        b = QToolButton(self, text=text, toolTip=tip, checkable=True, autoRaise=True)
        b.setMinimumWidth(28)
        return b

    def _combo(self, choices) -> QComboBox:
        c = QComboBox(self)
        for value, label in choices:
            c.addItem(label, value)
        c.currentIndexChanged.connect(self._update_filter_inputs)
        c.currentIndexChanged.connect(self._schedule)
        return c

    def _size_inputs(self):
        spin = QDoubleSpinBox(self, decimals=1, maximum=1_000_000, value=1.0)
        spin.setMinimumWidth(90)
        spin.valueChanged.connect(self._schedule)
        unit = QComboBox(self)
        for label, factor in SIZE_UNITS:
            unit.addItem(label, factor)
        unit.setCurrentIndex(2)                                  # MB
        unit.currentIndexChanged.connect(self._schedule)
        return spin, unit

    def _date_input(self, days_from_today: int) -> QDateEdit:
        d = QDateEdit(QDate.currentDate().addDays(days_from_today), self, calendarPopup=True)
        d.setDisplayFormat("yyyy-MM-dd")
        d.dateChanged.connect(self._schedule)
        return d

    def _show_help(self, button: QToolButton) -> None:
        """The name syntax, as a menu; picking a line adds that example."""
        menu = QMenu(self)
        title = menu.addAction("Name search - combine any of these:")
        title.setEnabled(False)
        menu.addSeparator()
        for example, meaning in QUERY_HELP:
            act = menu.addAction(f"{example}\t{meaning}")
            act.triggered.connect(lambda _=False, ex=example.split()[0]: self._insert_example(ex))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _insert_example(self, example: str) -> None:
        text = self.name_edit.text()
        self.name_edit.setText((text + " " if text else "") + example)
        self.name_edit.setFocus()

    # ==================================================================
    # Widgets <-> SearchSpec
    # ==================================================================
    def read_spec(self) -> SearchSpec:
        """What the widgets say, as a SearchSpec."""
        spec = SearchSpec(locations=list(self.spec.locations), whole_computer=self.whole_cb.isChecked())
        spec.name = self.name_edit.text().strip()
        spec.case_sensitive = self.case_btn.isChecked()
        spec.content = self.content_edit.text()
        spec.content_case = self.ccase_btn.isChecked()
        spec.content_regex = self.regex_btn.isChecked()
        spec.content_whole_word = self.word_btn.isChecked()
        spec.kind = self.kind_combo.currentData()
        spec.extensions = parse_extensions(self.ext_edit.text())
        size_mode = self.size_combo.currentData()
        a = int(self.size_a.value() * self.size_a_unit.currentData())
        b = int(self.size_b.value() * self.size_b_unit.currentData())
        if size_mode == "min":
            spec.size_min = a
        elif size_mode == "max":
            spec.size_max = a
        elif size_mode == "between":
            spec.size_min, spec.size_max = min(a, b), max(a, b)
        date_mode = self.date_combo.currentData()
        start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_a = _day_start(self.date_a.date())
        day_b = _day_start(self.date_b.date())
        if date_mode == "today":
            spec.modified_after = start_of_day
        elif date_mode in ("7d", "30d", "365d"):
            spec.modified_after = time.time() - int(date_mode[:-1]) * DAY
        elif date_mode == "after":
            spec.modified_after = day_a
        elif date_mode == "before":
            spec.modified_before = day_a
        elif date_mode == "between":
            spec.modified_after, spec.modified_before = min(day_a, day_b), max(day_a, day_b) + DAY - 1
        spec.include_hidden = self.hidden_cb.isChecked()
        spec.exclude = parse_names(self.exclude_edit.text())
        spec.max_results = int(self.ctx.settings.get("search.max_results", 250_000) or 250_000)
        return spec

    def get_state(self) -> dict:
        spec = self.read_spec()
        return {"utility": self.KIND, "spec": spec.to_dict(), "filters_open": self.filters_btn.isChecked(),
                "widgets": {"size_mode": self.size_combo.currentData(), "size_a": self.size_a.value(),
                            "size_a_unit": self.size_a_unit.currentIndex(), "size_b": self.size_b.value(),
                            "size_b_unit": self.size_b_unit.currentIndex(),
                            "date_mode": self.date_combo.currentData(),
                            "date_a": self.date_a.date().toString(Qt.DateFormat.ISODate),
                            "date_b": self.date_b.date().toString(Qt.DateFormat.ISODate)},
                "view_state": self.view.get_state() if self.view is not None else None}

    def restore(self, state: dict) -> None:
        spec = SearchSpec.from_dict(state.get("spec"))
        self.spec = spec
        widgets = state.get("widgets") or {}
        for w in (self.name_edit, self.content_edit, self.ext_edit, self.exclude_edit, self.whole_cb,
                  self.case_btn, self.ccase_btn, self.regex_btn, self.word_btn, self.hidden_cb, self.kind_combo,
                  self.size_combo, self.date_combo, self.size_a, self.size_b, self.size_a_unit, self.size_b_unit,
                  self.date_a, self.date_b):
            w.blockSignals(True)
        self.name_edit.setText(spec.name)
        self.case_btn.setChecked(spec.case_sensitive)
        self.content_edit.setText(spec.content)
        self.ccase_btn.setChecked(spec.content_case)
        self.regex_btn.setChecked(spec.content_regex)
        self.word_btn.setChecked(spec.content_whole_word)
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(spec.kind)))
        self.ext_edit.setText(", ".join(spec.extensions))
        self.hidden_cb.setChecked(spec.include_hidden)
        self.exclude_edit.setText(", ".join(spec.exclude))
        self.whole_cb.setChecked(spec.whole_computer)
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(widgets.get("size_mode", "any"))))
        self.size_a.setValue(float(widgets.get("size_a", 1.0)))
        self.size_b.setValue(float(widgets.get("size_b", 1.0)))
        self.size_a_unit.setCurrentIndex(int(widgets.get("size_a_unit", 2)))
        self.size_b_unit.setCurrentIndex(int(widgets.get("size_b_unit", 2)))
        self.date_combo.setCurrentIndex(max(0, self.date_combo.findData(widgets.get("date_mode", "any"))))
        for key, edit in (("date_a", self.date_a), ("date_b", self.date_b)):
            if widgets.get(key):
                edit.setDate(QDate.fromString(widgets[key], Qt.DateFormat.ISODate))
        for w in (self.name_edit, self.content_edit, self.ext_edit, self.exclude_edit, self.whole_cb,
                  self.case_btn, self.ccase_btn, self.regex_btn, self.word_btn, self.hidden_cb, self.kind_combo,
                  self.size_combo, self.date_combo, self.size_a, self.size_b, self.size_a_unit, self.size_b_unit,
                  self.date_a, self.date_b):
            w.blockSignals(False)
        self.filters_btn.setChecked(bool(state.get("filters_open")) or self._filters_active(spec))
        self._view_state = state.get("view_state") or {}
        self._rebuild_chips()
        self._update_filter_inputs()

    # ==================================================================
    # Locations
    # ==================================================================
    def add_location(self, path: str) -> None:
        if path and path not in self.spec.locations:
            self.spec.locations.append(path)
            self.whole_cb.setChecked(False)
            self._rebuild_chips()
            self._schedule()

    def remove_location(self, path: str) -> None:
        if path in self.spec.locations:
            self.spec.locations.remove(path)
            if not self.spec.locations:
                self.whole_cb.setChecked(True)
            self._rebuild_chips()
            self._schedule()

    def _rebuild_chips(self) -> None:
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        whole = self.whole_cb.isChecked()
        for path in self.spec.locations:
            chip = LocationChip(self._place_name(path), path, self.remove_location, self)
            chip.setEnabled(not whole)
            self.chips.addWidget(chip)
        self._update_header()

    def _place_name(self, path: str) -> str:
        try:
            return self.ctx.vfs.resolve(path).basename(path) or path
        except OSError:
            return os.path.basename(path.rstrip("/\\")) or path

    def _choose_folder(self) -> None:
        start = self.spec.locations[0] if self.spec.locations and "://" not in self.spec.locations[0] else home()
        folder = QFileDialog.getExistingDirectory(self, "Search in folder", start)
        if folder:
            self.add_location(folder)

    def _fill_drive_menu(self) -> None:
        self.drive_menu.clear()
        devices = self.ctx.devices.devices if self.ctx.devices else []
        icons = self.ctx.icons
        for section, title in (("drives", "Drives"), ("network", "Network"), ("cloud", "Cloud")):
            items = [d for d in devices if d.section == section and d.mounted]
            if not items:
                continue
            self.drive_menu.addSection(title)
            for d in items:
                icon = icons.cloud_app(d.cloud_root) if section == "cloud" else icons.special(d.kind)
                self.drive_menu.addAction(icon, d.label).triggered.connect(lambda _=False, p=d.path: self.add_location(p))
        conns = self.ctx.connections.all()
        if conns:
            self.drive_menu.addSection("Saved connections")
            for c in conns:
                uri = self.ctx.connections.root_uri(c)
                self.drive_menu.addAction(icons.special("network"), c.get("name") or uri).triggered.connect(
                    lambda _=False, p=uri: self.add_location(p))

    def _on_whole_toggled(self, checked: bool) -> None:
        if not checked and not self.spec.locations:
            self.spec.locations.append(home())
        self._rebuild_chips()
        self._schedule()

    # ==================================================================
    # Filters
    # ==================================================================
    def _show_filters(self, show: bool) -> None:
        for widget, *_ in self._filter_widgets:
            widget.setVisible(show)
        self.filters_btn.setText(("▾" if show else "▸") + " Filters")
        self._update_filter_inputs()

    def _filters_active(self, spec: SearchSpec) -> bool:
        return bool(spec.kind != "all" or spec.extensions or spec.size_min is not None or spec.size_max is not None
                    or spec.modified_after is not None or spec.modified_before is not None or spec.include_hidden
                    or spec.exclude)

    def _update_filter_inputs(self, *_) -> None:
        size_mode = self.size_combo.currentData()
        for w in (self.size_a, self.size_a_unit):
            w.setVisible(size_mode != "any")
        for w in (self.size_and, self.size_b, self.size_b_unit):
            w.setVisible(size_mode == "between")
        date_mode = self.date_combo.currentData()
        self.date_a.setVisible(date_mode in ("after", "before", "between"))
        for w in (self.date_and, self.date_b):
            w.setVisible(date_mode == "between")
        if self._restoring:
            return
        spec = self.read_spec()
        parts = []
        if spec.kind != "all":
            parts.append(self.kind_combo.currentText().lower())
        if spec.extensions:
            parts.append(", ".join("." + e for e in spec.extensions))
        if size_mode != "any":
            parts.append(f"size {self.size_combo.currentText().lower()} {self.size_a.value():g} "
                         f"{self.size_a_unit.currentText()}" + (f" and {self.size_b.value():g} "
                                                                f"{self.size_b_unit.currentText()}"
                                                                if size_mode == "between" else ""))
        if date_mode != "any":
            parts.append("modified " + self.date_combo.currentText().lower().rstrip("…"))
        if spec.include_hidden:
            parts.append("hidden files included")
        if spec.exclude:
            parts.append("skipping " + ", ".join(spec.exclude))
        self.filter_summary.setText("" if self.filters_btn.isChecked() else " · ".join(parts))

    def _reset_filters(self) -> None:
        self.kind_combo.setCurrentIndex(0)
        self.ext_edit.clear()
        self.size_combo.setCurrentIndex(0)
        self.date_combo.setCurrentIndex(0)
        self.hidden_cb.setChecked(False)
        self.exclude_edit.clear()

    # ==================================================================
    # Running searches
    # ==================================================================
    def focus_query(self) -> None:
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _on_content_typed(self, text: str) -> None:
        self._update_search_button()
        if not text and not self._running:
            self._schedule()                     # contents cleared: back to live name search

    def _schedule(self, *_) -> None:
        if self._restoring:
            return
        self._update_filter_inputs()
        self._update_header()
        if self.content_edit.text():
            self._set_footer("Press Enter to search inside the files")
            return                               # reading files waits for Enter / the Search button
        if self.ctx.settings.get("search.live", True):
            self._timer.start()

    def _live_search(self) -> None:
        if not self.content_edit.text():
            self.run()

    def _search_or_stop(self) -> None:
        if self._running:
            self.stop()
        else:
            self.run()

    def run(self, keep_selection: bool = False) -> None:
        """Start the search the widgets describe (cancelling one still running)."""
        self._timer.stop()
        spec = self.read_spec()
        self.spec.whole_computer = spec.whole_computer
        if spec.is_empty:
            self.stop()
            self.listing.reset()
            self._incoming = []
            self.qmodel.set_entries([])
            self._stats = None
            self._error = ""
            self._set_message("Type part of a name, or choose a filter")
            self._set_footer("")
            self.updated()
            return
        self._keep = None
        if keep_selection and self.view is not None:
            self._keep = {"selection": [e.path for e in self.view.selected_entries()], "top": self.view.top_path()}
        self.stop()
        self._running = True
        self._error = ""
        self._progress = "Searching…"
        self._incoming = []
        self._first = True
        self._stats = None
        self._update_columns(spec)
        if not keep_selection:
            self._set_message("Searching…")
        self._update_search_button()
        self._token = self.ctx.search.search(spec, self._on_batch, self._on_progress, self._on_done, self._on_error)
        self._update_footer()
        self.updated()

    def stop(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self._token = None
        if self._running:
            self._running = False
            self._progress = ""
            self._update_search_button()
            self._flush()
            self._update_footer()

    def _on_batch(self, batch: List[FileEntry]) -> None:
        if self._first:
            self._first = False
            self.listing.reset()
            if self._keep is None:
                self.qmodel.set_entries([])
            self._set_message("")
        self._incoming.extend(batch)
        if not self._flush_pending:
            self._flush_pending = True
            QTimer.singleShot(80 if self.listing.total < 20_000 else 400, self._flush)

    def _flush(self) -> None:
        self._flush_pending = False
        if self._incoming:
            batch, self._incoming = self._incoming, []
            self.listing.add(batch)
        if self._keep is not None and self._running:
            return                               # quiet re-run: swap the list in at the end, no jumping
        self._push()

    def _push(self) -> None:
        self.qmodel.set_entries(self.listing.visible)
        self._update_footer()
        self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=self)

    def _on_progress(self, text: str) -> None:
        self._progress = text
        self._update_footer()
        if self.listing.total == 0:
            self._set_message(text)

    def _on_done(self, stats) -> None:
        self._token = None
        self._running = False
        self._stats = stats
        self._progress = ""
        if self._first:                          # no results at all
            self.listing.reset()
        self._flush()
        self._push()
        if self._keep is not None and self.view is not None:
            self.view.select_paths(self._keep["selection"], scroll=False)
            if self._keep.get("top"):
                self.view.scroll_to_path(self._keep["top"])
            self._keep = None
        if self._select_after_load and self.view is not None:
            self.view.select_paths(self._select_after_load)
            self._select_after_load = None
        self._set_message("No results" if self.listing.total == 0 else "")
        self._update_search_button()
        self._update_footer()
        self._update_header()
        self.updated()

    def _on_error(self, exc: BaseException) -> None:
        self._token = None
        self._running = False
        self._progress = ""
        self._error = str(exc) if isinstance(exc, QueryError) else f"Search failed: {exc}"
        if "regex" in self._error.lower() or exc.__class__.__name__ == "error":
            self._error = f"That is not a valid regular expression: {exc}"
        self.listing.reset()
        self.qmodel.set_entries([])
        self._set_message(self._error)
        self._update_search_button()
        self._update_footer()
        self.updated()

    def _update_search_button(self) -> None:
        self.search_btn.setText("Stop" if self._running else "Search")

    # ==================================================================
    # Index status
    # ==================================================================
    def _update_header(self) -> None:
        spec = self.spec
        if self.whole_cb.isChecked():
            where = "on the whole computer"
        elif spec.locations:
            where = "in " + ", ".join(self._place_name(p) for p in spec.locations[:4]) + \
                ("…" if len(spec.locations) > 4 else "")
        else:
            where = ""
        manager = self.ctx.search.indexes
        roots = manager.computer_roots() if self.whole_cb.isChecked() else spec.locations
        indexes = [manager.index_for(r) for r in roots]
        if roots and all(indexes):
            count = sum({id(i): i.count for i in indexes}.values())
            oldest = min(i.built_at for i in indexes)
            note = f"index: {count:,} items, updated {_ago(oldest)}"
        elif roots:
            note = "not indexed yet - the first search reads the disk"
        else:
            note = ""
        self.header.set_subtitle(" · ".join(p for p in (where, note) if p))

    def _update_index(self) -> None:
        manager = self.ctx.search.indexes
        roots = manager.computer_roots() if self.whole_cb.isChecked() else \
            [manager.index_for(p).root if manager.index_for(p) else p for p in self.spec.locations]
        for root in dict.fromkeys(roots):
            manager.refresh_in_background(root, self.ctx.runner)
        self._progress = "Updating the index in the background…"
        self._update_footer()

    def _on_index_updated(self, root: str) -> None:
        """A newer index is ready: re-run quietly if it covers what we search."""
        roots = self.ctx.search.indexes.computer_roots() if self.whole_cb.isChecked() else self.spec.locations
        if any(is_inside(r, root) or is_inside(root, r) for r in roots):
            self._update_header()
            if not self._running and self._stats is not None and not self.content_edit.text():
                self.run(keep_selection=True)
            elif self._progress.startswith("Updating"):
                self._progress = ""
                self._update_footer()

    # ==================================================================
    # Footer / status bar
    # ==================================================================
    def _update_footer(self) -> None:
        self._set_footer(self.summary())

    def _set_footer(self, text: str) -> None:
        self.footer_label.setText(text)

    def summary(self) -> str:
        if self._error:
            return self._error
        count = self.listing.total + len(self._incoming)
        parts = []
        if self._running:
            parts.append(self._progress or "Searching…")
            if count:
                parts.append(f"{count:,} found so far")
        elif self._stats is not None:
            s = self._stats
            took = f"{max(1, round(s.seconds * 1000))} ms" if s.seconds < 1 else f"{s.seconds:.1f} s"
            parts.append(f"{s.matches:,} result{'s' if s.matches != 1 else ''} in {took}")
            if s.truncated:
                parts.append("showing the first results only - narrow the search")
            if s.content_checked:
                parts.append(f"{s.content_checked:,} files read")
            if s.built_now:
                parts.append(f"{s.indexed:,} items indexed")
        elif self._progress:
            parts.append(self._progress)
        sel = self.selected_entries()
        if sel:
            parts.append(f"{len(sel):,} selected")
        return "  ·  ".join(parts)

    @property
    def title(self) -> str:
        text = self.name_edit.text().strip() if hasattr(self, "name_edit") else ""
        content = self.content_edit.text().strip() if hasattr(self, "content_edit") else ""
        label = text or (f'"{content}"' if content else "")
        return f"Search: {label}" if label else "Search"

    @property
    def path(self) -> str:
        return self.spec.locations[0] if self.spec.locations else home()

    # ==================================================================
    # Results view (the same views and model as folder tabs)
    # ==================================================================
    def ensure_view(self) -> None:
        mode = self.ctx.settings.get("view.mode", "details")
        if self.view is not None and self.view_mode == mode:
            return
        selected, state = [], self._view_state
        old = self.view
        if old is not None:
            selected = [e.path for e in old.selected_entries()]
            state = old.get_state() or state
            old.detach_model()
            self.view_slot.removeWidget(old)
            old.hide()
            old.deleteLater()
        zoom = self.ctx.settings.get(f"view.zoom.{mode}", 1)
        self.view = view_class(mode)(self.ctx, self, zoom, self)
        self.view_mode = mode
        self.view_slot.addWidget(self.view)
        self._update_columns(self.spec)
        self.view.attach_model(self.qmodel)
        if state:
            self.view.set_state(state)
        if selected:
            self.view.select_paths(selected, scroll=False)
        self._set_message(getattr(self, "_message", ""))

    def _update_columns(self, spec: SearchSpec) -> None:
        cols = ["name", "location"] + [c for c in self.ctx.settings.get("view.visible_columns",
                                                                        ["name", "size", "type", "modified"])
                                       if c in ("size", "type", "modified", "status")]
        if spec.content:
            cols.insert(2, "match")
        if cols != getattr(self.qmodel, "columns", None):
            self.qmodel.set_columns(cols)
        self._match_delegate_for(cols)

    def _match_delegate_for(self, cols: List[str]) -> None:
        """Details / Tree view: the "Matching line" column gets its own delegate."""
        view = self.view
        if view is None or not hasattr(view, "setItemDelegateForColumn"):
            return
        for k in range(len(cols) + 2):
            view.setItemDelegateForColumn(k, None)
        if "match" in cols:
            from ..views.base import MatchLineDelegate
            self._match_delegate = MatchLineDelegate(view)
            view.setItemDelegateForColumn(cols.index("match"), self._match_delegate)

    def _set_message(self, text: str) -> None:
        self._message = text
        if self.view is not None:
            self.view.set_message(text if self.qmodel.rowCount() == 0 else "")

    def _refresh_look(self) -> None:
        if self.view is not None:
            self.view.refresh_look()

    def _on_zoom(self, mode: str, level: int) -> None:
        if self.view is not None and self.view_mode == mode:
            self.view.set_zoom(level)

    def _on_sort(self, key: str, reverse: bool, folders_first: bool) -> None:
        self.listing.set_sort(key, reverse, folders_first)
        self._push()

    def _on_setting(self, key: str, value=None) -> None:
        if key == "view.visible_columns":
            self._update_columns(self.read_spec())
        elif key in ("view.thumbnails", "view.list_cell_width") and self.view is not None:
            self.view.apply_zoom()

    def _on_theme(self) -> None:
        super()._on_theme()
        self._refresh_look()

    # -- what the views ask their host (see ui/views/base.py) ----------------------------
    def on_view_activate(self, entries: List[FileEntry], new_tab: bool) -> None:
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        for k, d in enumerate(dirs[:10]):                # folders open in folder tabs next to this one
            self.panel.open_tab(d.path, activate=(k == 0 and not new_tab and not files),
                                index=self.panel.tab_widget.indexOf(self) + 1 + k)
        for f in files[:20]:
            self.ctx.commands.open_file(f.path)

    def on_view_context_menu(self, global_pos, entries: List[FileEntry]) -> None:
        self.ctx.commands.show_context_menu(self, global_pos, entries)

    def on_view_selection_changed(self) -> None:
        self._update_footer()
        self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=self)

    def on_view_zoom(self, delta: int) -> None:
        self.ctx.commands.zoom(delta)

    def on_view_type(self, text: str) -> None:
        """Typing in the results continues the name."""
        self.name_edit.setFocus()
        self.name_edit.setText(self.name_edit.text() + text)

    def on_view_focus(self) -> None:
        self.panel.set_active_panel()

    def on_view_sort(self, column: str) -> None:
        if column in ("name", "size", "type", "modified"):
            self.ctx.commands.set_sort(column)

    def on_view_drop(self, paths: List[str], dest: str, op: str) -> None:
        self.ctx.commands.drop_files(paths, dest, op)

    def load_children(self, path: str, callback) -> None:
        self.ctx.scanner.list_all(path, callback, lambda exc: callback([]))

    def prepare_children(self, entries: List[FileEntry]) -> List[FileEntry]:
        m = self.listing
        keyf = key_func(m.sort_key)
        for e in entries:
            e.sk = keyf(e)
        sk = attrgetter("sk")
        dirs = sorted((e for e in entries if e.is_dir), key=sk, reverse=m.reverse)
        files = sorted((e for e in entries if not e.is_dir), key=sk, reverse=m.reverse)
        return filter_entries(dirs, files, None, self.spec.include_hidden, m.folders_first, m.reverse)

    # -- after file operations ------------------------------------------------------------------
    def on_folder_changed(self, folder: str) -> None:
        """Files were renamed/deleted: the index is patched in a moment and we
        re-run then (INDEX_UPDATED). Deleted items vanish from the list now."""
        gone = [e.path for e in self.listing.all_entries if e.parent == folder and not os.path.lexists(e.path)
                and "://" not in e.path]
        if gone:
            self.listing.remove_paths(gone)
            self._push()

    def refresh(self, soft: bool = True) -> None:
        self.run(keep_selection=True)

    def shutdown(self) -> None:
        self.stop()
        self._timer.stop()
        super().shutdown()
        if self.view is not None:
            self.view.detach_model()


def _day_start(qdate: QDate) -> float:
    return QDateTime(qdate, QTime(0, 0)).toSecsSinceEpoch()


def _ago(stamp: float) -> str:
    seconds = max(0, time.time() - stamp)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return human_time(stamp)


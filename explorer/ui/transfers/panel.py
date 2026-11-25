"""
TransferPanel - the contents of the transfer window / Transfers tab. One
widget, moved between the two by ui/transfers/controller.py.

    ┌─────────────────────────────────────────────── [Open as tab] [Clear finished] ┐
    │   Transfer                          Files    Size     Speed     Started  Result  │
    │ ↻ Copying photos → Backup           1,204    5.6 GB   112 MB/s  14:32    62 %    │
    │ ✓ Moved report.pdf → Documents          1    2.1 MB   -         14:30    Done    │
    │ ✗ Copied 3 items → USB                 40    1.2 GB   35 MB/s   13:58    2 failed│
    ├──────────────────────────────────────────────────────────────────────────────────┤
    │ Copying 1,204 files (5.6 GB) from Pictures to Backup                             │
    │ [██████████████████████░░░░░░░░░░░░░░]  3.5 GB of 5.6 GB · 62 %                  │
    │ Now: 2024/IMG_0042.JPG   Speed 112 MB/s (average 98 MB/s)   12 s left   ✓ 740 ✗ 2  │
    │ ╱╲╱‾‾╲__╱‾‾‾‾  speed plot                                                        │
    │ [Pause] [Cancel] [Open destination]          Show [All ▾] [✓] Follow  [✓] Files   │
    │ ✓ │ 2024/IMG_0040.JPG │ 4.2 MB │ Verified                                          │
    │ ✗ │ 2024/IMG_0041.JPG │ 3.9 MB │ Failed: Permission denied                         │

It only READS the transfers (core/transfer_model.py) and calls the engine's
pause/resume/cancel; ``refresh()`` is called on a timer by the controller.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QProgressBar, QPushButton, QSplitter, QTableView, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ...core import transfer_model as tm
from ...core.formatting import human_size, human_time
from .files_model import FILTERS, TransferFilesModel
from .speed_plot import SpeedPlot

ID_ROLE = Qt.ItemDataRole.UserRole


def _short(path: str) -> str:
    trimmed = path.rstrip("/\\")
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    return trimmed[cut + 1:] or path if cut >= 0 else trimmed


def _parent(path: str) -> str:
    trimmed = path.rstrip("/\\")
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    return (trimmed[:cut] or trimmed[:cut + 1]) if cut >= 0 else ""


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def title_of(t: tm.Transfer) -> str:
    verb = {"copy": ("Copying", "Copied"), "move": ("Moving", "Moved")}[t.kind][0 if t.active else 1]
    what = _short(t.sources[0]) if len(t.sources) == 1 else f"{len(t.sources)} items"
    return f"{verb} {what} → {_short(t.dest)}"


def result_of(t: tm.Transfer) -> tuple:
    """(mark, text, palette colour) for the list's Result column."""
    if t.state == tm.PREPARING:
        return "↻", "Preparing…", "accent"
    if t.state == tm.PAUSED:
        return "⏸", f"Paused {t.progress * 100:.0f} %", "ansi_yellow"
    if t.state == tm.RUNNING:
        return "↻", f"{t.progress * 100:.0f} %", "accent"
    failed = t.failed
    if t.state == tm.NO_SPACE:
        return "✗", "Drive full", "error"
    if t.state == tm.CANCELLED:
        return "–", "Cancelled", "ansi_yellow"
    if t.state == tm.ERROR:
        return "✗", "Error", "error"
    if failed:
        return "✗", f"{failed:,} failed", "error"
    return "✓", "Verified" if t.verify else "Done", "ok"


class TransferPanel(QWidget):
    mode_requested = Signal()                     # the "Open as tab / in a window" button

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._items: Dict[str, QTreeWidgetItem] = {}
        self.selected: Optional[tm.Transfer] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame(self, objectName="UtilityPanel")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 8, 6)
        self.summary_label = QLabel("", self, objectName="Dim")
        row.addWidget(self.summary_label, 1)
        self.mode_btn = QPushButton("Open as tab", self)
        self.mode_btn.clicked.connect(self.mode_requested.emit)
        self.clear_btn = QPushButton("Clear finished", self)
        self.clear_btn.setToolTip("Forget finished transfers (running ones stay). Your files are not touched.")
        self.clear_btn.clicked.connect(self._clear)
        row.addWidget(self.mode_btn)
        row.addWidget(self.clear_btn)
        outer.addWidget(bar)

        split = QSplitter(Qt.Orientation.Vertical, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)                       # a 1px line like everywhere else (Qt keeps a 5px grab area)
        outer.addWidget(split, 1)

        # -- the list of transfers ------------------------------------------------
        self.list = QTreeWidget(self)
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setAlternatingRowColors(True)
        self.list.setHeaderLabels(["", "Transfer", "Files", "Size", "Average speed", "Started", "Result"])
        h = self.list.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 28), (2, 70), (3, 80), (4, 110), (5, 120), (6, 110)):
            self.list.setColumnWidth(col, width)
        for col in (2, 3, 4):                         # numbers: right-aligned, titles too
            self.list.headerItem().setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.list.currentItemChanged.connect(lambda cur, _: self._select_item(cur))
        split.addWidget(self.list)

        # -- details of the selected transfer -------------------------------------------
        details = QWidget(self)
        d = QVBoxLayout(details)
        d.setContentsMargins(12, 8, 12, 8)
        d.setSpacing(6)
        self.title_label = QLabel("", self)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.1)
        self.title_label.setFont(font)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        d.addWidget(self.title_label)
        prow = QHBoxLayout()
        self.progress = QProgressBar(self, minimum=0, maximum=1000, textVisible=False)
        self.progress.setFixedHeight(10)
        self.progress_label = QLabel("", self)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.progress_label)
        d.addLayout(prow)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self.stats: Dict[str, QLabel] = {}
        for n, (key, label) in enumerate((("now", "Now"), ("speed", "Speed"), ("time", "Time"),
                                          ("files", "Files"), ("verify", "Verify"), ("message", ""))):
            cap = QLabel(label, self, objectName="UtilitySection")
            val = QLabel("", self)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setWordWrap(key == "message")
            grid.addWidget(cap, n // 3, (n % 3) * 2)
            grid.addWidget(val, n // 3, (n % 3) * 2 + 1)
            self.stats[key] = val
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)
        d.addLayout(grid)
        self.plot = SpeedPlot(ctx, self)
        d.addWidget(self.plot)

        controls = QHBoxLayout()
        self.pause_btn = QPushButton("Pause", self)
        self.pause_btn.clicked.connect(self._pause)
        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.clicked.connect(lambda: self.selected and self.ctx.transfers.cancel(self.selected))
        self.open_btn = QPushButton("Open destination", self)
        self.open_btn.clicked.connect(lambda: self.selected and self.ctx.commands.open_location(self.selected.dest,
                                                                                                new_tab=True))
        for b in (self.pause_btn, self.cancel_btn, self.open_btn):
            controls.addWidget(b)
        controls.addStretch(1)
        controls.addWidget(QLabel("Show", self, objectName="UtilitySection"))
        self.filter_combo = QComboBox(self)
        for value, label in FILTERS:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(lambda _: self.files.set_filter(self.filter_combo.currentData()))
        controls.addWidget(self.filter_combo)
        self.follow_cb = QCheckBox("Follow", self, checked=True)
        self.follow_cb.setToolTip("Keep the file being copied in view")
        controls.addWidget(self.follow_cb)
        self.files_cb = QCheckBox("Files", self, checked=bool(ctx.settings.get("transfers.show_files", True)))
        self.files_cb.setToolTip("Show every file with its result")
        self.files_cb.toggled.connect(self._toggle_files)
        controls.addWidget(self.files_cb)
        d.addLayout(controls)

        self.files = TransferFilesModel(ctx, self)
        self.table = QTableView(self)
        self.table.setModel(self.files)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 6)
        th = self.table.horizontalHeader()
        th.setHighlightSections(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(1, 420)
        self.table.setColumnWidth(2, 90)
        th.setStretchLastSection(True)
        self.table.doubleClicked.connect(self._open_file_folder)
        self.table.setVisible(self.files_cb.isChecked())
        d.addWidget(self.table, 1)
        split.addWidget(details)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        self.split = split
        self.refresh()

    # ==================================================================
    # Refresh (timer)
    # ==================================================================
    def refresh(self) -> None:
        transfers = self.ctx.transfer_log.transfers
        alive = {t.id for t in transfers}
        for tid in [i for i in self._items if i not in alive]:
            item = self._items.pop(tid)
            self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        pal = self.ctx.theme.palette
        for t in transfers:
            item = self._items.get(t.id)
            if item is None:
                item = self._items[t.id] = QTreeWidgetItem()
                item.setData(0, ID_ROLE, t.id)
                item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
                for col in (2, 3, 4):
                    item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.list.insertTopLevelItem(0, item)
            mark, result, colour = result_of(t)
            values = [mark, title_of(t), f"{t.file_count:,}", human_size(t.total_bytes),
                      f"{human_size(t.average_speed)}/s" if t.done_bytes else "", human_time(t.started), result]
            for col, text in enumerate(values):
                if item.text(col) != text:
                    item.setText(col, text)
            for col in (0, 6):
                item.setForeground(col, QColor(pal.get(colour, "#888888")))
            item.setToolTip(1, "\n".join(t.sources) + f"\n→ {t.dest}")
            item.setToolTip(6, t.message or result)
        if self.selected is None and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        if self.selected is not None and self.selected.id not in alive:
            self.select(None)
        active = [t for t in transfers if t.active]
        n = len(transfers)
        text = f"{n} transfer{'s' if n != 1 else ''}"
        if active:
            text += f" · {len(active)} running"
        self.summary_label.setText(text)
        self.clear_btn.setEnabled(any(not t.active for t in transfers))
        self._refresh_details()

    def _refresh_details(self) -> None:
        t = self.selected
        for w in (self.pause_btn, self.cancel_btn, self.open_btn, self.progress):
            w.setEnabled(t is not None)
        if t is None:
            self.title_label.setText("No transfers yet - copy or move something")
            self.progress.setValue(0)
            self.progress_label.setText("")
            for label in self.stats.values():
                label.setText("")
            self.plot.set_samples([], 0)
            return
        files = sum(1 for f in t.files if not f.is_dir) + t.files_trimmed
        folders = sum(1 for f in t.files if f.is_dir)
        what = f"{files:,} file{'s' if files != 1 else ''}"
        if folders:
            what += f" in {folders:,} folder{'s' if folders != 1 else ''}"
        verb = title_of(t).split(" ", 1)[0]
        src_folder = _short(_parent(t.sources[0])) if t.sources else ""
        self.title_label.setText(f"{verb} {what} ({human_size(t.total_bytes)}) from {src_folder} to {_short(t.dest)}")
        self.title_label.setToolTip("\n".join(t.sources) + f"\n→ {t.dest}")
        self.progress.setValue(int(t.progress * 1000))
        self.progress_label.setText(f"{human_size(t.done_bytes)} of {human_size(t.total_bytes)} · {t.progress * 100:.0f} %")
        s = self.stats
        s["now"].setText(t.current if t.active else "")
        if t.active:
            s["speed"].setText(f"{human_size(t.current_speed)}/s (average {human_size(t.average_speed)}/s)")
        else:
            s["speed"].setText(f"average {human_size(t.average_speed)}/s" if t.done_bytes else "")
        if t.active:
            left = t.eta
            s["time"].setText(f"{_duration(t.elapsed)} so far" + (f", about {_duration(left)} left" if left else ""))
        else:
            s["time"].setText(f"{human_time(t.started)} - took {_duration(t.elapsed)}")
        c = t.counts()
        good = sum(c.get(k, 0) for k in tm.GOOD)
        parts = [f"✓ {good:,}"]
        if t.failed:
            parts.append(f"✗ {t.failed:,} failed")
        for state, word in ((tm.SKIPPED, "skipped"), (tm.NOT_COPIED, "not copied")):
            if c.get(state):
                parts.append(f"{c[state]:,} {word}")
        waiting = sum(v for k, v in c.items() if k not in tm.FINAL)
        if waiting:
            parts.append(f"{waiting:,} to go")
        s["files"].setText("   ".join(parts))
        s["verify"].setText(f"{t.algorithm.upper()} checksums, read back after copying" if t.verify else "Off")
        s["message"].setText(t.message)
        s["message"].setStyleSheet(f"color: {self.ctx.theme.palette.get('error', '#e06c75')};"
                                   if t.state in (tm.NO_SPACE, tm.ERROR) else "")
        self.pause_btn.setEnabled(t.state in (tm.RUNNING, tm.PAUSED))
        self.pause_btn.setText("Resume" if t.state == tm.PAUSED else "Pause")
        self.cancel_btn.setEnabled(t.active)
        self.plot.set_samples(t.samples, t.average_speed if t.done_bytes else 0)
        if self.table.isVisible():
            self.files.refresh()
            if t.active and self.follow_cb.isChecked():
                row = self.files.row_of_current()
                if row >= 0:
                    self.table.scrollTo(self.files.index(row, 1), QAbstractItemView.ScrollHint.EnsureVisible)

    # ==================================================================
    # Actions
    # ==================================================================
    def select(self, t: Optional[tm.Transfer]) -> None:
        if t is not None:
            item = self._items.get(t.id)
            if item is None:
                self.refresh()
                item = self._items.get(t.id)
            if item is not None:
                self.list.setCurrentItem(item)
                return
        self.selected = None
        self.files.set_transfer(None)
        self._refresh_details()

    def _select_item(self, item: Optional[QTreeWidgetItem]) -> None:
        t = self.ctx.transfer_log.get(item.data(0, ID_ROLE)) if item is not None else None
        if t is not self.selected:
            self.selected = t
            self.files.set_transfer(t)
        self._refresh_details()

    def _pause(self) -> None:
        t = self.selected
        if t is None:
            return
        if t.state == tm.PAUSED:
            self.ctx.transfers.resume(t)
        else:
            self.ctx.transfers.pause(t)
        self._refresh_details()

    def _clear(self) -> None:
        self.ctx.transfer_log.clear()
        self.refresh()

    def _toggle_files(self, on: bool) -> None:
        self.table.setVisible(on)
        self.ctx.settings.set("transfers.show_files", bool(on))
        if on:
            self.files.refresh()

    def _open_file_folder(self, index) -> None:
        f = self.files.file_at(index.row())
        if f is None:
            return
        target = f.dst if f.state in tm.GOOD else f.src
        self.ctx.commands.open_location(_parent(target) or target, new_tab=True)

    def set_mode_label(self, in_tab: bool) -> None:
        self.mode_btn.setText("Open in a window" if in_tab else "Open as tab")
        self.mode_btn.setToolTip("Move this to its own window" if in_tab else "Move this into the main window, as a tab")

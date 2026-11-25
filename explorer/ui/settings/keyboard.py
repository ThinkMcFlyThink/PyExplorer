"""
KeyboardPage - every command and its shortcut (Settings -> Keyboard, F1).

Commands are grouped like the menus. Double-click a shortcut (or press Enter)
and type the new keys; Backspace or "Remove shortcut" leaves it without one.
Giving a command keys that another command uses MOVES them (and says so):
two commands sharing keys would make both of them stop working.

Changes apply straight away (settings "shortcuts": only the ones that differ
from the defaults are stored).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QMenu,
                               QPushButton, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...actions import plain_label
from ...core.settings_schema import matches

ID_ROLE = Qt.ItemDataRole.UserRole
NATIVE = QKeySequence.SequenceFormat.NativeText
PORTABLE = QKeySequence.SequenceFormat.PortableText


class _KeyEditor(QKeySequenceEdit):
    """Takes ONE key combination, then finishes (no waiting for a second chord)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        if hasattr(self, "setMaximumSequenceLength"):
            self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(True)


class _Delegate(QStyledItemDelegate):
    def __init__(self, page: "KeyboardPage") -> None:
        super().__init__(page.tree)
        self.page = page

    def createEditor(self, parent, option, index):
        if index.column() != 1 or not index.data(ID_ROLE):
            return None
        editor = _KeyEditor(parent)
        editor.editingFinished.connect(lambda: self.commitData.emit(editor) or self.closeEditor.emit(editor))
        return editor

    def setEditorData(self, editor, index) -> None:
        editor.setKeySequence(QKeySequence(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        QTimer.singleShot(0, editor.setFocus)

    def setModelData(self, editor, model, index) -> None:
        self.page.assign(index.data(ID_ROLE), editor.keySequence().toString(PORTABLE))


class KeyboardPage(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.note = QLabel("", self, objectName="SettingHelp")
        self.note.setWordWrap(True)
        top.addWidget(self.note, 1)
        reset = QPushButton("Reset all shortcuts", self)
        reset.clicked.connect(self.reset_all)
        top.addWidget(reset, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self.tree = QTreeWidget(self, objectName="SettingsTable")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Command", "Shortcut"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(False)         # headings are taller than rows
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                  QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.setItemDelegate(_Delegate(self))
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # the page scrolls instead
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.installEventFilter(self)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 200)
        layout.addWidget(self.tree)
        self._items: Dict[str, QTreeWidgetItem] = {}
        self._fill()
        self._set_note()

    # -- building ----------------------------------------------------------------
    def _groups(self) -> List[tuple]:
        """[(title, [action ids])] in menu order; commands in no menu go under "Other"."""
        from ..menubar import MENUS
        seen, out = set(), []

        def flatten(items) -> List[str]:
            ids = []
            if isinstance(items, str):
                return ids
            for item in items:
                if isinstance(item, tuple):
                    ids += flatten(item[1])
                elif item != "-" and item not in seen and self.ctx.actions.get(item) is not None:
                    seen.add(item)
                    ids.append(item)
            return ids
        for title, items in MENUS:
            ids = flatten(items)
            if ids:
                out.append((title.replace("&", ""), ids))
        rest = [a.id for a in self.ctx.actions.all() if a.id not in seen and not a.id.startswith("theme.")]
        if rest:
            out.append(("Other", rest))
        return out

    def _row_heights(self):
        """(command row, section heading): rows as tall as a text field, so the key editor
        fits in one; headings a little taller, which leaves air above them."""
        h = self.tree.fontMetrics().height() + 10
        return h, h + 8

    def _fill(self) -> None:
        self.tree.clear()
        self._items = {}
        bold = QFont(self.tree.font())
        bold.setBold(True)
        row_h, head_h = self._row_heights()
        for title, ids in self._groups():
            head = QTreeWidgetItem([title.upper(), ""])
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            head.setFont(0, bold)
            head.setForeground(0, QColor(self.ctx.theme.palette.get("fg_dim", "#888888")))
            head.setTextAlignment(0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            head.setSizeHint(0, QSize(0, head_h))          # (width 0: a valid size; -1 would be ignored)
            self.tree.addTopLevelItem(head)
            for action_id in ids:
                action = self.ctx.actions.get(action_id)
                item = QTreeWidgetItem([plain_label(action.label), action.shortcut])
                item.setData(0, ID_ROLE, action_id)
                item.setData(1, ID_ROLE, action_id)
                item.setIcon(0, action.qaction.icon())
                item.setSizeHint(0, QSize(0, row_h))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                self.tree.addTopLevelItem(item)
                self._items[action_id] = item
        self._style_rows()
        self._fit()

    def _style_rows(self) -> None:
        pal = self.ctx.theme.palette
        for action_id, item in self._items.items():
            changed = self._changed(action_id)
            font = QFont(self.tree.font())
            font.setBold(changed)
            item.setFont(1, font)
            item.setForeground(1, QColor(pal.get("accent" if changed else "fg_dim", "#888888")))
            default = QKeySequence(self.ctx.actions.default_shortcut(action_id)).toString(NATIVE)
            item.setToolTip(1, ("Changed - default: " + (default or "none")) if changed else
                            "Double-click to change")

    def _fit(self) -> None:
        """As tall as its rows: the Settings page scrolls, not the list."""
        row_h, head_h = self._row_heights()
        rows = 0
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item.isHidden():
                rows += row_h if item.data(0, ID_ROLE) else head_h
        frame = 2 * self.tree.frameWidth() + 10                    # border + the list's own padding
        self.tree.setFixedHeight(self.tree.header().sizeHint().height() + rows + frame)

    def _set_note(self, text: str = "") -> None:
        self.note.setText(text or "Double-click a shortcut and press the new keys. Backspace removes it.")

    # -- shortcuts ---------------------------------------------------------------------
    def _current(self, action_id: str) -> str:
        action = self.ctx.actions.get(action_id)
        seqs = action.qaction.shortcuts() if action else []
        return seqs[0].toString(PORTABLE) if seqs else ""

    def _default(self, action_id: str) -> str:
        return QKeySequence(self.ctx.actions.default_shortcut(action_id)).toString(PORTABLE)

    def _defaults(self, action_id: str) -> List[str]:
        """Every default shortcut (Up has two: Alt+Up and Backspace), normalised."""
        action = self.ctx.actions.get(action_id)
        return [QKeySequence(k).toString(PORTABLE) for k in (action.defaults if action else []) if k]

    def _changed(self, action_id: str) -> bool:
        action = self.ctx.actions.get(action_id)
        current = [s.toString(PORTABLE) for s in action.qaction.shortcuts()] if action else []
        return current != self._defaults(action_id)

    def assign(self, action_id: str, keys: str) -> None:
        """Give ``action_id`` these keys ("" = none). Another command that had them loses them."""
        if not action_id or keys == self._current(action_id):
            return
        overrides = dict(self.ctx.settings.get("shortcuts") or {})
        moved_from = ""
        if keys:
            for other in self.ctx.actions.all():
                theirs = [s.toString(PORTABLE) for s in other.qaction.shortcuts()]
                if other.id != action_id and keys in theirs:
                    rest = [k for k in theirs if k != keys]           # e.g. Up keeps Alt+Up if Backspace goes
                    overrides[other.id] = rest[0] if rest else ""
                    moved_from = plain_label(other.label)
        overrides[action_id] = keys
        self._save(overrides)
        label = plain_label(self.ctx.actions.get(action_id).label)
        native = QKeySequence(keys).toString(NATIVE)
        if moved_from:
            self._set_note(f"{native} moved from “{moved_from}” to “{label}”.")
        elif keys:
            self._set_note(f"“{label}” is now {native}.")
        else:
            self._set_note(f"“{label}” has no shortcut now.")

    def reset(self, action_id: str) -> None:
        overrides = dict(self.ctx.settings.get("shortcuts") or {})
        overrides.pop(action_id, None)
        self._save(overrides)

    def reset_all(self) -> None:
        self._save({})
        self._set_note("Every shortcut is back to its default.")

    def _save(self, overrides: dict) -> None:
        # Keep only real differences from the defaults ("Del" and "Delete" are the same keys).
        # An override is ONE shortcut: for a command with two defaults, even its first one
        # alone is a difference (the second is gone).
        clean = {}
        for k, v in overrides.items():
            if self.ctx.actions.get(k) is None:
                continue
            wanted = [QKeySequence(v).toString(PORTABLE)] if v else []
            if wanted != self._defaults(k):
                clean[k] = v
        self.ctx.settings.set("shortcuts", clean)
        self.ctx.actions.apply_overrides()
        self.refresh()

    def refresh(self) -> None:
        for action_id, item in self._items.items():
            action = self.ctx.actions.get(action_id)
            item.setText(1, action.shortcut if action else "")
        self._style_rows()

    # -- interaction -----------------------------------------------------------------------
    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        action_id = item.data(0, ID_ROLE) if item is not None else None
        if not action_id:
            return
        menu = QMenu(self)
        menu.addAction("Change shortcut…", lambda: self.tree.editItem(item, 1))
        menu.addAction("Remove shortcut", lambda: self.assign(action_id, ""))
        reset = menu.addAction("Reset to default", lambda: self.reset(action_id))
        reset.setEnabled(self._changed(action_id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event) -> bool:
        if obj is self.tree and event.type() == QEvent.Type.KeyPress and \
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.tree.currentItem()
            if item is not None and item.data(0, ID_ROLE) and self.tree.state() != QAbstractItemView.State.EditingState:
                self.tree.editItem(item, 1)
                return True
        return False

    def filter(self, query: str) -> bool:
        """Show only commands matching the search box; True if any do."""
        any_shown = False
        head: Optional[QTreeWidgetItem] = None
        head_has = False
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            action_id = item.data(0, ID_ROLE)
            if not action_id:
                if head is not None:
                    head.setHidden(not head_has)
                head, head_has = item, False
                continue
            hit = not query or matches(query, item.text(0), item.text(1), action_id.replace(".", " "))
            item.setHidden(not hit)
            head_has = head_has or hit
            any_shown = any_shown or hit
        if head is not None:
            head.setHidden(not head_has)
        self._fit()
        return any_shown

    def on_theme(self) -> None:
        self._fill()

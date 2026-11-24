"""
ViewBehaviour - everything the file views have in common.

Mixed into QTreeView / QListView subclasses (see details.py, grids.py).
A view talks to its owner (the tab) only through this small "host"
interface, so views can be reused or swapped freely:

    host.on_view_activate(entries, new_tab)     host.on_view_context_menu(global_pos, entries)
    host.on_view_selection_changed()            host.on_view_zoom(delta)
    host.on_view_type(text)                     host.on_view_focus()
    host.on_view_sort(column_id)                host.on_view_drop(paths, dest, op)
    host.go_back() / host.go_forward()          host.current_folder  (str | None)
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLineEdit, QStyledItemDelegate

from ...core.fileinfo import FileEntry
from ..models.file_model import INTERNAL_MIME, paths_from_mime

SEL = QItemSelectionModel.SelectionFlag


class FileDelegate(QStyledItemDelegate):
    """Draws items, with three small touches:
    * full-row hover highlight in the Details table, and no dotted focus boxes;
    * inline rename pre-selects the name without its extension;
    * views with a fixed cell size (List, Icons) get EVERY item at that size.
      Those views use Qt's "uniform item sizes" speed-up, which measures ONE
      item and uses its size for all. Entries stream in unsorted, so that one
      could be any file: a long name made every item wider than its column
      (overlapping the next column), a short one made them narrower."""

    def sizeHint(self, option, index):
        fixed = getattr(self.parent(), "fixed_item_size", None)
        size = fixed() if fixed is not None else None
        return size if size is not None else super().sizeHint(option, index)

    def paint(self, painter, option, index) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QStyle
        view = self.parent()
        option.state &= ~QStyle.StateFlag.State_HasFocus
        if getattr(view, "full_row_hover", False):
            option.state &= ~QStyle.StateFlag.State_MouseOver
            if index.row() == getattr(view, "_hover_row", -1) and \
                    not option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, QColor(view.ctx.theme.palette.get("hover", "#888888")))
        usage = self._drive_usage(view, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if usage is not None and not selected and view.mode != "icons":
            self._usage_bar(view, painter, option.rect, usage)                    # behind the name
        super().paint(painter, option, index)
        if usage is not None and view.mode == "icons":                            # a slim bar under the name
            r = option.rect
            self._usage_bar(view, painter, r.adjusted(6, r.height() - 9, -6, -2), usage)
        elif usage is not None and selected:                                      # over the selection colour
            self._usage_bar(view, painter, option.rect, usage, on_top=True)

    @staticmethod
    def _drive_usage(view, index):
        """Computer view: how full the drive in this row is (Name column only)."""
        model = index.model()
        entry = model.entry(index) if hasattr(model, "entry") else None
        if entry is None or entry.parent != "computer://" or not getattr(view, "ctx", None):
            return None
        columns = getattr(model, "columns", None)
        if columns and view.mode in ("details", "tree") and columns[index.column()] != "name":
            return None
        return view.ctx.drive_usage.get(entry.path) if view.ctx.drive_usage else None

    @staticmethod
    def _usage_bar(view, painter, rect, usage, on_top: bool = False) -> None:
        from ..usage_bar import paint_usage_bar
        paint_usage_bar(painter, rect, usage.fraction, view.ctx.theme.palette, on_top=on_top)


    def setEditorData(self, editor, index) -> None:
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            name = editor.text()
            entry = index.model().entry(index)
            dot = name.rfind(".")
            end = dot if (entry is not None and not entry.is_dir and dot > 0) else len(name)
            QTimer.singleShot(0, lambda: editor.setSelection(0, end))


class MatchLineDelegate(FileDelegate):
    """For the Search tab's "Matching line" column only (set per column, so
    folder tabs never pay for it). The matched text is drawn in bold italic;
    a long line is cut at the END (it starts just before the match), never in
    the middle where the match is."""

    def paint(self, painter, option, index) -> None:
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        entry = index.model().entry(index)
        extra = (entry.extra or {}) if entry is not None else {}
        text = extra.get("snippet", "")
        start, end = extra.get("match", (0, 0))
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""                                   # the cell without its text: background, selection, hover
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        view = self.parent()
        if getattr(view, "full_row_hover", False):
            opt.state &= ~QStyle.StateFlag.State_MouseOver
            if index.row() == getattr(view, "_hover_row", -1) and not opt.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(opt.rect, QColor(view.ctx.theme.palette.get("hover", "#888888")))
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        if not text:
            return
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        rect = rect.adjusted(2, 0, -2, 0)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setPen(opt.palette.color(opt.palette.ColorRole.HighlightedText if selected
                                         else opt.palette.ColorRole.Text))
        normal = QFont(opt.font)
        strong = QFont(opt.font)
        strong.setBold(True)
        strong.setItalic(True)
        pieces = [(text[:start], normal), (text[start:end], strong), (text[end:], normal)]
        x, right = rect.left(), rect.right()
        for piece, font in pieces:
            if not piece or x >= right:
                continue
            painter.setFont(font)
            fm = QFontMetrics(font)
            room = right - x
            shown = piece if fm.horizontalAdvance(piece) <= room else fm.elidedText(piece, Qt.TextElideMode.ElideRight, room)
            painter.drawText(QRect(x, rect.top(), room, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), shown)
            x += fm.horizontalAdvance(shown)
            if shown != piece:
                break
        painter.restore()


class ViewBehaviour:
    """Mixin. The class using it must also inherit a QAbstractItemView."""

    ZOOM_SIZES: List[int] = [16, 20, 24, 32, 48]
    mode = "base"

    def setup_behaviour(self, ctx, host, zoom: int) -> None:
        self.ctx = ctx
        self.host = host
        self.zoom = max(0, min(len(self.ZOOM_SIZES) - 1, zoom))
        self._saved: Optional[dict] = None
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # rename via F2 / menu
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDragDropOverwriteMode(True)           # dropping ON a folder means "into it"
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setItemDelegate(FileDelegate(self))
        self._hover_row = -1
        self.entered.connect(self._on_entered)
        self.setMouseTracking(True)                   # hover highlight
        self.doubleClicked.connect(self._on_double_clicked)
        self.clicked.connect(self._on_clicked)

    # ======================================================================
    # Model
    # ======================================================================
    def attach_model(self, model) -> None:
        """Call instead of setModel: also hooks up state-keeping across refreshes."""
        self.setModel(model)
        self._model_conns = []
        self.connect_model(model.modelAboutToBeReset, self._save_state)
        self.connect_model(model.modelReset, self._restore_state)
        self.selectionModel().selectionChanged.connect(lambda *a: self.host.on_view_selection_changed())
        self.configure_model(model)
        self.apply_zoom()

    def connect_model(self, signal, slot) -> None:
        """Connect to a model signal and remember it, so detach_model can undo it."""
        signal.connect(slot)
        self._model_conns.append((signal, slot))

    def detach_model(self) -> None:
        """The view is about to be replaced: stop listening to the (shared) model."""
        for signal, slot in getattr(self, "_model_conns", []):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._model_conns = []

    def configure_model(self, model) -> None:
        """Views set the model options they need (tree on/off, thumbnails)."""
        model.set_tree_enabled(False)
        model.thumbnail_size = 0

    def entry_at(self, index: QModelIndex) -> Optional[FileEntry]:
        return self.model().entry(index.siblingAtColumn(0)) if index.isValid() else None

    # ======================================================================
    # Selection helpers (used by the tab and commands)
    # ======================================================================
    def selected_entries(self) -> List[FileEntry]:
        model = self.model()
        rows = [i for i in self.selectionModel().selectedIndexes() if i.column() == 0]
        rows.sort(key=lambda i: (i.internalId(), i.row()))
        return [e for e in (model.entry(i) for i in rows) if e is not None]

    def current_path(self) -> Optional[str]:
        e = self.entry_at(self.currentIndex())
        return e.path if e else None

    def select_paths(self, paths, cursor: Optional[str] = None, scroll: bool = True) -> None:
        model = self.model()
        indexes = [model.index_for_path(p) for p in paths]
        indexes = sorted((i for i in indexes if i.isValid()), key=lambda i: (i.internalId(), i.row()))
        selection = QItemSelection()
        last_col = max(0, model.columnCount() - 1)
        start = prev = None
        for idx in indexes + [None]:          # merge neighbouring rows into ranges (fast for big selections)
            if idx is not None and prev is not None and idx.internalId() == prev.internalId() \
                    and idx.row() == prev.row() + 1:
                prev = idx
                continue
            if start is not None:
                selection.select(start, prev.siblingAtColumn(last_col))
            start = prev = idx
        self.selectionModel().select(selection, SEL.ClearAndSelect | SEL.Rows)
        cur = model.index_for_path(cursor) if cursor else (indexes[0] if indexes else QModelIndex())
        if cur.isValid():
            self.selectionModel().setCurrentIndex(cur, SEL.NoUpdate)
            if scroll:
                self.scrollTo(cur)

    def select_all(self) -> None:
        self.selectAll()

    def invert_selection(self) -> None:
        model = self.model()
        selected = {e.path for e in self.selected_entries()}
        self.select_paths([e.path for e in model.top_entries() if e.path not in selected], scroll=False)

    def top_path(self) -> Optional[str]:
        e = self.entry_at(self.indexAt(QPoint(8, 8)))
        return e.path if e else None

    def scroll_to_path(self, path: str) -> None:
        idx = self.model().index_for_path(path)
        if idx.isValid():
            self.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtTop)

    def begin_rename(self, path: str) -> None:
        idx = self.model().index_for_path(path)
        if idx.isValid():
            self.selectionModel().setCurrentIndex(idx, SEL.ClearAndSelect | SEL.Rows)
            self.scrollTo(idx)
            self.edit(idx)

    # -- keeping state across model resets ----------------------------------
    def _save_state(self) -> None:
        self._saved = {
            "selected": [e.path for e in self.selected_entries()],
            "current": self.current_path(),
            "v": self.verticalScrollBar().value(),
            "h": self.horizontalScrollBar().value(),
        }
        self.save_extra_state(self._saved)

    def _restore_state(self) -> None:
        saved, self._saved = self._saved, None
        if not saved:
            return
        self.restore_extra_state(saved)
        if saved["selected"] or saved["current"]:
            self.select_paths(saved["selected"], cursor=saved["current"], scroll=False)
        self.doItemsLayout()                 # make scroll ranges correct before restoring them
        self.verticalScrollBar().setValue(saved["v"])
        self.horizontalScrollBar().setValue(saved["h"])

    def save_extra_state(self, state: dict) -> None:
        """Hook: the tree view remembers which folders are expanded."""

    def restore_extra_state(self, state: dict) -> None:
        pass

    def get_state(self) -> dict:
        """Saved in the session (per tab)."""
        return {}

    def set_state(self, state: dict) -> None:
        pass

    # ======================================================================
    # Zoom
    # ======================================================================
    @property
    def icon_px(self) -> int:
        return self.ZOOM_SIZES[self.zoom]

    def set_zoom(self, level: int) -> None:
        self.zoom = max(0, min(len(self.ZOOM_SIZES) - 1, level))
        self.apply_zoom()

    def apply_zoom(self) -> None:
        from PySide6.QtCore import QSize
        self.setIconSize(QSize(self.icon_px, self.icon_px))

    # ======================================================================
    # Mouse & keyboard
    # ======================================================================
    def _on_entered(self, index: QModelIndex) -> None:
        if getattr(self, "full_row_hover", False) and index.row() != self._hover_row:
            self._hover_row = index.row()
            self.viewport().update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._hover_row != -1:
            self._hover_row = -1
            self.viewport().update()

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if not self.ctx.settings.get("behaviour.single_click_open"):
            e = self.entry_at(index)
            if e:
                self.host.on_view_activate([e], False)

    def _on_clicked(self, index: QModelIndex) -> None:
        from PySide6.QtWidgets import QApplication
        if self.ctx.settings.get("behaviour.single_click_open") and \
                QApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier:
            e = self.entry_at(index)
            if e:
                self.host.on_view_activate([e], False)

    def mousePressEvent(self, event) -> None:
        button = event.button()
        if button == Qt.MouseButton.BackButton:
            self.host.go_back()
            return
        if button == Qt.MouseButton.ForwardButton:
            self.host.go_forward()
            return
        if button == Qt.MouseButton.MiddleButton:
            e = self.entry_at(self.indexAt(event.position().toPoint()))
            if e and e.is_dir:
                self.host.on_view_activate([e], True)
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.host.on_view_zoom(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.state() != QAbstractItemView.State.EditingState:
            entries = self.selected_entries()
            if entries:
                new_tab = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.host.on_view_activate(entries, new_tab)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyboardSearch(self, text: str) -> None:
        """Typing letters in a view: open the instant filter (or jump to a name)."""
        if self.ctx.settings.get("behaviour.type_to_filter", True) and text.strip():
            self.host.on_view_type(text)
        else:
            super().keyboardSearch(text)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.host.on_view_focus()

    def contextMenuEvent(self, event) -> None:
        from PySide6.QtGui import QContextMenuEvent
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            idx = self.currentIndex()
            rect = self.visualRect(idx) if idx.isValid() else self.viewport().rect()
            global_pos = self.viewport().mapToGlobal(rect.center() if idx.isValid() else QPoint(30, 30))
            entries = self.selected_entries()
        else:
            global_pos = event.globalPos()
            idx = self.indexAt(self.viewport().mapFromGlobal(global_pos))
            entry = self.entry_at(idx)
            if entry is None:
                self.clearSelection()
                entries = []
            else:
                if not self.selectionModel().isSelected(idx):
                    self.selectionModel().setCurrentIndex(idx, SEL.ClearAndSelect | SEL.Rows)
                entries = self.selected_entries()
        self.host.on_view_context_menu(global_pos, entries)
        event.accept()

    # ======================================================================
    # Drag & drop (works with other apps too: we offer/accept file URLs)
    # ======================================================================
    def startDrag(self, supported_actions) -> None:
        indexes = [i for i in self.selectionModel().selectedIndexes() if i.column() == 0]
        if not indexes:
            return
        mime = self.model().mimeData(indexes)
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = indexes[0].data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            drag.setPixmap(icon.pixmap(48, 48))
        # We never let Qt delete rows after a "move": our file operations do the
        # work and the folder refresh shows the result.
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction,
                  Qt.DropAction.CopyAction)

    @staticmethod
    def _accepts(event) -> bool:
        mime = event.mimeData()
        return mime.hasFormat(INTERNAL_MIME) or mime.hasUrls()

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            super().dragEnterEvent(event)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            event.ignore()
            return
        super().dragMoveEvent(event)       # draws the highlight on the folder under the mouse
        event.accept()

    def drop_destination(self, pos: QPoint) -> Optional[str]:
        entry = self.entry_at(self.indexAt(pos))
        if entry is not None and entry.is_dir:
            return entry.path
        return self.host.current_folder

    def dropEvent(self, event) -> None:
        paths = paths_from_mime(event.mimeData())
        dest = self.drop_destination(event.position().toPoint())
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if not paths or not dest:
            event.ignore()
            return
        mods = event.modifiers()
        op = "copy" if mods & Qt.KeyboardModifier.ControlModifier else \
            "move" if mods & Qt.KeyboardModifier.ShiftModifier else "auto"
        # Always report "copy" back to the source, so other apps never delete their originals.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.host.on_view_drop(paths, dest, op)

    # ======================================================================
    # "Loading…" / "This folder is empty" / error message
    # ======================================================================
    def set_message(self, text: str) -> None:
        if text != getattr(self, "_message", ""):
            self._message = text
            self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        message = getattr(self, "_message", "")
        if message and self.model() is not None and self.model().rowCount() == 0:
            from PySide6.QtGui import QColor, QPainter
            p = QPainter(self.viewport())
            p.setPen(QColor(self.ctx.theme.palette.get("fg_dim", "#888888")))
            rect = self.viewport().rect().adjusted(20, 48, -20, -20)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                       message)
            p.end()

    # ======================================================================
    def refresh_look(self) -> None:
        """Theme / folder colour / clipboard changed: repaint."""
        self.viewport().update()

"""
BreadcrumbBar - the address bar.

    Home › Documents › Projects › pyexplorer ›

* Click a part to go there (middle-click: open it in a new tab).
* Click a "›" for a menu of the sub-folders at that level.
* Click the empty space (or press Ctrl+L) to type a path. Local paths
  auto-complete; network URIs like sftp://me@server/var/www work too.
* When the path is too long, the first parts fold into a "«" menu.
* You can drop files onto a part to copy/move them there.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QCompleter, QFileSystemModel, QLineEdit, QMenu, QSizePolicy, QStackedWidget,
                               QWidget)

from ..core.events import Topics
from .models.file_model import INTERNAL_MIME, paths_from_mime

SEP = "›"


class _Crumbs(QWidget):
    """The painted row of clickable parts."""

    def __init__(self, bar: "BreadcrumbBar") -> None:
        super().__init__(bar)
        self.bar = bar
        self.ctx = bar.ctx
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hits: List[Tuple[str, int, float, float]] = []      # (kind, crumb index, x1, x2)
        self._hover: Optional[Tuple[str, int]] = None
        self._first = 0

    def sizeHint(self) -> QSize:
        # As tall as a text field (theme.metrics: text + 2 + 2x3 padding + 2x1 border),
        # so the address bar and the filter field under it are the same height.
        return QSize(200, QFontMetrics(self.font()).height() + 10)

    # -- layout + painting ------------------------------------------------------
    def _layout(self) -> None:
        crumbs = self.bar.crumbs
        fm, bold_fm = QFontMetrics(self.font()), QFontMetrics(self._bold())
        pad, gap = 8, 4
        avail = self.width() - 40                  # keep a free area that starts editing

        def width_of(i: int) -> int:
            m = bold_fm if i == len(crumbs) - 1 else fm
            return m.horizontalAdvance(crumbs[i][0]) + 2 * pad + fm.horizontalAdvance(SEP) + 2 * gap

        first = 0
        overflow_w = fm.horizontalAdvance("«") + 2 * pad
        while first < len(crumbs) - 1 and \
                sum(width_of(i) for i in range(first, len(crumbs))) + (overflow_w if first else 0) > avail:
            first += 1
        self._first = first
        hits = []
        x = 4.0
        if first:
            hits.append(("overflow", 0, x, x + overflow_w))
            x += overflow_w
        for i in range(first, len(crumbs)):
            m = bold_fm if i == len(crumbs) - 1 else fm
            w = m.horizontalAdvance(crumbs[i][0]) + 2 * pad
            hits.append(("crumb", i, x, x + w))
            x += w
            sw = fm.horizontalAdvance(SEP) + 2 * gap
            hits.append(("sep", i, x, x + sw))
            x += sw
        self._hits = hits

    def _bold(self) -> QFont:
        f = QFont(self.font())
        f.setBold(True)
        return f

    def paintEvent(self, event) -> None:
        self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.ctx.theme.palette
        from PySide6.QtGui import QColor
        p.setPen(QColor(pal["border"]))
        p.setBrush(QColor(pal["entry"]))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)    # (= text fields' radius)
        h = self.height()
        crumbs = self.bar.crumbs
        for kind, i, x1, x2 in self._hits:
            if self._hover == (kind, i):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal["hover"]))
                p.drawRoundedRect(QRectF(x1, 3, x2 - x1, h - 6), 4, 4)
            rect = QRectF(x1, 0, x2 - x1, h)
            if kind == "overflow":
                p.setPen(QColor(pal["fg_dim"]))
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "«")
            elif kind == "crumb":
                last = i == len(crumbs) - 1
                p.setFont(self._bold() if last else self.font())
                p.setPen(QColor(pal["fg"]))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, crumbs[i][0])
            else:
                p.setFont(self.font())
                p.setPen(QColor(pal["fg_dim"]))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, SEP)
        p.end()

    # -- mouse ---------------------------------------------------------------------
    def _hit(self, x: float):
        for kind, i, x1, x2 in self._hits:
            if x1 <= x < x2:
                return kind, i
        return None

    def mouseMoveEvent(self, event) -> None:
        hit = self._hit(event.position().x())
        if hit != self._hover:
            self._hover = hit
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        hit = self._hit(event.position().x())
        middle = event.button() == Qt.MouseButton.MiddleButton
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            return
        if hit is None:
            if not middle:
                self.bar.start_edit()
            return
        kind, i = hit
        crumbs = self.bar.crumbs
        global_pos = self.mapToGlobal(event.position().toPoint())
        if kind == "crumb":
            self.bar.on_navigate(crumbs[i][1], middle)
        elif kind == "sep":
            self.bar.subfolder_menu(crumbs[i][1], global_pos)
        elif kind == "overflow":
            menu = QMenu(self)
            for label, path in crumbs[:self._first]:
                menu.addAction(label).triggered.connect(lambda _=False, p=path: self.bar.on_navigate(p, False))
            menu.exec(global_pos)

    def contextMenuEvent(self, event) -> None:
        hit = self._hit(event.pos().x())
        path = self.bar.crumbs[hit[1]][1] if hit and hit[0] == "crumb" else self.bar.path
        menu = QMenu(self)
        menu.addAction("Open in new tab").triggered.connect(lambda: self.bar.on_navigate(path, True))
        menu.addAction("Copy path").triggered.connect(lambda: self.ctx.commands.copy_text(path))
        menu.addAction("Edit location…").triggered.connect(self.bar.start_edit)
        menu.exec(event.globalPos())

    # -- dropping files on a crumb -------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(INTERNAL_MIME) or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        hit = self._hit(event.position().x())
        self._hover = hit
        self.update()
        if hit and hit[0] == "crumb":
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        hit = self._hit(event.position().x())
        self._hover = None
        self.update()
        if hit and hit[0] == "crumb":
            mods = event.modifiers()
            op = "copy" if mods & Qt.KeyboardModifier.ControlModifier else \
                "move" if mods & Qt.KeyboardModifier.ShiftModifier else "auto"
            self.ctx.commands.drop_files(paths_from_mime(event.mimeData()), self.bar.crumbs[hit[1]][1], op)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()


_FOLDER_MODEL = None


def _shared_folder_model() -> QFileSystemModel:
    """One folder model for every tab's path completion."""
    global _FOLDER_MODEL
    if _FOLDER_MODEL is None:
        from PySide6.QtCore import QDir
        from PySide6.QtWidgets import QApplication
        _FOLDER_MODEL = QFileSystemModel(QApplication.instance())
        _FOLDER_MODEL.setRootPath("")
        _FOLDER_MODEL.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
    return _FOLDER_MODEL


class _PathEdit(QLineEdit):
    def __init__(self, bar: "BreadcrumbBar") -> None:
        super().__init__(bar)
        self.bar = bar

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.bar.stop_edit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not (self.completer() and self.completer().popup().isVisible()):
            self.bar.stop_edit()


class BreadcrumbBar(QStackedWidget):
    def __init__(self, ctx, on_navigate: Callable[[str, bool], None], parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.on_navigate = on_navigate
        self.path = ""
        self.crumbs: List[Tuple[str, str]] = []
        self.crumb_widget = _Crumbs(self)
        self.edit = _PathEdit(self)
        self.addWidget(self.crumb_widget)
        self.addWidget(self.edit)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Auto-complete local folder paths (Qt reads the disk in its own thread).
        completer = QCompleter(_shared_folder_model(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.edit.setCompleter(completer)
        self.edit.returnPressed.connect(self._commit)
        self._unsub = ctx.bus.subscribe(Topics.THEME_CHANGED, lambda **_: self.crumb_widget.update())
        self.destroyed.connect(lambda *_: self._unsub())

    def sizeHint(self) -> QSize:
        return QSize(300, max(self.crumb_widget.sizeHint().height(), self.edit.sizeHint().height()))

    def set_path(self, path: str) -> None:
        self.path = path
        try:
            self.crumbs = self.ctx.vfs.resolve(path).breadcrumbs(path)
        except OSError:
            self.crumbs = [(path, path)]
        self.crumb_widget.update()

    def subfolder_menu(self, folder: str, global_pos) -> None:
        """List the folder's sub-folders in the background, then pop up a menu."""
        def show(entries) -> None:
            dirs = sorted((e for e in entries if e.is_dir and not e.hidden), key=lambda e: e.name.lower())
            menu = QMenu(self)
            if not dirs:
                menu.addAction("(no sub-folders)").setEnabled(False)
            for e in dirs[:80]:
                act = menu.addAction(self.ctx.icons.folder_for(e.path), e.name)
                act.triggered.connect(lambda _=False, p=e.path: self.on_navigate(p, False))
            if len(dirs) > 80:
                menu.addAction(f"… {len(dirs) - 80} more").setEnabled(False)
            menu.exec(global_pos)

        self.ctx.scanner.list_all(folder, show, lambda exc: None)

    # -- edit mode -------------------------------------------------------------------
    def start_edit(self) -> None:
        try:
            text = self.ctx.vfs.resolve(self.path).display(self.path)
        except OSError:
            text = self.path
        if text.startswith("computer://"):
            text = ""
        self.edit.setText(text)
        self.setCurrentWidget(self.edit)
        self.edit.setFocus()
        self.edit.selectAll()

    def stop_edit(self) -> None:
        if self.currentWidget() is self.edit:
            self.setCurrentWidget(self.crumb_widget)

    def _commit(self) -> None:
        text = self.edit.text().strip()
        self.stop_edit()
        if text:
            self.on_navigate(text, False)

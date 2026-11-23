"""
Grid views built on QListView:

* IconView - big icons (and image thumbnails) with the name underneath.
* ListView - small icon + name, laid out in columns.

Ctrl + mouse wheel changes the icon size (see base.ViewBehaviour).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QListView

from .base import ViewBehaviour


class _GridBase(ViewBehaviour, QListView):
    def __init__(self, ctx, host, zoom: int = 1, parent=None) -> None:
        QListView.__init__(self, parent)
        self.setup_behaviour(ctx, host, zoom)
        self.setUniformItemSizes(True)              # big speed-up for huge folders
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setSelectionRectVisible(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Lay out huge folders in chunks between screen updates, so the window never freezes.
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(1000)

    def apply_zoom(self) -> None:
        super().apply_zoom()
        self.setGridSize(self.cell_size())
        self.configure_thumbnails()
        self.doItemsLayout()

    def configure_thumbnails(self) -> None:
        if self.model() is not None:
            self.model().thumbnail_size = 0

    def configure_model(self, model) -> None:
        model.set_tree_enabled(False)
        self.configure_thumbnails()

    def cell_size(self) -> QSize:
        raise NotImplementedError

    GAP = 4   # pixels left free between neighbouring items, so selections never touch

    def fixed_item_size(self) -> QSize:
        """Every item's size (see FileDelegate.sizeHint): its grid cell, minus a small gap.
        Names that don't fit are shortened with "…"."""
        cell = self.gridSize()
        return QSize(max(16, cell.width() - self.GAP), max(16, cell.height() - self.GAP // 2))


class IconView(_GridBase):
    ZOOM_SIZES = [32, 48, 64, 80, 96, 128, 160, 192, 256]
    mode = "icons"

    def __init__(self, ctx, host, zoom: int = 2, parent=None) -> None:
        super().__init__(ctx, host, zoom, parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSpacing(0)
        self.apply_zoom()

    def cell_size(self) -> QSize:
        line = QFontMetrics(self.font()).lineSpacing()
        icon = self.icon_px
        return QSize(max(icon + 28, 100), icon + 14 + 2 * line + 6)   # room for two lines of name

    def configure_thumbnails(self) -> None:
        if self.model() is not None:
            on = self.ctx.settings.get("view.thumbnails", True) and self.icon_px >= 48
            self.model().thumbnail_size = self.icon_px if on else 0


class ListView(_GridBase):
    ZOOM_SIZES = [16, 20, 24, 32, 40]
    mode = "list"

    def __init__(self, ctx, host, zoom: int = 1, parent=None) -> None:
        super().__init__(ctx, host, zoom, parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.apply_zoom()

    def cell_size(self) -> QSize:
        line = QFontMetrics(self.font()).lineSpacing()
        width = int(self.ctx.settings.get("view.list_cell_width", 240))
        return QSize(width, max(self.icon_px, line) + 8)

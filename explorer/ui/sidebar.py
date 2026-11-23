"""
Sidebar - the user's own categories of places (see core/categories.py).

* Click an item to open it, middle-click to open it in a new tab.
* Right-click for: new/rename/delete category, rename/remove/move items,
  add the current folder, a drive, or a network location.
* Drag items to reorder them or move them into another category.
* Drag folders from a file view (or another app) onto a category to add
  them, or onto an item to copy/move files into that place.

It only DISPLAYS the categories; every change goes through ctx.categories,
which saves and announces CATEGORIES_CHANGED, and then we rebuild.

Live sections (categories with "auto": drives | network | cloud) also show
what is plugged in, mounted or synced right now, from ctx.devices. Those rows
are rebuilt on DEVICES_CHANGED; right-click one to hide it, or drag it into
another category to keep a permanent shortcut.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QBrush, QDrag, QFont
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QMenu, QTreeWidget, QTreeWidgetItem

from ..core.events import Topics
from ..core.categories import AUTO_SECTIONS
from ..core.platform_info import user_dirs
from . import dialogs
from .models.file_model import INTERNAL_MIME, paths_from_mime

ITEM_MIME = "application/x-pyexplorer-sidebar-item"
KIND_ROLE = Qt.ItemDataRole.UserRole          # ("cat", cat id) | ("item", item id) | ("live", "cat id\npath")
LIVE_PREFIX = "live\n"                        # ITEM_MIME payload for a dragged live entry


class Sidebar(QTreeWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Sidebar")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(10)
        # (no setUniformRowHeights: it would measure rows before the theme's spacing applies)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.itemClicked.connect(self._on_clicked)
        self.itemExpanded.connect(lambda item: self._on_collapse(item, False))
        self.itemCollapsed.connect(lambda item: self._on_collapse(item, True))
        bus = ctx.bus
        self._unsubs = [bus.subscribe(Topics.CATEGORIES_CHANGED, self.rebuild),
                        bus.subscribe(Topics.THEME_CHANGED, lambda **_: self.rebuild()),
                        bus.subscribe(Topics.FOLDER_COLORS_CHANGED, lambda **_: self.rebuild()),
                        bus.subscribe(Topics.DEVICES_CHANGED, lambda **_: self.rebuild()),
                        bus.subscribe(Topics.ACTIVE_LOCATION_CHANGED, self._highlight)]
        self.destroyed.connect(lambda *_: [u() for u in self._unsubs])
        self._rebuilding = False
        self.rebuild()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            # Row sizes are measured before the theme's spacing reaches us; measure again.
            self._shown_once = True
            self.rebuild()

    # -- building --------------------------------------------------------------
    def rebuild(self, **_) -> None:
        self._rebuilding = True
        scroll = self.verticalScrollBar().value()
        self.clear()
        bold = QFont(self.font())
        bold.setBold(True)
        bold.setPointSizeF(max(6.0, bold.pointSizeF() * 0.9))
        for cat in self.ctx.categories.all():
            live = self.live_entries(cat)
            if cat.get("auto") == "cloud" and not cat["items"] and not live:
                continue                       # no cloud storage on this computer: don't show an empty header
            top = QTreeWidgetItem([cat["name"].upper()])
            top.setData(0, KIND_ROLE, ("cat", cat["id"]))
            top.setFont(0, bold)
            top.setForeground(0, self.palette().placeholderText())
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            top.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
            self.addTopLevelItem(top)
            for item in cat["items"]:
                child = QTreeWidgetItem([item["label"]])
                child.setData(0, KIND_ROLE, ("item", item["id"]))
                child.setIcon(0, self.ctx.icons.for_kind(item.get("kind", "folder"), item["path"]))
                child.setToolTip(0, item["path"])
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                               Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
                top.addChild(child)
            for dev in live:
                top.addChild(self._live_row(cat["id"], dev))
            top.setExpanded(not cat.get("collapsed"))
        self._rebuilding = False
        self.verticalScrollBar().setValue(scroll)
        tab = self.ctx.commands.active_tab() if self.ctx.main else None
        if tab:
            self._highlight(tab.path)

    def live_entries(self, cat: dict) -> list:
        """The plugged-in / mounted / synced things this live section shows now."""
        section = cat.get("auto")
        if not section or self.ctx.devices is None:
            return []
        hidden = set(cat.get("hidden") or ())
        own = {i["path"] for i in cat["items"]}         # the user's own shortcut wins over the live row
        return [d for d in self.ctx.devices.devices
                if d.section == section and d.path not in hidden and d.path not in own]

    def _live_row(self, cat_id: str, dev) -> QTreeWidgetItem:
        row = QTreeWidgetItem([dev.label])
        row.setData(0, KIND_ROLE, ("live", f"{cat_id}\n{dev.path}"))
        icons = self.ctx.icons
        row.setIcon(0, icons.cloud_app(dev.cloud_root) if dev.section == "cloud" else icons.special(dev.kind))
        tip = dev.path if not dev.detail else f"{dev.path}\n{dev.detail}"
        row.setToolTip(0, tip)
        if not dev.mounted or dev.running is False:     # fstab share not mounted / sync app not running
            row.setForeground(0, QBrush(self.palette().placeholderText()))
        row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                     Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        return row

    def _highlight(self, path: str = "", **_) -> None:
        """Select the sidebar item matching the current folder (if any)."""
        target = path.rstrip("/\\")
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                found = self._item_path(child)
                if found is not None and found.rstrip("/\\") == target:
                    self.setCurrentItem(child)
                    return
        self.clearSelection()

    def _find(self, key) -> Optional[QTreeWidgetItem]:
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if top.data(0, KIND_ROLE) == key:
                return top
            for j in range(top.childCount()):
                if top.child(j).data(0, KIND_ROLE) == key:
                    return top.child(j)
        return None

    # -- helpers -----------------------------------------------------------------
    @staticmethod
    def _key(item: Optional[QTreeWidgetItem]) -> Tuple[str, str]:
        data = item.data(0, KIND_ROLE) if item is not None else None
        return tuple(data) if data else ("", "")

    def _item_path(self, item) -> Optional[str]:
        kind, ident = self._key(item)
        if kind == "live":
            return ident.split("\n", 1)[1]
        if kind != "item":
            return None
        _, data = self.ctx.categories.find_item(ident)
        return data["path"] if data else None

    def _category_of(self, item) -> Optional[str]:
        kind, ident = self._key(item)
        if kind == "cat":
            return ident
        if kind == "item":
            cat, _ = self.ctx.categories.find_item(ident)
            return cat["id"] if cat else None
        if kind == "live":
            return ident.split("\n", 1)[0]
        return None

    def _device(self, path: str):
        return next((d for d in self.ctx.devices.devices if d.path == path), None) if self.ctx.devices else None

    # -- events ------------------------------------------------------------------
    def _on_clicked(self, item, column) -> None:
        kind, ident = self._key(item)
        if kind == "cat":
            item.setExpanded(not item.isExpanded())
            return
        path = self._item_path(item)
        if path:
            self.ctx.commands.open_location(path, new_tab=False)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            path = self._item_path(self.itemAt(event.position().toPoint()))
            if path:
                self.ctx.commands.open_location(path, new_tab=True)
            return
        super().mouseReleaseEvent(event)

    def _on_collapse(self, item, collapsed: bool) -> None:
        kind, ident = self._key(item)
        if kind == "cat" and not self._rebuilding:
            self.ctx.categories.set_collapsed(ident, collapsed)

    # -- drag & drop ---------------------------------------------------------------
    def startDrag(self, actions) -> None:
        item = self.currentItem()
        kind, ident = self._key(item)
        if kind not in ("item", "live"):
            return
        mime = QMimeData()
        payload = ident if kind == "item" else LIVE_PREFIX + self._item_path(item)
        mime.setData(ITEM_MIME, payload.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon(0).pixmap(24, 24))
        drag.exec(Qt.DropAction.MoveAction)

    @staticmethod
    def _wanted(mime) -> bool:
        return mime.hasFormat(ITEM_MIME) or mime.hasFormat(INTERNAL_MIME) or mime.hasUrls()

    def dragEnterEvent(self, event) -> None:
        if self._wanted(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._wanted(event.mimeData()):
            event.ignore()
            return
        super().dragMoveEvent(event)              # draws the drop indicator
        event.acceptProposedAction()              # anywhere is fine: empty space adds to the last category

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        pos = event.position().toPoint()
        target = self.itemAt(pos)
        kind, ident = self._key(target)
        cats = self.ctx.categories
        self.setState(QAbstractItemView.State.NoState)
        if mime.hasFormat(ITEM_MIME):                       # reorder sidebar items
            item_id = bytes(mime.data(ITEM_MIME)).decode()
            if item_id.startswith(LIVE_PREFIX):             # a live entry: keep a permanent shortcut to it
                self._pin_live(item_id[len(LIVE_PREFIX):], self._category_of(target))
            elif kind == "cat":
                cats.move_item(item_id, ident, 0)
            elif kind == "item":
                cat, data = cats.find_item(ident)
                if cat:
                    index = cat["items"].index(data)
                    rect = self.visualItemRect(target)
                    if pos.y() > rect.center().y():
                        index += 1
                    cats.move_item(item_id, cat["id"], index)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        paths = paths_from_mime(mime)
        if not paths:
            event.ignore()
            return
        if kind in ("item", "live"):                        # onto a place: copy/move files there
            dest = self._item_path(target)
            mods = event.modifiers()
            op = "copy" if mods & Qt.KeyboardModifier.ControlModifier else \
                "move" if mods & Qt.KeyboardModifier.ShiftModifier else "auto"
            if dest:
                self.ctx.commands.drop_files(paths, dest, op)
        else:                                               # onto a category / empty space: add shortcuts
            cat_id = ident if kind == "cat" else None
            if cat_id is None:
                all_cats = cats.all()
                cat_id = all_cats[-1]["id"] if all_cats else cats.add_category("Favourites")
            self.ctx.commands.add_paths_to_category(paths, cat_id)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    # -- context menu ------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        kind, ident = self._key(item)
        cats = self.ctx.categories
        cmds = self.ctx.commands
        menu = QMenu(self)
        cat_id = self._category_of(item)

        if kind == "item":
            path = self._item_path(item)
            menu.addAction("Open").triggered.connect(lambda: cmds.open_location(path, False))
            menu.addAction("Open in new tab").triggered.connect(lambda: cmds.open_location(path, True))
            if path and self.ctx.vfs.is_remote(path):
                menu.addSeparator()
                menu.addAction("Edit connection…").triggered.connect(lambda: cmds.edit_connection_for(path))
                menu.addAction("Disconnect").triggered.connect(lambda: cmds.disconnect(path))
                menu.addAction("Forget saved login").triggered.connect(lambda: cmds.forget_login_for(path))
            menu.addSeparator()
            menu.addAction("Rename…").triggered.connect(lambda: self._rename_item(ident))
            move = menu.addMenu("Move to category")
            for cat in cats.all():
                move.addAction(cat["name"]).triggered.connect(lambda _=False, c=cat["id"]: cats.move_item(ident, c))
            menu.addAction("Move up").triggered.connect(lambda: cats.shift_item(ident, -1))
            menu.addAction("Move down").triggered.connect(lambda: cats.shift_item(ident, 1))
            menu.addSeparator()
            menu.addAction("Remove from sidebar").triggered.connect(lambda: cats.remove_item(ident))
            menu.addSeparator()

        if kind == "live":                          # something plugged in / mounted / synced right now
            path = self._item_path(item)
            menu.addAction("Open").triggered.connect(lambda: cmds.open_location(path, False))
            menu.addAction("Open in new tab").triggered.connect(lambda: cmds.open_location(path, True))
            menu.addSeparator()
            keep = menu.addMenu("Keep a shortcut in")
            for cat in cats.all():
                keep.addAction(cat["name"]).triggered.connect(
                    lambda _=False, c=cat["id"]: self._pin_live(path, c))
            menu.addAction("Hide from sidebar").triggered.connect(lambda: cats.hide_live(cat_id, path))
            menu.addSeparator()

        if cat_id:
            add = menu.addMenu("Add to this category")
            tab = cmds.active_tab()
            if tab and tab.current_folder:
                add.addAction("Current folder").triggered.connect(
                    lambda: cmds.add_paths_to_category([tab.current_folder], cat_id))
            add.addAction("Folder…").triggered.connect(lambda: self._add_folder_dialog(cat_id))
            drives = add.addMenu("Drive")
            drives.addAction(self.ctx.icons.special("computer"), "Computer").triggered.connect(
                lambda: cats.add_item(cat_id, "computer://", "Computer", "computer"))
            for dev in (self.ctx.devices.devices if self.ctx.devices else []):
                if dev.section == "cloud":
                    continue
                drives.addAction(self.ctx.icons.special(dev.kind), dev.label).triggered.connect(
                    lambda _=False, d=dev: cats.add_item(cat_id, d.path, d.label, d.kind))
            if self.ctx.cloud.roots:
                clouds = add.addMenu("Cloud storage")
                for root in self.ctx.cloud.roots:
                    clouds.addAction(self.ctx.icons.cloud_app(root), root.label).triggered.connect(
                        lambda _=False, r=root: cats.add_item(cat_id, r.path, r.label, "cloud"))
            places = add.addMenu("Standard folder")
            for label, path in user_dirs():
                places.addAction(label).triggered.connect(
                    lambda _=False, l=label, p=path: cats.add_item(cat_id, p, l, "folder"))
            net = add.addMenu("Network location")
            for conn in self.ctx.connections.all():
                net.addAction(conn.get("name") or conn["host"]).triggered.connect(
                    lambda _=False, c=conn: cats.add_item(cat_id, self.ctx.connections.root_uri(c),
                                                          c.get("name") or c["host"], "network"))
            net.addSeparator()
            net.addAction("New connection…").triggered.connect(lambda: cmds.new_connection(cat_id))
            menu.addSeparator()
        cat_data = cats.find_category(cat_id) if cat_id else None
        if cat_data is not None and cat_data.get("hidden"):
            count = len(cat_data["hidden"])
            menu.addAction(f"Show hidden entries again ({count})").triggered.connect(
                lambda: cats.unhide_all(cat_id))
            menu.addSeparator()
        if kind == "cat":
            menu.addAction("Rename category…").triggered.connect(lambda: self._rename_category(ident))
            menu.addAction("Move category up").triggered.connect(lambda: cats.move_category(ident, -1))
            menu.addAction("Move category down").triggered.connect(lambda: cats.move_category(ident, 1))
            menu.addAction("Delete category").triggered.connect(lambda: self._delete_category(ident))
            menu.addSeparator()
        menu.addAction("New category…").triggered.connect(self.new_category)
        missing = [s for s in AUTO_SECTIONS if s not in cats.auto_sections()]
        if missing:                                  # a live section was deleted: offer it back
            back = menu.addMenu("Add live section")
            for section in missing:
                back.addAction(AUTO_SECTIONS[section]).triggered.connect(
                    lambda _=False, s=section: cats.add_category(AUTO_SECTIONS[s], auto=s))
        menu.exec(event.globalPos())

    # -- actions -----------------------------------------------------------------
    def _pin_live(self, path: str, cat_id: Optional[str]) -> None:
        """Copy a live entry into a category as a normal (permanent) shortcut."""
        dev = self._device(path)
        if dev is None or cat_id is None:
            return
        cat = self.ctx.categories.find_category(cat_id)
        if cat is not None and not any(i["path"] == path for i in cat["items"]):
            self.ctx.categories.add_item(cat_id, dev.path, dev.label, dev.kind)

    def new_category(self) -> None:
        name = dialogs.ask_string(self, "New category", "Category name:", "New category")
        if name:
            self.ctx.categories.add_category(name)

    def _rename_category(self, cat_id: str) -> None:
        cat = self.ctx.categories.find_category(cat_id)
        if cat:
            name = dialogs.ask_string(self, "Rename category", "Category name:", cat["name"])
            if name:
                self.ctx.categories.rename_category(cat_id, name)

    def _delete_category(self, cat_id: str) -> None:
        cat = self.ctx.categories.find_category(cat_id)
        if cat and dialogs.confirm(self, "Delete category",
                                   f"Delete the category '{cat['name']}' and its {len(cat['items'])} shortcut(s)?\n"
                                   "(Only the sidebar shortcuts are removed - no files are touched.)", "Delete"):
            self.ctx.categories.delete_category(cat_id)

    def _rename_item(self, item_id: str) -> None:
        _, item = self.ctx.categories.find_item(item_id)
        if item:
            label = dialogs.ask_string(self, "Rename shortcut", "Label:", item["label"])
            if label:
                self.ctx.categories.rename_item(item_id, label)

    def _add_folder_dialog(self, cat_id: str) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add folder to sidebar")
        if path:
            self.ctx.commands.add_paths_to_category([path], cat_id)

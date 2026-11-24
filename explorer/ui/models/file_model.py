"""
FileModel - shows a list of FileEntry objects to Qt's views.

ONE model serves all four views:
  * Details / Icons / List show only the top-level rows.
  * Tree view turns on ``tree_enabled``: folders then report children, which
    are loaded in the background the first time a folder is expanded.

The model never reads the disk and never sorts. The tab gives it a finished,
sorted, filtered list (``set_entries``) and a ``loader`` for sub-folders. It
also never renames or moves files itself: it calls handlers the tab provides.

How rows are addressed (the technical bit)
------------------------------------------
Qt identifies a row by (row, column, internalId). We use internalId as a
"group id": 0 = the top-level list; every expanded folder's children form
another group with its own id. ``_groups[gid]`` remembers each group's
entries and where its parent sits. Groups are rebuilt lazily after every
reset, so a 100,000-row folder costs no per-row Python objects at all.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtGui import QColor

from ...core import cloud
from ...core.fileinfo import FileEntry
from ...core.formatting import human_size, human_time

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1          # data(index, ENTRY_ROLE) -> FileEntry
PATH_ROLE = Qt.ItemDataRole.UserRole + 2
INTERNAL_MIME = "application/x-pyexplorer-paths"

COLUMN_TITLES = {"name": "Name", "size": "Size", "type": "Type", "modified": "Modified",
                 "status": "Status", "location": "Location", "match": "Matching line"}

R = Qt.ItemDataRole


class _Group:
    __slots__ = ("entries", "parent_gid", "parent_row", "path")

    def __init__(self, path: str, entries: List[FileEntry], parent_gid: int, parent_row: int) -> None:
        self.path = path
        self.entries = entries
        self.parent_gid = parent_gid
        self.parent_row = parent_row


def paths_to_mime(paths: List[str]) -> QMimeData:
    """Drag/clipboard data: our own list of paths + standard file URLs other apps understand."""
    mime = QMimeData()
    mime.setData(INTERNAL_MIME, "\n".join(paths).encode("utf-8"))
    mime.setUrls([QUrl(p) if "://" in p else QUrl.fromLocalFile(p) for p in paths])
    mime.setText("\n".join(paths))
    return mime


def paths_from_mime(mime: QMimeData) -> List[str]:
    if mime.hasFormat(INTERNAL_MIME):
        return [p for p in bytes(mime.data(INTERNAL_MIME)).decode("utf-8").split("\n") if p]
    out = []
    for url in mime.urls():
        if url.isLocalFile():
            out.append(url.toLocalFile())
        elif url.scheme() in ("sftp", "ftp", "ftps"):
            out.append(url.toString())
    return out


class FileModel(QAbstractItemModel):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.columns: List[str] = ["name", "size", "type", "modified"]
        self.tree_enabled = False
        self.thumbnail_size = 0           # >0: show image thumbnails at this size
        self.loader: Optional[Callable[[str, Callable[[List[FileEntry]], None]], None]] = None
        self.prepare: Callable[[List[FileEntry]], List[FileEntry]] = lambda entries: entries
        self.rename_handler: Optional[Callable[[FileEntry, str], None]] = None
        self._top: List[FileEntry] = []
        self._cloud_roots: Dict[str, object] = {}    # folder -> CloudRoot (or None), cached lookups
        self._children: Dict[str, List[FileEntry]] = {}   # raw loaded children, by folder path
        self._loading: set = set()
        self._reset_lookups()

    # ==================================================================
    # Feeding data
    # ==================================================================
    def _reset_lookups(self) -> None:
        self._groups: Dict[int, _Group] = {}
        self._group_of_path: Dict[str, int] = {}
        self._rows: Optional[Dict[str, Tuple[int, int]]] = None   # path -> (gid, row), built on demand
        self._flag_cache: Dict[tuple, list] = {}
        self._next_gid = 1

    def set_entries(self, entries: List[FileEntry]) -> None:
        self.beginResetModel()
        self._top = entries
        self._cloud_roots.clear()
        self._reset_lookups()
        self.endResetModel()

    def _top_flags(self, column0: bool):
        """Flags of every top-level row, computed once per reset (Qt asks a LOT)."""
        cache = self._flag_cache
        key = (column0, self.tree_enabled)
        flags = cache.get(key)
        if flags is None:
            flags = cache[key] = [self._compute_flags(e, column0) for e in self._top]
        return flags

    def refresh_layout(self) -> None:
        """Re-run ``prepare`` on loaded children (sorting/filter changed)."""
        self.set_entries(self._top)

    def clear_children(self) -> None:
        """Forget loaded sub-folders (the tab navigated somewhere else)."""
        self._children.clear()
        self._loading.clear()

    def set_columns(self, columns: List[str]) -> None:
        if columns != self.columns:
            self.beginResetModel()
            self.columns = list(columns)
            self._reset_lookups()
            self.endResetModel()

    def set_tree_enabled(self, enabled: bool) -> None:
        if enabled != self.tree_enabled:
            self.beginResetModel()
            self.tree_enabled = enabled
            self._reset_lookups()
            self.endResetModel()

    def top_entries(self) -> List[FileEntry]:
        return self._top

    def loaded_folders(self) -> List[str]:
        return list(self._children)

    # ==================================================================
    # Looking things up
    # ==================================================================
    def entry(self, index: QModelIndex) -> Optional[FileEntry]:
        if not index.isValid():
            return None
        gid = index.internalId()
        entries = self._top if gid == 0 else (self._groups[gid].entries if gid in self._groups else None)
        if entries is None or index.row() >= len(entries):
            return None
        return entries[index.row()]

    def _build_rows(self) -> Dict[str, Tuple[int, int]]:
        if self._rows is None:
            rows = {e.path: (0, i) for i, e in enumerate(self._top)}
            for gid, g in self._groups.items():
                for i, e in enumerate(g.entries):
                    rows[e.path] = (gid, i)
            self._rows = rows
        return self._rows

    def index_for_path(self, path: str, column: int = 0) -> QModelIndex:
        found = self._build_rows().get(path)
        if found is None and self.tree_enabled:
            # Maybe it is inside a loaded sub-folder whose group isn't built yet.
            parent_path = self._parent_path(path)
            if parent_path and parent_path != path and parent_path in self._children:
                parent = self.index_for_path(parent_path)
                if parent.isValid():
                    self.rowCount(parent)            # builds the group
                    found = self._build_rows().get(path)
        if found is None:
            return QModelIndex()
        gid, row = found
        return self.createIndex(row, column, gid)

    def _parent_path(self, path: str) -> Optional[str]:
        try:
            return self.ctx.vfs.resolve(path).parent(path)
        except OSError:
            return None

    def refresh_path(self, path: str) -> None:
        """Ask views to repaint one row (e.g. its thumbnail arrived)."""
        index = self.index_for_path(path)
        if index.isValid():
            self.dataChanged.emit(index, index, [R.DecorationRole])

    # ==================================================================
    # Tree structure (QAbstractItemModel interface)
    # ==================================================================
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if column < 0 or column >= len(self.columns) or row < 0:
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, 0) if row < len(self._top) else QModelIndex()
        gid = self._group_for(parent)
        if gid is None or row >= len(self._groups[gid].entries):
            return QModelIndex()
        return self.createIndex(row, column, gid)

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:  # noqa: A003
        if not index.isValid():
            return QModelIndex()
        gid = index.internalId()
        group = self._groups.get(gid)
        if gid == 0 or group is None:
            return QModelIndex()
        return self.createIndex(group.parent_row, 0, group.parent_gid)

    def _group_for(self, parent: QModelIndex) -> Optional[int]:
        """Group id holding the children of ``parent`` (created on first use)."""
        entry = self.entry(parent)
        if entry is None or not entry.is_dir or entry.path not in self._children:
            return None
        gid = self._group_of_path.get(entry.path)
        if gid is None:
            gid = self._next_gid
            self._next_gid += 1
            parent_gid = parent.internalId()
            self._groups[gid] = _Group(entry.path, self.prepare(self._children[entry.path]), parent_gid, parent.row())
            self._group_of_path[entry.path] = gid
            self._rows = None
        return gid

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._top)
        if not self.tree_enabled or parent.column() > 0:
            return 0
        gid = self._group_for(parent)
        return len(self._groups[gid].entries) if gid is not None else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.columns)

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return True
        if not self.tree_enabled or parent.column() > 0:
            return False
        entry = self.entry(parent)
        if entry is None or not entry.is_dir or (entry.extra and entry.extra.get("kind") == "computer"):
            return False
        if entry.path in self._children:
            return self.rowCount(parent) > 0
        return True                          # not loaded yet: show the expand arrow

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid() or not self.tree_enabled:
            return False
        entry = self.entry(parent)
        return bool(entry and entry.is_dir and entry.path not in self._children and entry.path not in self._loading)

    def fetchMore(self, parent: QModelIndex) -> None:
        entry = self.entry(parent)
        if entry is None or self.loader is None:
            return
        path = entry.path
        self._loading.add(path)
        self.loader(path, lambda entries, p=path: self.set_children(p, entries))

    def set_children(self, path: str, entries: Optional[List[FileEntry]]) -> None:
        """A sub-folder's contents arrived (or changed)."""
        self._loading.discard(path)
        entries = list(entries or [])
        if path in self._children:
            # Already shown: simplest correct update is a reset (views keep their state, see views/base.py).
            self._children[path] = entries
            self.set_entries(self._top)
            return
        parent = self.index_for_path(path)
        if not parent.isValid():
            self._children[path] = entries
            return
        prepared = self.prepare(entries)
        if prepared:
            self.beginInsertRows(parent, 0, len(prepared) - 1)
        self._children[path] = entries
        self._group_for(parent)
        if prepared:
            self.endInsertRows()
        else:
            self.dataChanged.emit(parent, parent)     # arrow disappears for an empty folder

    def reload_children(self, path: Optional[str] = None) -> None:
        """Reload one (or every) loaded sub-folder in the background."""
        if self.loader is None:
            return
        for p in ([path] if path else list(self._children)):
            if p in self._children:
                self.loader(p, lambda entries, p=p: self.set_children(p, entries))

    # ==================================================================
    # Cloud state (OneDrive etc.) - see core/cloud.py
    # ==================================================================
    def cloud_info(self, entry: FileEntry):
        """(state, CloudRoot) for an entry; state is cloud.NONE outside cloud folders."""
        if entry.extra and entry.extra.get("kind"):
            return cloud.NONE, None
        folder = entry.parent or ""
        if folder in self._cloud_roots:
            root = self._cloud_roots[folder]
        else:
            root = self._cloud_roots[folder] = self.ctx.cloud.find(entry.path)
        if root is None and not entry.cloud:
            return cloud.NONE, None
        reports_local = root.reports_local if root is not None else False
        return cloud.effective_state(entry.cloud, root is not None, reports_local), root

    # ==================================================================
    # Cell contents
    # ==================================================================
    def data(self, index: QModelIndex, role: int = R.DisplayRole):
        entry = self.entry(index)
        if entry is None:
            return None
        col = self.columns[index.column()]
        if role == R.DisplayRole:
            if col == "name":
                return entry.name
            if col == "size":
                if entry.parent == "computer://":            # Computer view: free space of each drive
                    usage = self.ctx.drive_usage.get(entry.path) if self.ctx.drive_usage else None
                    return f"{human_size(usage.free)} free" if usage is not None else ""
                return "" if entry.is_dir else human_size(entry.size)
            if col == "type":
                return entry.type_label
            if col == "modified":
                return human_time(entry.mtime)
            if col == "location":
                return entry.parent
            if col == "match":                         # Search tab: the line that contains the text
                return (entry.extra or {}).get("snippet", "")
            if col == "status":
                state, root = self.cloud_info(entry)
                if state == cloud.NONE:
                    return ""
                return f"{root.provider if root else 'Cloud'} · {cloud.SHORT_LABELS[state]}"
            return None
        if role == R.DecorationRole and col == "name":
            state, root = self.cloud_info(entry)
            icon, key = None, None
            # Thumbnails read the file, which would DOWNLOAD a cloud-only file: skip those.
            if self.thumbnail_size and not entry.is_dir and "://" not in entry.path and \
                    entry.ext in self.ctx.thumbnails.extensions and state != cloud.ONLINE:
                icon = self.ctx.thumbnails.get(entry.path, entry.mtime, self.thumbnail_size, entry.size,
                                               lambda p=entry.path: self.refresh_path(p))
                key = ("thumb", entry.path, entry.mtime, self.thumbnail_size)
            if icon is None:
                icon = self.ctx.icons.for_entry(entry)
                key = None
            if state == cloud.NONE:
                return icon
            return self.ctx.icons.with_cloud_badge(icon, key or self.ctx.icons.icon_key(entry), root, state)
        if role == R.EditRole and col == "name":
            return entry.name
        if role == ENTRY_ROLE:
            return entry
        if role == PATH_ROLE:
            return entry.path
        if role == R.TextAlignmentRole:
            if col == "size":
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return None
        if role == R.ForegroundRole:
            if entry.hidden or self.ctx.clipboard.is_cut(entry.path):
                return QColor(self.ctx.theme.palette.get("fg_dim", "#888888"))
            if col in ("size", "type", "modified", "location", "status", "match"):
                return QColor(self.ctx.theme.palette.get("fg_dim", "#888888"))
            return None
        if role == R.ToolTipRole and col == "name":
            lines = [entry.name]
            if not entry.is_dir:
                lines.append(human_size(entry.size))
            if entry.mtime:
                lines.append("Modified " + human_time(entry.mtime))
            state, root = self.cloud_info(entry)
            if state != cloud.NONE:
                lines.append(f"{root.provider if root else 'Cloud'} — {cloud.LABELS[state].lower()}")
            if entry.parent == "computer://" and self.ctx.drive_usage:
                usage = self.ctx.drive_usage.get(entry.path)
                if usage is not None:
                    lines.append(usage.tooltip)
            return "\n".join(lines)
        return None

    def headerData(self, section: int, orientation, role: int = R.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            if role == R.DisplayRole:
                return COLUMN_TITLES.get(self.columns[section], self.columns[section])
            if role == R.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def flags(self, index: QModelIndex):
        # Called for every row whenever Qt lays out a view, so it must be FAST:
        # top-level rows use a precomputed list; combinations come from _FLAGS.
        if not index.isValid():
            return _FLAGS["root"]
        if index.internalId() == 0:
            flags = self._top_flags(index.column() == 0)
            row = index.row()
            return flags[row] if row < len(flags) else _FLAGS["root"]
        entry = self.entry(index)
        return _FLAGS["root"] if entry is None else self._compute_flags(entry, index.column() == 0)

    def _compute_flags(self, entry: FileEntry, name_col: bool):
        kind = entry.extra.get("kind") if entry.extra else None
        if kind:     # drives, shares, "Computer": can't be renamed or dragged, but can be dropped on
            return _FLAGS["special_leaf" if (kind == "computer" or not self.tree_enabled) else "special"]
        if entry.is_dir:
            if self.tree_enabled:
                return _FLAGS["dir_name" if name_col else "dir"]
            return _FLAGS["flat_dir_name" if name_col else "flat_dir"]
        return _FLAGS["file_name" if name_col else "file"]

    def setData(self, index: QModelIndex, value, role: int = R.EditRole) -> bool:
        """Inline rename finished. The actual rename happens in the background."""
        entry = self.entry(index)
        if role != R.EditRole or entry is None or self.rename_handler is None:
            return False
        new = str(value).strip()
        if new and new != entry.name:
            self.rename_handler(entry, new)
        return False                     # the refreshed listing will show the new name

    # -- drag & drop -----------------------------------------------------------
    def mimeTypes(self) -> List[str]:
        return [INTERNAL_MIME, "text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:
        seen, paths = set(), []
        for index in indexes:
            entry = self.entry(index)
            if entry is not None and entry.path not in seen:
                seen.add(entry.path)
                paths.append(entry.path)
        return paths_to_mime(paths)

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        return data.hasFormat(INTERNAL_MIME) or data.hasUrls()


def _make_flags() -> dict:
    F = Qt.ItemFlag
    base = F.ItemIsEnabled | F.ItemIsSelectable
    never = F.ItemNeverHasChildren          # lets Qt skip asking hasChildren() for each row
    drag = F.ItemIsDragEnabled
    drop = F.ItemIsDropEnabled
    edit = F.ItemIsEditable
    return {
        "root": drop,
        "special": base | drop,
        "special_leaf": base | drop | never,
        "dir": base | drag | drop,
        "dir_name": base | drag | drop | edit,
        "flat_dir": base | drag | drop | never,
        "flat_dir_name": base | drag | drop | edit | never,
        "file": base | drag | never,
        "file_name": base | drag | edit | never,
    }


_FLAGS = _make_flags()

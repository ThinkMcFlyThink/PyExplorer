"""
InfoPanel - details about the selected item, on the right (Alt+P).

Speed rules:
  * Hidden = idle. While the panel is closed it ignores every selection change,
    and nothing in services/metadata is even imported until it first opens.
  * What we already know (name, size, date) is drawn at once. The details
    (ffprobe, reading the EPUB...) come from a worker after a short pause, so
    holding an arrow key through a folder of videos starts no programs.
  * Moving on cancels the work for the previous file - external programs
    are killed - and late results are dropped.
  * Results and pictures are cached by (path, modified time, size), so going
    back to an item is instant.

The details themselves come from services/metadata (one provider per family
of files); images and PDFs from ui/info_providers.py. This file only draws.
"""
from __future__ import annotations

import html
import re
import time
from collections import OrderedDict
from typing import List, Optional

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import cloud
from ..core.events import Topics
from ..core.fileinfo import FileEntry
from ..core.formatting import human_size, human_time
from ..core.tasks import CancelToken

DETAILS_DELAY_MS = 120          # pause before reading details (arrow-key scrolling stays light)
CACHE_SIZE = 96
PREVIEW_CACHE_SIZE = 24
PREVIEW_MAX_HEIGHT = 420
CATEGORY_NAMES = {"folder": "Folders", "image": "Images", "video": "Videos", "audio": "Audio", "archive": "Archives",
                  "code": "Code", "document": "Documents", "text": "Text", "executable": "Programs", "other": "Other"}
_LONG_TOKEN = re.compile(r"\S{26,}")


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _breakable(text: str) -> str:
    """Let long paths / hashes / URLs wrap inside the panel (zero-width spaces)."""
    def split(m):
        token = m.group(0)
        token = re.sub(r"([/\\_\-.,:&?=+])", "\\1\u200b", token)
        return re.sub(r"([^\u200b]{20})(?=[^\u200b])", "\\1\u200b", token)
    return _LONG_TOKEN.sub(split, text)


def decode_preview(spec, max_px: int, token=None) -> Optional[QImage]:
    """Worker thread: a preview spec (bytes / path / callable(token) / QImage) -> a QImage no bigger than max_px."""
    if callable(spec):
        spec = spec(token)
    if spec is None:
        return None
    if isinstance(spec, QImage):
        image = spec
    else:
        buf = None
        if isinstance(spec, (bytes, bytearray)):
            buf = QBuffer()
            buf.setData(QByteArray(bytes(spec)))
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            reader = QImageReader(buf)
        else:
            reader = QImageReader(str(spec))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > max_px or size.height() > max_px):
            reader.setScaledSize(size.scaled(QSize(max_px, max_px), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        del buf
    if image.isNull():
        return None
    if image.width() > max_px or image.height() > max_px:
        image = image.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


class _Preview(QLabel):
    """A picture that always fits the panel's width."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="InfoPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._image: Optional[QImage] = None
        self.hide()

    def set_image(self, image: Optional[QImage]) -> None:
        self._image = image
        self.setVisible(image is not None)
        self._fit()

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        if self._image is not None and abs(event.size().width() - event.oldSize().width()) > 1:
            self._fit()

    def _fit(self) -> None:
        if self._image is None:
            self.clear()
            return
        dpr = self.devicePixelRatioF()
        w = max(self.width(), 60)
        img = self._image
        scale = min(w / img.width(), PREVIEW_MAX_HEIGHT / img.height(), 1.0)
        tw, th = max(1, int(img.width() * scale)), max(1, int(img.height() * scale))
        pm = QPixmap.fromImage(img.scaled(int(tw * dpr), int(th * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
        self.setPixmap(pm)
        self.setFixedHeight(th + 4)


class InfoPanel(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent, objectName="InfoPanel")
        self.ctx = ctx
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.active = False
        self._key = None                    # what is shown now
        self._entry: Optional[FileEntry] = None
        self._entries: List[FileEntry] = []
        self._info = None                   # the shown metadata.Info (for Copy and re-drawing)
        self._token: Optional[CancelToken] = None
        self._action_token: Optional[CancelToken] = None
        self._cache: "OrderedDict[tuple, object]" = OrderedDict()
        self._previews: "OrderedDict[tuple, Optional[QImage]]" = OrderedDict()
        self._font_id = -1
        self._loaded_providers = False
        self._pending = None                # (key, entry, fs, deep, this_folder) waiting for the details timer

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget(self, objectName="InfoBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 2, 4, 2)
        row.addWidget(QLabel("Info", bar, objectName="Dim"), 1)
        self.copy_button = QToolButton(bar, toolTip="Copy all details as text", autoRaise=True)
        self.copy_button.setIcon(ctx.icons.symbol("copy", "edit-copy"))
        self.copy_button.clicked.connect(self.copy_details)
        row.addWidget(self.copy_button)
        close = QToolButton(bar, toolTip="Close the info panel", autoRaise=True)
        close.setIcon(ctx.icons.symbol("close", "window-close"))
        close.clicked.connect(lambda: ctx.main.set_info_visible(False))
        row.addWidget(close)
        layout.addWidget(bar)

        self.scroll = QScrollArea(self, objectName="InfoScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="InfoBody")
        self.body_layout = v = QVBoxLayout(body)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(4)
        self.preview = _Preview(body)
        v.addWidget(self.preview)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel(body)
        self.icon.setFixedSize(36, 36)
        head.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = QLabel(body, objectName="InfoName")
        self.name.setWordWrap(True)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.kind = QLabel(body, objectName="Dim")
        self.kind.setWordWrap(True)
        names.addWidget(self.name)
        names.addWidget(self.kind)
        head.addLayout(names, 1)
        v.addLayout(head)
        self.font_sample = QLabel(body, objectName="InfoFontSample")
        self.font_sample.setWordWrap(True)
        self.font_sample.hide()
        v.addWidget(self.font_sample)
        self.sections = QWidget(body)
        self.sections_layout = QVBoxLayout(self.sections)
        self.sections_layout.setContentsMargins(0, 4, 0, 0)
        self.sections_layout.setSpacing(2)
        v.addWidget(self.sections)
        self.note = QLabel(body, objectName="Dim")
        self.note.setWordWrap(True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note.hide()
        v.addWidget(self.note)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.size_button = QPushButton("Calculate size", body)
        self.size_button.clicked.connect(self._calculate_size)
        self.hash_button = QPushButton("SHA-256", body, toolTip="Calculate the file's SHA-256 checksum")
        self.hash_button.clicked.connect(self._checksum)
        actions.addWidget(self.size_button)
        actions.addWidget(self.hash_button)
        actions.addStretch(1)
        v.addLayout(actions)
        self.action_result = QLabel(body)
        self.action_result.setWordWrap(True)
        self.action_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.action_result.hide()
        v.addWidget(self.action_result)
        v.addStretch(1)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        self._details_timer = QTimer(self, singleShot=True, interval=DETAILS_DELAY_MS, timeout=self._start_details)
        self._refresh_timer = QTimer(self, singleShot=True, interval=300, timeout=lambda: self.refresh(force=True))
        bus = ctx.bus
        self._unsubs = [
            bus.subscribe(Topics.SELECTION_CHANGED, self._on_selection),
            bus.subscribe(Topics.ACTIVE_LOCATION_CHANGED, lambda **_: self._on_selection()),
            bus.subscribe(Topics.THEME_CHANGED, lambda **_: self.active and self.refresh(force=True, keep_cache=True)),
            bus.subscribe(Topics.SETTINGS_CHANGED, self._on_setting),
            bus.subscribe(Topics.FS_CHANGED, self._on_fs_changed),
        ]
        self.destroyed.connect(lambda *_: [u() for u in self._unsubs])
        self._set_actions(False, False)

    # ======================================================================
    # Shown / hidden
    # ======================================================================
    def on_shown(self) -> None:
        if not self._loaded_providers:
            from .info_providers import register_qt_providers
            register_qt_providers()
            self._loaded_providers = True
        self.active = True
        self.refresh(force=True, keep_cache=True)

    def on_hidden(self) -> None:
        self.active = False
        self._details_timer.stop()
        self._cancel()
        self._key = None
        self.preview.set_image(None)
        self._release_font()

    def shutdown(self) -> None:
        """Quitting: cancel the work in flight (external programs are killed)."""
        self.active = False
        self._details_timer.stop()
        self._refresh_timer.stop()
        self._cancel()

    def _cancel(self) -> None:
        for token in (self._token, self._action_token):
            if token is not None:
                token.cancel()
        self._token = self._action_token = None

    # ======================================================================
    # Events
    # ======================================================================
    def _on_selection(self, tab=None, **_) -> None:
        if not self.active:
            return                                    # closed: cost nothing
        if tab is not None and tab is not self.ctx.commands.active_tab():
            return
        self.refresh()

    def _on_setting(self, key: str = "", **_) -> None:
        if not self.active:
            if key.startswith("info_panel.") or key in ("view.size_units", "view.date_format"):
                self._cache.clear()
            return
        if key in ("info_panel.visible", "info_panel.collapsed"):
            return
        if key.startswith("info_panel.") or key in ("view.size_units", "view.date_format"):
            self._cache.clear()
            self._previews.clear()
            self.refresh(force=True)

    def _on_fs_changed(self, paths=None, **_) -> None:
        if not self.active or self._entry is None:
            return
        folders = set(paths or [])
        if self._entry.parent in folders or self._entry.path in folders:
            for key in [k for k in self._cache if k[0] == self._entry.path]:
                self._cache.pop(key, None)
            self._refresh_timer.start()

    # ======================================================================
    # What to show
    # ======================================================================
    def refresh(self, force: bool = False, keep_cache: bool = True) -> None:
        if not self.active:
            return
        if not keep_cache:
            self._cache.clear()
        tab = self.ctx.commands.active_tab()
        entries = list(getattr(tab, "selected_entries", lambda: [])()) if tab is not None else []
        if len(entries) == 1:
            self._show_entry(entries[0], force)
        elif len(entries) > 1:
            self._show_many(entries, force)
        else:
            folder = getattr(tab, "current_folder", None) if tab is not None else None
            if folder and not folder.startswith(("computer://", "device://")):
                self._show_entry(self._folder_entry(folder), force, this_folder=True)
            else:
                self._show_nothing(force)

    def _folder_entry(self, path: str) -> FileEntry:
        fs = self.ctx.vfs.resolve(path)
        name = fs.basename(path) or path
        return FileEntry(name, path, fs.parent(path) or "", True)

    @staticmethod
    def _key_for(e: FileEntry) -> tuple:
        return (e.path, e.mtime, e.size, e.is_dir)

    def _begin(self, key, force: bool) -> bool:
        """True if ``key`` needs drawing (it isn't what's on screen already)."""
        if key == self._key and not force:
            return False
        self._details_timer.stop()
        self._cancel()
        self._key = key
        self.action_result.hide()
        self.action_result.clear()
        return True

    def _show_nothing(self, force: bool) -> None:
        if not self._begin(("nothing",), force):
            return
        self._entry, self._entries, self._info = None, [], None
        self.preview.set_image(None)
        self.icon.clear()
        self.name.setText("Nothing selected")
        self.kind.setText("")
        self._draw_sections([])
        self._set_note("Select a file or folder to see its details.")
        self._release_font()
        self._set_actions(False, False)

    def _show_entry(self, e: FileEntry, force: bool, this_folder: bool = False) -> None:
        key = self._key_for(e) + (this_folder,)
        if not self._begin(key, force):
            return
        from ..services.metadata import general
        self._entry, self._entries = e, [e]
        self.icon.setPixmap(self.ctx.icons.for_entry(e).pixmap(36, 36))
        self.name.setText(e.name)
        fs = self.ctx.vfs.resolve(e.path)
        is_local = bool(getattr(fs, "is_local", False))
        root = self.ctx.cloud.find(e.path) if is_local else None
        state = cloud.effective_state(e.cloud, root is not None, root.reports_local if root else False)
        deep = state != cloud.ONLINE
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._draw_info(e, cached, this_folder)
            self._show_preview(key, cached)
        else:
            from ..services.metadata.base import Info
            basic = Info(kind=e.type_label, sections=[general.basic(e)])
            self._draw_info(e, basic, this_folder, loading=deep)
            self.preview.set_image(None)
            self._pending = (key, e, fs, deep, this_folder)
            self._details_timer.start(0 if force else DETAILS_DELAY_MS)
        if not deep:
            self._set_note("Only in the cloud — the details aren't read, so the file isn't downloaded.")
        self._set_actions(e.is_dir, not e.is_dir and is_local and deep)

    def _start_details(self) -> None:
        if self._pending is None:
            return
        key, e, fs, deep, this_folder = self._pending
        if key != self._key or not self.active:
            return
        from ..services.metadata import gather
        s = self.ctx.settings
        options = {"deep": deep, "video_frames": bool(s.get("info_panel.video_frames", True)),
                   "text_lines": int(s.get("info_panel.text_lines", 40) or 0)}
        token = self._token = CancelToken()
        self.ctx.runner.submit(gather, e, fs, token, options, pool="info", token=token,
                               on_done=lambda info: self._details_ready(key, e, info, this_folder, token),
                               on_error=lambda exc: self._details_failed(key, exc))

    def _details_ready(self, key, e: FileEntry, info, this_folder: bool, token: CancelToken) -> None:
        self._cache[key] = info
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        if key != self._key or token.cancelled:
            return
        self._draw_info(e, info, this_folder)
        self._show_preview(key, info, token)

    def _details_failed(self, key, exc: BaseException) -> None:
        if key == self._key:
            self._set_note(f"Couldn't read the details: {exc}")

    def _show_preview(self, key, info, token: Optional[CancelToken] = None) -> None:
        if info.preview is None or not self.ctx.settings.get("info_panel.previews", True):
            self.preview.set_image(None)
            return
        if key in self._previews:
            self._previews.move_to_end(key)
            self.preview.set_image(self._previews[key])
            return
        token = token or self._token or CancelToken()
        self._token = token
        max_px = int(min(1000, max(360, self.width() * self.devicePixelRatioF())))

        def done(image) -> None:
            self._previews[key] = image
            while len(self._previews) > PREVIEW_CACHE_SIZE:
                self._previews.popitem(last=False)
            if key == self._key:
                self.preview.set_image(image)
        self.ctx.runner.submit(decode_preview, info.preview, max_px, token, pool="info", token=token,
                               on_done=done, on_error=lambda exc: None)

    def _show_many(self, entries: List[FileEntry], force: bool) -> None:
        total = sum(e.size for e in entries if not e.is_dir)
        key = ("many", len(entries), entries[0].path, entries[-1].path, total)
        if not self._begin(key, force):
            return
        from ..services.metadata.base import Info, Section
        self._entry, self._entries = None, entries
        self.preview.set_image(None)
        self._release_font()
        self.icon.setPixmap(self.ctx.icons.for_entry(entries[0]).pixmap(36, 36))
        folders = sum(1 for e in entries if e.is_dir)
        files = len(entries) - folders
        self.name.setText(f"{len(entries):,} items selected")
        parts = [_count(folders, "folder") if folders else "", _count(files, "file") if files else ""]
        self.kind.setText(", ".join(p for p in parts if p))
        s = Section("Selection")
        s.add("Files' size", f"{human_size(total)} ({total:,} bytes)" if files else "")
        if folders:
            s.add("Folders' size", "Use “Calculate size” below")
        dated = [e.mtime for e in entries if e.mtime]
        if dated:
            s.add("Oldest", human_time(min(dated)))
            s.add("Newest", human_time(max(dated)))
        parents = {e.parent for e in entries}
        s.add("Location", next(iter(parents)) if len(parents) == 1 else f"{len(parents):,} folders")
        counts = {}
        for e in entries:
            c = counts.setdefault(e.category, [0, 0])
            c[0] += 1
            c[1] += e.size if not e.is_dir else 0
        t = Section("Types")
        for cat, (n, size) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
            t.add(CATEGORY_NAMES.get(cat, cat.title()), f"{n:,}" + (f"  ({human_size(size)})" if size else ""))
        exts = {}
        for e in entries:
            if e.ext:
                exts[e.ext] = exts.get(e.ext, 0) + 1
        if len(exts) > 1:
            top = sorted(exts.items(), key=lambda kv: -kv[1])[:8]
            t.add("Extensions", ", ".join(f".{x} ({n:,})" for x, n in top) + (" …" if len(exts) > 8 else ""))
        self._info = Info(sections=[s, t])
        self._draw_sections(self._info.sections)
        self._set_note("")
        self._set_actions(True, False)

    # ======================================================================
    # Drawing
    # ======================================================================
    def _draw_info(self, e: FileEntry, info, this_folder: bool, loading: bool = False) -> None:
        self._info = info
        kind = info.kind or e.type_label
        if this_folder:
            kind = f"This folder · {kind}" if kind != "Folder" else "This folder"
        if e.is_link:
            kind += " (link)"
        self.kind.setText(kind)
        self._draw_sections(info.sections)
        self._set_note("Reading details…" if loading else info.note)
        self._draw_font_sample(info.extra.get("font_file", ""))

    def _draw_sections(self, sections) -> None:
        lay = self.sections_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)                 # gone now, not at the next event-loop turn
                w.deleteLater()
        folded = set(self.ctx.settings.get("info_panel.collapsed", []) or [])
        p = self.ctx.theme.palette
        key_width = self.fontMetrics().horizontalAdvance("Overall bit rate") + 14   # one column for every section
        for s in sections:
            if not s:
                continue
            header = QPushButton(self.sections, objectName="InfoSection")
            header.setCheckable(True)
            header.setFlat(True)
            header.setChecked(s.fold_key not in folded)
            header.setCursor(Qt.CursorShape.PointingHandCursor)
            header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(header)
            bodies = []
            if s.rows:
                rows = "".join(
                    f'<tr><td width="{key_width}" style="color:{p["fg_dim"]}; padding:1px 8px 1px 0;" valign="top">'
                    f'{html.escape(k)}</td><td style="padding:1px 0;">'
                    f'{_breakable(html.escape(v)).replace(chr(10), "<br>")}</td></tr>' for k, v in s.rows)
                label = QLabel(f'<table cellspacing="0" cellpadding="0" width="100%">{rows}</table>', self.sections)
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                bodies.append(label)
            if s.text and s.mono:
                code = QPlainTextEdit(self.sections, objectName="InfoCode")
                code.setReadOnly(True)
                code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
                code.setFont(self.ctx.theme.mono_font())
                code.setPlainText(s.text)
                lines = min(s.text.count("\n") + 1, 18)
                code.setFixedHeight(code.fontMetrics().lineSpacing() * lines + 14)
                code.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                bodies.append(code)
            elif s.text:
                text = QLabel(_breakable(s.text), self.sections)
                text.setTextFormat(Qt.TextFormat.PlainText)
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                bodies.append(text)
            for b in bodies:
                lay.addWidget(b)

            def toggle(open_: bool, h=header, bs=bodies, title=s.title, k=s.fold_key) -> None:
                h.setText(("▾  " if open_ else "▸  ") + title)
                for b in bs:
                    b.setVisible(open_)
                current = set(self.ctx.settings.get("info_panel.collapsed", []) or [])
                changed = (current - {k}) if open_ else (current | {k})
                if changed != current:
                    self.ctx.settings.set("info_panel.collapsed", sorted(changed))
            header.toggled.connect(toggle)
            header.setText(("▾  " if header.isChecked() else "▸  ") + s.title)
            for b in bodies:
                b.setVisible(header.isChecked())

    def _set_note(self, text: str) -> None:
        self.note.setText(text or "")
        self.note.setVisible(bool(text))

    def _set_actions(self, size: bool, checksum: bool) -> None:
        self.size_button.setVisible(size)
        self.hash_button.setVisible(checksum)

    def _draw_font_sample(self, path: str) -> None:
        self._release_font()
        if not path:
            return
        fid = QFontDatabase.addApplicationFont(path)
        families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if not families:
            if fid >= 0:
                QFontDatabase.removeApplicationFont(fid)
            return
        self._font_id = fid
        font = QFont(families[0])
        font.setPointSizeF(max(self.font().pointSizeF(), 9) * 1.6)
        self.font_sample.setFont(font)
        self.font_sample.setText("The quick brown fox jumps over the lazy dog\n"
                                 "ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789 !?&@%(){}")
        self.font_sample.show()

    def _release_font(self) -> None:
        self.font_sample.hide()
        self.font_sample.clear()
        if self._font_id >= 0:
            QFontDatabase.removeApplicationFont(self._font_id)
            self._font_id = -1

    # ======================================================================
    # Buttons
    # ======================================================================
    def copy_details(self) -> None:
        if self._info is None:
            return
        head = self.name.text() + (f"\n{self.kind.text()}" if self.kind.text() else "")
        if self._entry is not None:
            head += f"\n{self._entry.path}"
        QGuiApplication.clipboard().setText(head + "\n\n" + self._info.as_text())
        self.ctx.bus.publish(Topics.STATUS_MESSAGE, text="Details copied", level="info")

    def _calculate_size(self) -> None:
        from ..services.walker import folder_size
        entries = list(self._entries)
        if not entries:
            return
        if self._action_token is not None:
            self._action_token.cancel()
        token = self._action_token = CancelToken()
        key = self._key
        dirs = [e for e in entries if e.is_dir]
        file_bytes = sum(e.size for e in entries if not e.is_dir)
        post = self.ctx.dispatcher.post
        last = [0.0]

        def show(total: int, files: int, folders: int, final: bool) -> None:
            if key != self._key or token.cancelled:
                return
            suffix = "" if final else " …"
            self.action_result.setText(f"Total: {human_size(total)} ({total:,} bytes)\n"
                                       f"{_count(files, 'file')}, {_count(folders, 'folder')}{suffix}")
            self.action_result.show()

        def progress(total, files, folders) -> None:          # worker thread
            if time.monotonic() - last[0] > 0.2:
                last[0] = time.monotonic()
                post(show, total + file_bytes, files, folders, False)

        def work():
            totals = [0, 0, 0]
            for d in dirs:
                b, f, fo = folder_size(self.ctx.vfs.resolve(d.path), d.path, token, progress)
                totals = [totals[0] + b, totals[1] + f, totals[2] + fo]
            return totals
        show(file_bytes, 0, 0, False)
        self.ctx.runner.submit(work, pool="misc", token=token,
                               on_done=lambda t: show(t[0] + file_bytes, t[1] + len(entries) - len(dirs), t[2], True),
                               on_error=lambda exc: self.action_result.setText(str(exc)))

    def _checksum(self) -> None:
        from ..services.hashing import sha256_file
        e = self._entry
        if e is None or e.is_dir:
            return
        if self._action_token is not None:
            self._action_token.cancel()
        token = self._action_token = CancelToken()
        key = self._key
        self.action_result.setText("SHA-256: calculating…")
        self.action_result.show()

        def done(digest: str) -> None:
            if key == self._key:
                self.action_result.setText(f"SHA-256:\n{_breakable(digest)}")
        # Hashing is CPU work: a separate process keeps the window smooth.
        self.ctx.runner.submit(sha256_file, e.path, pool="process", token=token, on_done=done,
                               on_error=lambda exc: key == self._key and self.action_result.setText(str(exc)))


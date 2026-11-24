"""
Dialogs: text prompt, confirm, error list, file conflict, network connection
editor, properties, preferences, and the keyboard-shortcut editor.

Each is a plain function or class you can call from anywhere with a parent
widget. Modal ones return the answer (or None if cancelled).
"""
from __future__ import annotations

import os
import stat as stat_mod
import time
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                               QHeaderView, QInputDialog, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import cloud
from ..core.formatting import human_size, human_time
from ..core.tasks import CancelToken


def _window(parent) -> Optional[QWidget]:
    return parent.window() if parent is not None else None


# ---------------------------------------------------------------------------
def ask_string(parent, title: str, prompt: str, initial: str = "", select_stem: bool = False,
               password: bool = False) -> Optional[str]:
    dlg = QInputDialog(_window(parent))
    dlg.setWindowTitle(title)
    dlg.setLabelText(prompt)
    dlg.setTextValue(initial)
    dlg.setTextEchoMode(QLineEdit.EchoMode.Password if password else QLineEdit.EchoMode.Normal)
    dlg.resize(420, dlg.sizeHint().height())
    if select_stem:
        edit = dlg.findChild(QLineEdit)
        dot = initial.rfind(".")
        if edit is not None and dot > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: edit.setSelection(0, dot))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.textValue()


def ask_login(parent, title: str, message: str, user: str = "", remember_where: str = "",
              remember: bool = True) -> Optional[Tuple[str, str, bool]]:
    """Username + password prompt. Returns (user, password, remember) or None."""
    dlg = QDialog(_window(parent))
    dlg.setWindowTitle(title)
    form = QFormLayout()
    user_edit = QLineEdit(user)
    pass_edit = QLineEdit()
    pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
    form.addRow("User", user_edit)
    form.addRow("Password", pass_edit)
    remember_box = QCheckBox("Remember this login")
    remember_box.setChecked(remember)
    form.addRow("", remember_box)
    if remember_where:
        where = QLabel(f"Saved in {remember_where}.", objectName="Dim")
        where.setWordWrap(True)
        form.addRow("", where)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout = QVBoxLayout(dlg)
    label = QLabel(message)
    label.setWordWrap(True)
    layout.addWidget(label)
    layout.addLayout(form)
    layout.addWidget(buttons)
    (pass_edit if user else user_edit).setFocus()
    dlg.resize(420, dlg.sizeHint().height())
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return user_edit.text().strip(), pass_edit.text(), remember_box.isChecked()


def confirm(parent, title: str, message: str, ok_text: str = "OK", danger: bool = False) -> bool:
    box = QMessageBox(_window(parent))
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


def info(parent, title: str, message: str) -> None:
    QMessageBox.information(_window(parent), title, message)


def show_errors(parent, title: str, errors: List[str]) -> None:
    box = QMessageBox(_window(parent))
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(title)
    box.setInformativeText("\n".join(errors[:8]) + (f"\n… and {len(errors) - 8} more" if len(errors) > 8 else ""))
    if len(errors) > 8:
        box.setDetailedText("\n".join(errors[:2000]))
    box.exec()


def ask_conflict(parent, src, dst) -> Tuple[str, bool]:
    """'X already exists' -> (choice, apply_to_all). choice from fileops.Conflict."""
    from ..services.fileops import Conflict
    box = QMessageBox(_window(parent))
    box.setWindowTitle("File already exists")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"“{dst.name}” already exists in this folder.")

    def describe(e) -> str:
        return ("" if e.is_dir else human_size(e.size) + ", ") + "modified " + human_time(e.mtime)
    box.setInformativeText(f"Existing:  {describe(dst)}\nNew:  {describe(src)}")
    apply_all = QCheckBox("Do this for all conflicts")
    box.setCheckBox(apply_all)
    replace = box.addButton("Replace", QMessageBox.ButtonRole.AcceptRole)
    keep = box.addButton("Keep both", QMessageBox.ButtonRole.AcceptRole)
    skip = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(keep)
    box.exec()
    clicked = box.clickedButton()
    choice = {id(replace): Conflict.OVERWRITE, id(keep): Conflict.RENAME, id(skip): Conflict.SKIP,
              id(cancel): Conflict.CANCEL}.get(id(clicked), Conflict.CANCEL)
    return choice, apply_all.isChecked()


# ---------------------------------------------------------------------------
class ConnectionDialog(QDialog):
    """Create or edit a saved SFTP/FTP/FTPS connection."""

    def __init__(self, parent, conn: Optional[dict] = None, remember_where: str = "") -> None:
        super().__init__(_window(parent))
        self.setWindowTitle("Network location")
        self.conn = dict(conn or {})
        c = self.conn
        form = QFormLayout()
        self.name = QLineEdit(c.get("name", ""))
        self.protocol = QComboBox()
        self.protocol.addItems(["smb", "sftp", "ftp", "ftps"])
        self.protocol.setCurrentText(c.get("protocol", "smb"))
        self.protocol.currentTextChanged.connect(self._protocol_changed)
        self.host = QLineEdit(c.get("host", ""))
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(c.get("port", 445)))
        self.user = QLineEdit(c.get("user", ""))
        self.password = QLineEdit(c.get("password", ""))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.start = QLineEdit(c.get("initial_path", "/"))
        self.remember = QCheckBox("Remember password" + (f" (saved in {remember_where})" if remember_where else ""))
        self.remember.setChecked(bool(c.get("remember_password", True)))
        for label, widget in (("Name", self.name), ("Protocol", self.protocol), ("Host", self.host),
                              ("Port", self.port), ("User", self.user), ("Password", self.password),
                              ("Start folder", self.start)):
            form.addRow(label, widget)
        form.addRow("", self.remember)
        self.hint = QLabel("", objectName="Dim")
        self.hint.setWordWrap(True)
        form.addRow("", self.hint)
        self._protocol_changed(self.protocol.currentText(), set_port=False)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save && connect")
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.result_data: Optional[dict] = None
        self.resize(460, self.sizeHint().height())

    PROTOCOL_INFO = {
        "smb": (445, "Windows / Samba shares: TrueNAS, Synology, QNAP, Windows PCs. Leave Start folder as / to "
                     "see all shares, or enter one, e.g. /media. User: your NAS user (DOMAIN\\user also works)."),
        "sftp": (22, "Files over SSH. Your SSH keys / ssh-agent are used automatically."),
        "ftp": (21, "Plain FTP (not encrypted)."),
        "ftps": (21, "FTP over TLS (encrypted)."),
    }

    def _protocol_changed(self, protocol: str, set_port: bool = True) -> None:
        default_port, hint = self.PROTOCOL_INFO.get(protocol, (0, ""))
        if set_port and self.port.value() in (21, 22, 445):
            self.port.setValue(default_port)
        self.hint.setText(hint)

    def _ok(self) -> None:
        if not self.host.text().strip():
            self.host.setFocus()
            return
        data = {"name": self.name.text().strip() or self.host.text().strip(),
                "protocol": self.protocol.currentText(), "host": self.host.text().strip(),
                "port": self.port.value(), "user": self.user.text().strip(), "password": self.password.text(),
                "initial_path": self.start.text().strip() or "/", "remember_password": self.remember.isChecked()}
        if self.conn.get("id"):
            data["id"] = self.conn["id"]
        self.result_data = data
        self.accept()

    def run(self) -> Optional[dict]:
        return self.result_data if self.exec() == QDialog.DialogCode.Accepted else None


# ---------------------------------------------------------------------------
class PropertiesDialog(QDialog):
    """Non-modal info window. Folder sizes and checksums are computed in the background."""

    def __init__(self, parent, ctx, entries) -> None:
        super().__init__(_window(parent))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.ctx = ctx
        self.token = CancelToken()
        title = entries[0].name if len(entries) == 1 else f"{len(entries)} items"
        self.setWindowTitle(f"Properties — {title}")
        self.grid = QGridLayout()
        self.grid.setColumnStretch(1, 1)
        self.rows = 0
        layout = QVBoxLayout(self)
        header = QLabel()
        header.setPixmap(ctx.icons.for_entry(entries[0]).pixmap(48, 48))
        layout.addWidget(header)
        layout.addLayout(self.grid)
        self.hash_label = None
        if len(entries) == 1:
            e = entries[0]
            self._row("Name", e.name)
            self._row("Type", e.type_label + (" (link)" if e.is_link else ""))
            self._row("Location", e.parent or "")
            self.size_label = self._row("Size", "calculating…" if e.is_dir else f"{human_size(e.size)} ({e.size:,} bytes)")
            self._row("Modified", human_time(e.mtime))
            root = ctx.cloud.find(e.path)
            state = cloud.effective_state(e.cloud, root is not None, root.reports_local if root else False)
            if state != cloud.NONE:
                self._row("Cloud", f"{root.label if root else 'Cloud'} — {cloud.LABELS[state].lower()}")
            self.cloud_only = state == cloud.ONLINE
            if ctx.vfs.resolve(e.path).is_local:
                try:
                    st = os.stat(e.path)
                    self._row("Accessed", human_time(st.st_atime))
                    self._row("Created" if os.name == "nt" else "Changed", human_time(st.st_ctime))
                    self._row("Permissions", stat_mod.filemode(st.st_mode))
                except OSError:
                    pass
                if not e.is_dir:
                    self.hash_label = self._row("SHA-256", "")
                    btn = QPushButton("Calculate checksum")
                    btn.clicked.connect(lambda: self._checksum(e.path))
                    layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self._row("Items", f"{len(entries):,}")
            self.size_label = self._row("Total size", "calculating…")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self._compute_size(entries)
        self.resize(480, self.sizeHint().height())

    def _row(self, label: str, value: str) -> QLabel:
        key = QLabel(label + ":", objectName="Dim")
        val = QLabel(value)
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.grid.addWidget(key, self.rows, 0, Qt.AlignmentFlag.AlignTop)
        self.grid.addWidget(val, self.rows, 1)
        self.rows += 1
        return val

    def _compute_size(self, entries) -> None:
        from ..services.walker import folder_size
        dirs = [e for e in entries if e.is_dir]
        file_bytes = sum(e.size for e in entries if not e.is_dir)
        if not dirs:
            if len(entries) > 1:
                self.size_label.setText(f"{human_size(file_bytes)} ({file_bytes:,} bytes)")
            return
        post = self.ctx.dispatcher.post
        last = [0.0]

        def progress(total, files, folders) -> None:          # worker thread
            if time.monotonic() - last[0] > 0.2:
                last[0] = time.monotonic()
                post(self._show_size, total + file_bytes, files, folders, False)

        def work():
            totals = [0, 0, 0]
            for d in dirs:
                b, f, fo = folder_size(self.ctx.vfs.resolve(d.path), d.path, self.token, progress)
                totals = [totals[0] + b, totals[1] + f, totals[2] + fo]
            return totals

        self.ctx.runner.submit(work, pool="misc", token=self.token,
                               on_done=lambda t: self._show_size(t[0] + file_bytes, t[1], t[2], True))

    def _show_size(self, total: int, files: int, folders: int, final: bool) -> None:
        if not self.token.cancelled:
            suffix = "" if final else " …"
            self.size_label.setText(f"{human_size(total)} ({total:,} bytes) — {files:,} files, {folders:,} folders{suffix}")

    def _checksum(self, path: str) -> None:
        from ..services.hashing import sha256_file
        if getattr(self, "cloud_only", False) and not confirm(
                self, "Download file?", "This file is only in the cloud. Calculating its checksum will "
                "download it to this device first.", "Download && calculate"):
            return
        self.hash_label.setText("calculating…")
        # Hashing is CPU work: run it in a separate PROCESS so the UI never stutters.
        self.ctx.runner.submit(sha256_file, path, pool="process", token=self.token,
                               on_done=self.hash_label.setText,
                               on_error=lambda e: self.hash_label.setText(str(e)))

    def closeEvent(self, event) -> None:
        self.token.cancel()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Preferences: generated from this list, so adding a setting = one line here.
FONT_KEYS = ("appearance.font_family", "appearance.font_size", "appearance.terminal_font")  # re-apply the look
PREFERENCES = [
    # (tab, settings key, label, kind, options)
    ("Appearance", "appearance.theme", "Theme", "theme", None),
    ("Appearance", "appearance.font_family", "Font", "font", None),
    ("Appearance", "appearance.font_size", "Font size (0 = system default)", "int", (0, 24)),
    ("Appearance", "appearance.terminal_font", "Terminal font (monospace)", "monofont", None),
    ("Appearance", "appearance.system_icons", "Use system icons for files", "bool", None),
    ("Appearance", "view.thumbnails", "Show image thumbnails in Icons view", "bool", None),
    ("Appearance", "view.list_cell_width", "List view column width", "int", (120, 600)),
    ("Behaviour", "behaviour.single_click_open", "Open items with a single click", "bool", None),
    ("Behaviour", "behaviour.confirm_delete", "Ask before moving to trash", "bool", None),
    ("Behaviour", "behaviour.use_trash", "Delete moves to the trash / recycle bin", "bool", None),
    ("Behaviour", "behaviour.type_to_filter", "Typing in a folder starts the instant filter", "bool", None),
    ("Behaviour", "behaviour.watch_folders", "Auto-refresh when folders change on disk", "bool", None),
    ("Terminal", "terminal.sync_cd", "Terminal follows the current folder", "bool", None),
    ("Terminal", "terminal.shell", "Shell command (empty = automatic)", "str", None),
    ("Terminal", "terminal.external", "External terminal command (empty = automatic)", "str", None),
    ("Terminal", "terminal.scrollback", "Scroll-back lines (applies after restart)", "int", (100, 100000)),
    ("Network", "network.open_files", "Opening files on network shares", "choice",
     [("stream", "Open directly - the app streams the file (recommended)"), ("download", "Download a copy first")]),
    ("Network", "network.mount_shares", "Mount SMB shares like the system file manager does, so every app can open them",
     "bool", None),
    ("Transfers", "transfers.engine", "Copy and move with", "choice",
     [("pyexplorer", "PyExplorer's transfer window (verify, per-file results, history)"),
      ("simple", "Simple copier (status bar only, no verification)")]),
    ("Transfers", "transfers.show_in", "Show transfers in", "choice",
     [("window", "A separate window"), ("tab", "A tab in the main window")]),
    ("Transfers", "transfers.verify", "Verify every file after copying (checksum, read back)", "bool", None),
    ("Transfers", "transfers.algorithm", "Checksum", "choice",
     [("blake2b", "BLAKE2b (fast, built in)"), ("xxh3", "xxHash3 (fastest; needs the xxhash package)"),
      ("md5", "MD5"), ("sha1", "SHA-1"), ("sha256", "SHA-256")]),
    ("Transfers", "transfers.verify_from_disk", "Read the copy back from the disk itself, not from memory (local drives)",
     "bool", None),
    ("Transfers", "transfers.close_when_done", "Close the transfer window when everything finished fine", "bool", None),
    ("Transfers", "transfers.keep_log", "Keep the list of transfers after closing PyExplorer", "bool", None),
    ("Search", "search.live", "Search as you type", "bool", None),
    ("Search", "search.refresh_minutes", "Re-read the disk in the background after (minutes, 0 = never)", "int", (0, 1440)),
    ("Search", "search.index_network", "Include network and cloud drives in \"whole computer\"", "bool", None),
    ("Search", "search.workers", "Parallel readers for network folders (0 = automatic)", "int", (0, 32)),
]


class PreferencesDialog(QDialog):
    def __init__(self, parent, ctx) -> None:
        super().__init__(_window(parent))
        from .theme import display_name, grouped
        self.ctx = ctx
        self.setWindowTitle("Preferences")
        self.widgets = {}
        tabs = QTabWidget(self)
        forms = {}
        for section, key, label, kind, opts in PREFERENCES:
            if section not in forms:
                page = QWidget()
                forms[section] = QFormLayout(page)
                tabs.addTab(page, section)
            form = forms[section]
            value = ctx.settings.get(key)
            if kind == "bool":
                w = QCheckBox(label)
                w.setChecked(bool(value))
                form.addRow(w)
            else:
                if kind == "theme":
                    w = QComboBox()
                    w.setMaxVisibleItems(24)
                    w.addItem("System", "system")
                    for group, keys in grouped():
                        w.insertSeparator(w.count())
                        w.addItem(f"— {group} —")                 # a heading, not selectable
                        w.model().item(w.count() - 1).setEnabled(False)
                        for k in keys:
                            w.addItem(display_name(k), k)
                    w.setCurrentIndex(max(0, w.findData(value)))
                elif kind in ("font", "monofont"):
                    from .font_picker import FontCombo
                    w = FontCombo(str(value or ""), monospace=(kind == "monofont"))
                elif kind == "choice":
                    w = QComboBox()
                    for choice_value, choice_label in opts:
                        w.addItem(choice_label, choice_value)
                    w.setCurrentIndex(max(0, w.findData(value)))
                elif kind == "int":
                    w = QSpinBox(minimum=opts[0], maximum=opts[1], value=int(value or 0))
                else:
                    w = QLineEdit("" if value is None else str(value))
                form.addRow(label, w)
            self.widgets[key] = (kind, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        # Wide enough for every tab heading, whatever font is in use.
        self.resize(max(520, tabs.tabBar().sizeHint().width() + 40), self.sizeHint().height())

    def _apply(self) -> None:
        theme_changed = False
        for key, (kind, w) in self.widgets.items():
            if kind == "bool":
                value = w.isChecked()
            elif kind == "int":
                value = w.value()
            elif kind == "theme":
                if w.currentData() and w.currentData() != self.ctx.theme.name:
                    self.ctx.commands.set_theme(w.currentData())
                continue
            elif kind == "choice":
                value = w.currentData()
            elif kind in ("font", "monofont"):
                value = w.family()
            else:
                value = w.text()
            if key in FONT_KEYS and value != self.ctx.settings.get(key):
                theme_changed = True
            self.ctx.settings.set(key, value)
        if theme_changed:
            self.ctx.commands.set_theme(self.ctx.theme.name)
        self.accept()


# ---------------------------------------------------------------------------
class ShortcutsDialog(QDialog):
    """Every action and its keyboard shortcut. Click a shortcut to change it."""

    def __init__(self, parent, ctx) -> None:
        super().__init__(_window(parent))
        self.ctx = ctx
        self.setWindowTitle("Keyboard shortcuts")
        actions = [a for a in ctx.actions.all() if not a.id.startswith("theme.")]
        self.table = QTableWidget(len(actions), 2, self)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.editors = {}
        for row, action in enumerate(actions):
            item = QTableWidgetItem(action.label)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, item)
            edit = QKeySequenceEdit(QKeySequence(action.shortcut))
            edit.setClearButtonEnabled(True)
            self.table.setCellWidget(row, 1, edit)
            self.editors[action.id] = edit
        note = QLabel("Click a shortcut and press the new keys. Changes apply when you press OK.", objectName="Dim")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._defaults)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
        self.resize(560, 640)

    def _defaults(self) -> None:
        for action_id, edit in self.editors.items():
            edit.setKeySequence(QKeySequence(self.ctx.actions.default_shortcut(action_id)))

    def _apply(self) -> None:
        overrides = {}
        for action_id, edit in self.editors.items():
            portable = QKeySequence.SequenceFormat.PortableText
            text = edit.keySequence().toString(portable)
            default = QKeySequence(self.ctx.actions.default_shortcut(action_id)).toString(portable)
            if text != default:                 # compare normalised ("Del" == "Delete")
                overrides[action_id] = text
        self.ctx.settings.set("shortcuts", overrides)
        self.ctx.actions.apply_overrides()
        self.accept()


class SavedLoginsDialog(QDialog):
    """Saved network logins (no passwords shown), with a Forget button."""

    def __init__(self, parent, store) -> None:
        super().__init__(_window(parent))
        self.store = store
        self.setWindowTitle("Saved network logins")
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Server", "User"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        forget = QPushButton("Forget")
        forget.clicked.connect(self._forget)
        note = QLabel(f"Passwords are kept in {store.where()}.", objectName="Dim")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(forget, QDialogButtonBox.ButtonRole.ActionRole)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self._fill()
        self.resize(520, 320)

    def _fill(self) -> None:
        saved = self.store.saved()
        self.table.setRowCount(len(saved))
        for row, (key, user) in enumerate(saved):
            self.table.setItem(row, 0, QTableWidgetItem(key))
            self.table.setItem(row, 1, QTableWidgetItem(user))

    def _forget(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.store.delete(self.table.item(row, 0).text())
        self._fill()


def show_text(parent, title: str, text: str) -> None:
    dlg = QDialog(_window(parent))
    dlg.setWindowTitle(title)
    view = QPlainTextEdit(text)
    view.setReadOnly(True)
    layout = QVBoxLayout(dlg)
    layout.addWidget(view)
    dlg.resize(600, 400)
    dlg.exec()

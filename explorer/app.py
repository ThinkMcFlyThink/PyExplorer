"""
app.py - the "composition root": the ONE place that creates every service and
widget and connects them. Everything else receives what it needs through the
AppContext, so swapping a part (say, a different search engine) means
changing a line here, not hunting through the code.

Startup order:
  1. config + logging          4. theme, icons, commands, main window, actions
  2. QApplication              5. restore last session
  3. core + services           6. show window, start background loops
"""
from __future__ import annotations

import logging
import os
import sys
from typing import List

from . import APP_NAME, APP_VERSION
from .context import AppContext
from .core import formatting
from .core.categories import Categories
from .core.clipboard import FileClipboard
from .core.connections import Connections
from .core.events import EventBus, Topics
from .core.file_history import FileHistory
from .core.transfer_model import TransferLog
from .core.folder_colors import FolderColors
from .core.paths import config_dir, config_file
from .core.platform_info import IS_LINUX, default_categories, home
from .core.session import Session
from .core.settings import Settings
from .core.storage import JsonStore
from .core.tasks import JobManager, TaskRunner
from .services.cloud_providers import CloudProviders
from .services.device_monitor import DeviceMonitor, describe_changes
from .services.devices import cloud_roots
from .services.drive_usage import DriveUsage
from .services.credential_store import CredentialStore, login_key
from .services.desktop_apps import DesktopApps
from .services.fileops import FileOperations
from .services.transfer_engine import TransferEngine
from .services.remote_open import RemoteOpener
from .services.stream_server import StreamServer
from .services.scanner import DirectoryScanner
from .services.search import SearchService
from .services.watcher import FolderWatcher
from .vfs.registry import VfsRegistry
from .vfs.sftp import CredentialsProvider

log = logging.getLogger("pyexplorer")
AUTOSAVE_MS = 20_000


def _setup_logging() -> None:
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(config_file("pyexplorer.log"), mode="w", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


class UICredentials(CredentialsProvider):
    """Lets background network connections ask the user questions.
    Called from worker threads -> we hop to the UI thread and wait for the answer."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def ask_password(self, conn: dict):
        from .ui import dialogs
        who = f"{conn.get('user') or 'anonymous'}@{conn.get('host')}"
        return self.ctx.dispatcher.call_and_wait(
            dialogs.ask_string, self.ctx.main, "Password needed", f"Password for {who}:", "", password=True)

    def ask_login(self, conn: dict, reason: str = ""):
        from .ui import dialogs
        host = conn.get("host")
        answer = self.ctx.dispatcher.call_and_wait(
            dialogs.ask_login, self.ctx.main, f"Log in to {host}", reason or f"Log in to {host}:",
            conn.get("user") or "", self.ctx.credentials.where())
        if answer and conn.get("id"):          # saved connection: remember for this session
            self.ctx.connections.remember_session_password(conn["id"], answer[1])
        return answer

    # Remembered logins live in the keyring (services/credential_store.py).
    def saved_login(self, conn: dict):
        return self.ctx.credentials.get(login_key(conn))

    def save_login(self, conn: dict, user: str, password: str) -> None:
        self.ctx.credentials.set(login_key(conn), user, password)

    def forget_login(self, conn: dict) -> None:
        self.ctx.credentials.delete(login_key(conn))

    def confirm_host_key(self, host: str, key_type: str, fingerprint: str) -> bool:
        from .ui import dialogs
        return bool(self.ctx.dispatcher.call_and_wait(
            dialogs.confirm, self.ctx.main, "Unknown server",
            f"The server {host} is not known yet.\n\nKey type: {key_type}\nFingerprint: {fingerprint}\n\n"
            "Only continue if you trust this server. Remember it and connect?", "Trust && connect"))


def build_context(app) -> AppContext:
    """Create every service and the main window. Separate from run() so tests can use it."""
    from .actions import ActionRegistry, register_all
    from .commands import Commands
    from .ui import dialogs
    from .ui.icons import IconProvider
    from .ui.main_window import MainWindow
    from .ui.qt_bridge import QtDispatcher, qt_schedule
    from .ui.theme import ThemeManager
    from .ui.thumbnails import ThumbnailProvider

    ctx = AppContext()
    ctx.app = app

    # -- core ----------------------------------------------------------------
    ctx.bus = EventBus()
    ctx.settings = Settings(JsonStore(config_file("settings.json")), ctx.bus)
    formatting.configure(ctx.settings.get("view.size_units", "binary"), ctx.settings.get("view.date_format", "relative"))
    ctx.dispatcher = QtDispatcher()
    ctx.runner = TaskRunner(ctx.dispatcher)
    ctx.jobs = JobManager(ctx.dispatcher, ctx.bus)
    ctx.clipboard = FileClipboard(ctx.bus)
    ctx.folder_colors = FolderColors(JsonStore(config_file("folder_colors.json")), ctx.bus)
    # Drives, network mounts and cloud folders: one snapshot now, then kept
    # up to date in the background (started in run()).
    def on_devices_changed(old, new):           # runs on the UI thread
        ctx.cloud.set_roots(cloud_roots(new))
        ctx.bus.publish(Topics.DEVICES_CHANGED, old=old, new=new)
        message = describe_changes(old, new)
        if message:
            ctx.bus.publish(Topics.STATUS_MESSAGE, text=message, level="info")
    ctx.devices = DeviceMonitor(ctx.dispatcher, on_devices_changed)
    ctx.cloud = CloudProviders(cloud_roots(ctx.devices.devices))   # OneDrive / Dropbox / rclone... folders
    ctx.categories = Categories(JsonStore(config_file("categories.json")), ctx.bus, default_categories)
    ctx.connections = Connections(JsonStore(config_file("connections.json")), ctx.bus)
    ctx.credentials = CredentialStore(str(config_dir()))
    for conn, password in ctx.connections.take_plaintext_passwords():     # move old plain-text passwords
        ctx.credentials.set(login_key(conn), conn.get("user") or "", password)
    ctx.session = Session(JsonStore(config_file("session.json")))
    ctx.history = FileHistory(JsonStore(config_file("history.json")),
                              keep=lambda: bool(ctx.settings.get("history.keep", True)),
                              on_change=lambda: ctx.bus.publish(Topics.HISTORY_CHANGED))

    # -- file systems + services ---------------------------------------------
    ctx.vfs = VfsRegistry(ctx.connections, UICredentials(ctx))
    ctx.vfs.computer.set_device_source(lambda: ctx.devices.devices)
    ctx.scanner = DirectoryScanner(ctx.vfs, ctx.runner)
    # The Search tab's engine. Indexes saved last time load in the background (ctx.search.start()).
    ctx.search = SearchService(ctx.vfs, ctx.runner, ctx.settings,
                               devices=lambda: ctx.devices.devices if ctx.devices else [],
                               on_index_updated=lambda root: ctx.bus.publish(Topics.INDEX_UPDATED, root=root))

    def ask_conflict(src, dst):                 # worker thread -> UI dialog -> answer
        return ctx.dispatcher.call_and_wait(dialogs.ask_conflict, ctx.main, src, dst)

    def report_errors(title, errors):
        ctx.dispatcher.post(dialogs.show_errors, ctx.main, title, errors)

    def publish_changed(folders):
        ctx.dispatcher.post(ctx.bus.publish, Topics.FS_CHANGED, paths=list(folders))

    def record_history(kind, items):           # worker thread -> the History tab's list
        ctx.dispatcher.post(ctx.history.record, kind, items)

    ctx.fileops = FileOperations(ctx.vfs, ctx.runner, ctx.jobs, ask_conflict, report_errors, publish_changed,
                                 record_history)
    ctx.transfer_log = TransferLog(JsonStore(config_file("transfers.json")),
                                   keep=lambda: bool(ctx.settings.get("transfers.keep_log", True)))
    ctx.transfers = TransferEngine(
        ctx.vfs, ctx.runner, ctx.jobs, ctx.transfer_log,
        options=lambda: {"verify": bool(ctx.settings.get("transfers.verify", True)),
                         "algorithm": ctx.settings.get("transfers.algorithm", "blake2b"),
                         "verify_from_disk": bool(ctx.settings.get("transfers.verify_from_disk", True))},
        ask_conflict=ask_conflict, publish_changed=publish_changed, record_history=record_history,
        on_event=lambda event, t: ctx.dispatcher.post(ctx.bus.publish, Topics.TRANSFERS_CHANGED,
                                                      event=event, transfer=t))
    ctx.drive_usage = DriveUsage(ctx.runner, lambda: ctx.devices.devices if ctx.devices else [],
                                 lambda path: ctx.bus.publish(Topics.DRIVE_USAGE_CHANGED, path=path))
    ctx.drive_usage.enabled = bool(ctx.settings.get("appearance.drive_usage", True))
    ctx.stream_server = StreamServer(ctx.vfs)
    ctx.remote_opener = RemoteOpener(ctx.vfs, ctx.stream_server, ctx.settings)
    if IS_LINUX:
        ctx.desktop_apps = DesktopApps()
        ctx.runner.submit(ctx.desktop_apps.load, pool="misc", on_error=lambda exc: None)   # read app list early

    def open_folders():
        if ctx.main is None:
            return []
        paths = []
        for tab in ctx.commands.all_tabs():
            if getattr(tab, "is_utility", False):
                paths.extend(tab.watched_folders())              # e.g. the Rename tab's folder
            else:
                paths.append(tab.path)
                paths.extend(tab.qmodel.loaded_folders())      # expanded tree folders too
        return paths

    ctx.watcher = FolderWatcher(ctx.vfs, ctx.runner, qt_schedule, open_folders,
                                lambda path: ctx.bus.publish(Topics.FS_CHANGED, paths=[path]),
                                int(ctx.settings.get("behaviour.watch_interval_ms", 1500)))
    ctx.watcher.enabled = bool(ctx.settings.get("behaviour.watch_folders", True))

    # -- ui ------------------------------------------------------------------------
    ctx.theme = ThemeManager(app, ctx)
    ctx.theme.apply()
    ctx.icons = IconProvider(ctx)
    ctx.thumbnails = ThumbnailProvider(ctx.runner)
    ctx.commands = Commands(ctx)
    main = MainWindow(ctx)                     # sets ctx.main
    ctx.actions = ActionRegistry(main, ctx.settings, ctx.commands.focus_in_files)
    register_all(ctx.actions, ctx)
    main.build_chrome()
    from .ui.transfers.controller import TransferUI
    ctx.transfer_ui = TransferUI(ctx)
    main.confirm_close = ctx.transfer_ui.confirm_quit

    # -- reactions that span several parts -------------------------------------------
    def on_fs_changed(paths):
        """Files changed: patch the search index and refresh affected tabs."""
        for p in paths:
            ctx.search.folder_changed(p)
        ctx.drive_usage.invalidate()                     # free space may have changed: re-measure when shown
        for tab in list(ctx.commands.all_tabs()):
            for p in paths:
                tab.on_folder_changed(p)
    ctx.bus.subscribe(Topics.FS_CHANGED, on_fs_changed)

    def on_setting(key, value=None):
        if key == "behaviour.watch_folders":
            ctx.watcher.enabled = bool(value)
        elif key == "behaviour.watch_interval_ms":
            ctx.watcher.interval_ms = max(250, int(value or 1500))
        elif key in ("view.date_format", "view.size_units"):
            formatting.configure(ctx.settings.get("view.size_units", "binary"),
                                 ctx.settings.get("view.date_format", "relative"))
            ctx.bus.publish(Topics.FORMAT_CHANGED)
        elif key == "appearance.system_icons":
            ctx.icons.clear()                            # file icons (and their cloud badges) are cached
        elif key == "appearance.drive_usage":
            ctx.drive_usage.enabled = bool(value)
            ctx.bus.publish(Topics.DRIVE_USAGE_CHANGED, path="")
        elif key == "history.keep":
            if value:
                ctx.history.save()
            else:
                ctx.history.forget_saved()
            ctx.bus.publish(Topics.HISTORY_CHANGED)
    ctx.bus.subscribe(Topics.SETTINGS_CHANGED, on_setting)
    return ctx


def run(argv: List[str]) -> int:
    _setup_logging()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is missing. Install it with:  pip install -r requirements.txt")
        return 1
    try:
        import pyte  # noqa: F401
    except ImportError:
        print("pyte is missing (needed for the terminal). Install it with:  pip install -r requirements.txt")
        return 1
    from .ui import qt_messages
    qt_messages.install()                       # Qt's warnings -> our log (and harmless ones hushed)
    app = QApplication([sys.argv[0]] + list(argv))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("pyexplorer")
    ctx = build_context(app)
    main = ctx.main
    app.setWindowIcon(ctx.icons.folder())

    # -- restore last session (or start at home) -------------------------------
    session = ctx.session.load()
    if session:
        ctx.closed_tabs = list(session.get("closed_tabs") or [])
        if not ctx.settings.get("behaviour.restore_session", True):
            # Start fresh in the home folder - last time's tabs can still be reopened (Ctrl+Shift+T).
            ctx.closed_tabs += [t for p in session.get("panels") or [] for t in p.get("tabs") or []]
            session = None
    main.restore(session)
    for arg in argv:                                    # paths given on the command line
        if os.path.exists(arg) or "://" in arg:
            ctx.commands.new_tab(os.path.abspath(arg) if "://" not in arg else arg)

    def save_all() -> None:
        try:
            ctx.session.save(main.session_state())
            if ctx.settings.dirty:
                ctx.settings.save()
            if ctx.history.dirty:
                ctx.history.save()
            if ctx.transfer_log.dirty or ctx.transfer_log.active():
                ctx.transfer_log.save()
        except Exception:  # noqa: BLE001
            log.exception("Saving failed")

    def on_exit() -> None:
        save_all()
        ctx.transfer_ui.shutdown()
        ctx.watcher.stop()
        ctx.devices.stop()
        ctx.search.close()
        main.terminal.shutdown()
        main.info.shutdown()                            # stops any ffprobe / 7z it is waiting for
        ctx.dispatcher.stop()
        ctx.runner.shutdown()
        ctx.stream_server.stop()
        ctx.vfs.close_all()

    main.on_exit = on_exit
    ctx.dispatcher.start()
    ctx.watcher.start()
    ctx.devices.start()                                 # notices new drives / mounts / cloud folders
    ctx.search.start()                                  # loads the saved search indexes in the background
    from PySide6.QtCore import QTimer
    autosave = QTimer(main, interval=AUTOSAVE_MS, timeout=save_all)   # protects your session from crashes
    autosave.start()

    main.show()
    tab = ctx.commands.active_tab()
    if tab:
        tab.focus_view()
    log.info("%s %s started (config: %s, home: %s)", APP_NAME, APP_VERSION, config_dir(), home())
    return app.exec()

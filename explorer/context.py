"""
AppContext - one object that holds every shared service.

Widgets get the context instead of importing each other. A widget that needs
to copy files calls ``ctx.commands.copy(...)``; it does not need to know that
the main window, the panels or the status bar exist. This "dependency
injection" is what keeps each part replaceable on its own.

The attributes are filled in by ``app.py`` (the only place that knows how
everything is wired together).
"""
from __future__ import annotations

from typing import Any, List


class AppContext:
    def __init__(self) -> None:
        self.app: Any = None             # QApplication
        # core (plain Python)
        self.bus: Any = None             # core.events.EventBus
        self.settings: Any = None        # core.settings.Settings
        self.dispatcher: Any = None      # ui.qt_bridge.QtDispatcher (a core.tasks.UIDispatcher)
        self.runner: Any = None          # core.tasks.TaskRunner
        self.jobs: Any = None            # core.tasks.JobManager
        self.clipboard: Any = None       # core.clipboard.FileClipboard (which items are "cut")
        self.folder_colors: Any = None   # core.folder_colors.FolderColors
        self.categories: Any = None      # core.categories.Categories
        self.connections: Any = None     # core.connections.Connections
        self.credentials: Any = None     # services.credential_store.CredentialStore (saved logins)
        self.cloud: Any = None           # services.cloud_providers.CloudProviders
        self.devices: Any = None         # services.device_monitor.DeviceMonitor (.devices = live list)
        self.session: Any = None         # core.session.Session
        self.history: Any = None         # core.file_history.FileHistory (the History tab, Ctrl+H)
        # file systems + services
        self.vfs: Any = None             # vfs.registry.VfsRegistry
        self.scanner: Any = None         # services.scanner.DirectoryScanner
        self.search: Any = None          # services.search.SearchService
        self.fileops: Any = None         # services.fileops.FileOperations
        self.transfer_log: Any = None    # core.transfer_model.TransferLog (every copy/move and its files)
        self.transfers: Any = None       # services.transfer_engine.TransferEngine (does the copying)
        self.transfer_ui: Any = None     # ui.transfers.controller.TransferUI (window / tab, taskbar progress)
        self.drive_usage: Any = None     # services.drive_usage.DriveUsage (how full each drive is)
        self.watcher: Any = None         # services.watcher.FolderWatcher
        self.desktop_apps: Any = None    # services.desktop_apps.DesktopApps (Linux only, else None)
        self.stream_server: Any = None   # services.stream_server.StreamServer
        self.remote_opener: Any = None   # services.remote_open.RemoteOpener
        # ui helpers
        self.theme: Any = None           # ui.theme.ThemeManager
        self.icons: Any = None           # ui.icons.IconProvider
        self.thumbnails: Any = None      # ui.thumbnails.ThumbnailProvider
        self.commands: Any = None        # commands.Commands
        self.actions: Any = None         # actions.ActionRegistry
        self.main: Any = None            # ui.main_window.MainWindow
        self.closed_tabs: List[dict] = []  # for "reopen closed tab"

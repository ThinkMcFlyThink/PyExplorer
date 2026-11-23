"""
UtilityTab - the base for "utility tabs": tools that live in a tab next to
the folder tabs (Search, Rename, ...).

A utility tab sits in the same tab bar as folder tabs, can be moved between
the split panels, is saved with the session and reopened with Ctrl+Shift+T.
It looks clearly different (see TabBar.paintEvent in ui/panel.py and the
"UtilityHeader" style in ui/theme.py): an accent-tinted tab with a marker
stripe, and a tinted header band across the top of the tab.

What a subclass provides
------------------------
    KIND        "search", "rename" ...  (registered in ui/utility/__init__.py)
    TITLE       shown in the tab
    SHAPE       the line icon to use (see icons.SymbolIconEngine)
    build(body) put its widgets into ``body`` (a QVBoxLayout under the header)
    get_state() / restore(state)   for the session
and may override ``path``, ``current_folder``, ``view``, ``selected_entries``,
``summary``, ``refresh``, ``on_folder_changed``, ``watched_folders``.

Everything else the app may ask a tab (go_back, focus_search...) has a safe
"does nothing" default here, so commands written for folder tabs never break
when a utility tab is the current one.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.events import Topics
from ...core.fileinfo import FileEntry
from ...core.platform_info import home


class UtilityHeader(QFrame):
    """The tinted band across the top of every utility tab: icon, title, a
    subtitle (what it works on), and room for a few buttons on the right."""

    def __init__(self, icon: QIcon, title: str, parent=None) -> None:
        super().__init__(parent, objectName="UtilityHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(10)
        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(icon.pixmap(22, 22))
        row.addWidget(self.icon_label)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.title_label = QLabel(title, self, objectName="UtilityTitle")
        self.restyle()
        self.subtitle_label = QLabel("", self, objectName="UtilitySubtitle")
        self.subtitle_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.title_label)
        texts.addWidget(self.subtitle_label)
        row.addLayout(texts, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(4)
        row.addLayout(self.buttons)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setToolTip(text)

    def restyle(self, icon: Optional[QIcon] = None) -> None:
        """Follow theme / font changes: a bold title a bit larger than the interface font."""
        font = QFont(QApplication.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.2)
        self.title_label.setFont(font)
        if icon is not None:
            self.icon_label.setPixmap(icon.pixmap(22, 22))


class UtilityTab(QWidget):
    is_utility = True
    KIND = "utility"
    TITLE = "Utility"
    SHAPE = "search"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.panel = panel
        self.view = None                        # a file view, if the utility shows files
        self.status_note = ""
        self._unsubs = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = UtilityHeader(self.icon, self.TITLE, self)
        outer.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        outer.addLayout(self.body, 1)
        self.build(self.body)
        self._unsubs.append(ctx.bus.subscribe(Topics.THEME_CHANGED, lambda **_: self._on_theme()))
        self.restore(dict(state or {}))

    # -- for subclasses ------------------------------------------------------------
    def build(self, body: QVBoxLayout) -> None:
        raise NotImplementedError

    def get_state(self) -> dict:
        return {"utility": self.KIND}

    def restore(self, state: dict) -> None:
        pass

    # -- what the panel / tab bar / commands ask -------------------------------------------
    @property
    def title(self) -> str:
        return self.TITLE

    @property
    def icon(self) -> QIcon:
        return self.ctx.icons.symbol(self.SHAPE, force=True, tint="accent")

    @property
    def path(self) -> str:
        """The folder this utility is about (tooltip, sidebar highlight, splitting)."""
        return home()

    @property
    def current_folder(self) -> Optional[str]:
        """Where pasting / "new folder" would go: nowhere, by default."""
        return None

    search_mode = False                         # (folder tabs show a Location column when True)
    search_text = None

    def selected_entries(self) -> List[FileEntry]:
        return self.view.selected_entries() if self.view is not None else []

    def summary(self) -> str:
        return self.status_note

    def focus_view(self) -> None:
        if self.view is not None:
            self.view.setFocus()
        else:
            self.setFocus()

    def watched_folders(self) -> List[str]:
        """Folders whose changes this tab wants to hear about (on_folder_changed)."""
        return []

    def on_folder_changed(self, folder: str) -> None:
        pass

    def refresh(self, soft: bool = True) -> None:
        pass

    def navigate(self, path: str, record: bool = True, select: Optional[List[str]] = None) -> None:
        """"Open containing folder" etc.: a folder tab right next to this one."""
        tab = self.panel.open_tab(path, activate=True, index=self.panel.tab_widget.indexOf(self) + 1)
        if select:
            tab._select_after_load = list(select)

    def updated(self) -> None:
        """Title / subtitle changed: tell the tab bar and the status bar."""
        self.panel.tab_updated(self)
        self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=self)

    # Folder-tab commands that mean nothing here (so shortcuts never fail).
    def go_back(self) -> None: pass             # noqa: E704
    def go_forward(self) -> None: pass          # noqa: E704
    def go_up(self) -> None: pass               # noqa: E704
    def show_filter(self, initial: str = "") -> None: pass   # noqa: E704
    def hide_filter(self, focus: bool = True) -> None: pass  # noqa: E704
    def toggle_filter(self) -> None: pass       # noqa: E704
    def ensure_view(self) -> None: pass         # noqa: E704

    def _on_theme(self) -> None:
        self.header.restyle(self.icon)

    def shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

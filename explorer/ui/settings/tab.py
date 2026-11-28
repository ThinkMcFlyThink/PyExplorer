"""
SettingsTab - every setting, in a utility tab (Ctrl+,).

    ┌ ⚙ Settings  · changes apply straight away              [⌕ Find a setting      ] ┐
    │ ◐ Appearance │  Appearance                                                       │
    │ ▭ Toolbar    │  Colours, fonts and icons.                                        │
    │ ≣ Views      │  THEME                                                            │
    │ ↖ Behaviour  │  ┌──────────────────────────────────────────────────────────────┐ │
    │ ▯ Sidebar    │  │ Colour theme                          [GitHub Light     ▾] ↺ │ │
    │ ▣ Terminal   │  │ System follows your desktop's own look…                      │ │
    │ …            │  ├──────────────────────────────────────────────────────────────┤ │

Everything on the pages comes from core/settings_schema.py: this file only
lays it out. Each row's editor is made by editors.py; the few pages that
need more than rows (Keyboard, Toolbar, saved logins) are separate widgets
(keyboard.py, toolbar_page.py, logins.py), listed in CUSTOM below.

A change is stored at once (and applied, through the schema's ``apply``
command); ↺ puts a setting back to its default. Typing in the search box
shows the matching settings from every page.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ...core import settings_schema as schema
from ...core.events import Topics
from ..utility.base import UtilityTab
from .editors import make_editor

CONTENT_WIDTH = 860            # the pages' maximum width: long lines are hard to read


def _custom_pages() -> Dict[str, Callable]:
    """Pages that are more than rows of settings: id -> widget class (imported on first use)."""
    from .keyboard import KeyboardPage
    from .logins import LoginsPage
    from .toolbar_page import ToolbarPage
    return {"keyboard": KeyboardPage, "toolbar": ToolbarPage, "logins": LoginsPage}


class SettingRow(QFrame):
    """One setting: its name and explanation on the left, its editor and ↺ on the right."""

    def __init__(self, tab: "SettingsTab", page: schema.Page, group: schema.Group, setting: schema.Setting,
                 first: bool) -> None:
        super().__init__(objectName="SettingRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("first", first)
        self.tab, self.page, self.group, self.setting = tab, page, group, setting
        ctx = tab.ctx
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(14)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.label = QLabel(setting.label, self, objectName="SettingLabel")
        self.label.setWordWrap(True)
        texts.addWidget(self.label)
        help_text = setting.help
        if setting.restart:
            help_text = (help_text + " " if help_text else "") + "Takes effect when PyExplorer starts again."
        if help_text:
            self.help = QLabel(help_text, self, objectName="SettingHelp")
            self.help.setWordWrap(True)
            texts.addWidget(self.help)
        row.addLayout(texts, 1)
        self.editor = make_editor(setting, ctx, self.commit)
        if self.editor is not None:
            row.addWidget(self.editor.widget, 0, Qt.AlignmentFlag.AlignVCenter)
            self.label.setBuddy(self.editor.widget)
        self.reset_btn = QToolButton(self, objectName="SettingReset", autoRaise=True)
        self.reset_btn.setIcon(ctx.icons.symbol("undo", force=True))
        self.reset_btn.clicked.connect(self.reset)
        policy = self.reset_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)                # editors stay lined up in one column
        self.reset_btn.setSizePolicy(policy)
        row.addWidget(self.reset_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        if setting.kind == "action":
            self.reset_btn.hide()
        self.refresh()

    # -- value -----------------------------------------------------------------------
    def commit(self, value: Any) -> None:
        s, ctx = self.setting, self.tab.ctx
        if s.kind == "action":
            if s.apply:
                getattr(ctx.commands, s.apply)()
            return
        ctx.settings.set(s.key, value)
        if s.apply:
            getattr(ctx.commands, s.apply)()
        self._show_reset()

    def reset(self) -> None:
        ctx = self.tab.ctx
        ctx.settings.reset_key(self.setting.key)
        if self.setting.apply:
            getattr(ctx.commands, self.setting.apply)()
        self.refresh()

    def refresh(self) -> None:
        if self.editor is not None and self.setting.key:
            self.editor.set(self.tab.ctx.settings.get(self.setting.key))
        self._show_reset()

    def _show_reset(self) -> None:
        s, settings = self.setting, self.tab.ctx.settings
        if s.kind == "action" or not s.key:
            return
        default = settings.default(s.key)
        changed = settings.get(s.key) != default
        self.reset_btn.setVisible(changed)
        shown = default if not isinstance(default, bool) else ("on" if default else "off")
        if s.kind == "choice":
            from .editors import choice_options
            shown = dict(choice_options(s, self.tab.ctx)).get(default, shown)
        self.reset_btn.setToolTip(f"Reset to the default ({shown or 'empty'})")

    def matches(self, query: str) -> bool:
        return schema.setting_matches(query, self.page, self.group, self.setting)


class SettingsPage(QWidget):
    """A page: its title, its groups (each a card of rows), and maybe a custom editor."""

    def __init__(self, tab: "SettingsTab", page: schema.Page) -> None:
        super().__init__(objectName="SettingsPage")
        self.tab, self.page = tab, page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel(page.title, self, objectName="SettingPageTitle")
        layout.addWidget(self.title)
        self.summary = QLabel(page.summary, self, objectName="SettingHelp")
        self.summary.setWordWrap(True)
        layout.addSpacing(2)
        layout.addWidget(self.summary)
        self.sections: List[tuple] = []                     # (group label, card, [rows])
        for group in page.groups:
            label = QLabel(group.title.upper(), self, objectName="SettingGroup")
            layout.addSpacing(22)
            layout.addWidget(label)
            layout.addSpacing(8)
            card = QFrame(self, objectName="SettingCard")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            rows_box = QVBoxLayout(card)
            rows_box.setContentsMargins(0, 0, 0, 0)
            rows_box.setSpacing(0)
            rows = []
            for n, setting in enumerate(group.settings):
                row = SettingRow(tab, page, group, setting, first=(n == 0))
                rows_box.addWidget(row)
                rows.append(row)
                if setting.key:
                    tab.rows[setting.key] = row
            layout.addWidget(card)
            card.note_label = None
            if group.note:                                  # a line of help under the whole group
                note = QLabel(group.note, self, objectName="SettingHelp")
                note.setWordWrap(True)
                layout.addSpacing(6)
                layout.addWidget(note)
                card.note_label = note
            self.sections.append((label, card, rows))
        self.custom = None
        if page.custom:
            factory = _custom_pages().get(page.custom)
            if factory is not None:
                self.custom = factory(tab.ctx, self)
                layout.addSpacing(22)
                layout.addWidget(self.custom)
        layout.addStretch(1)
        self.restyle()

    def restyle(self) -> None:
        font = QFont(QApplication.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.45)
        self.title.setFont(font)

    def filter(self, query: str) -> bool:
        """Show only what matches ``query`` ("" = everything). True if anything is shown."""
        page_hit = bool(query) and schema.matches(query, self.page.title, self.page.keywords)
        any_shown = False
        for label, card, rows in self.sections:
            shown = 0
            for row in rows:
                visible = not query or page_hit or row.matches(query)
                row.setVisible(visible)
                if visible:
                    row.setProperty("first", shown == 0)
                    row.style().unpolish(row)
                    row.style().polish(row)
                    shown += 1
            label.setVisible(shown > 0)
            card.setVisible(shown > 0)
            if card.note_label is not None:
                card.note_label.setVisible(shown > 0)
            any_shown = any_shown or shown > 0
        if self.custom is not None:
            custom_hit = self.custom.filter("" if page_hit else query)
            self.custom.setVisible(custom_hit)
            any_shown = any_shown or custom_hit
        self.summary.setVisible(not query)
        return any_shown or page_hit


class SettingsTab(UtilityTab):
    KIND = "settings"
    TITLE = "Settings"
    SHAPE = "settings"

    def __init__(self, panel, ctx, state: Optional[dict] = None) -> None:
        self.rows: Dict[str, SettingRow] = {}
        self.pages: Dict[str, SettingsPage] = {}
        self._current = "appearance"
        super().__init__(panel, ctx, state)
        self._unsubs.append(ctx.bus.subscribe(Topics.SETTINGS_CHANGED, self._on_setting))

    # ==================================================================
    # Widgets
    # ==================================================================
    def build(self, body: QVBoxLayout) -> None:
        self.header.set_subtitle("Changes apply straight away")
        self.search = QLineEdit(self, objectName="SettingsSearch", clearButtonEnabled=True)
        self.search.setPlaceholderText("Find a setting")
        self.search.addAction(self.ctx.icons.symbol("search", force=True), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self._filter)
        self.header.buttons.addWidget(self.search)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.nav = QListWidget(self, objectName="SettingsNav")
        self.nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.nav.setFixedWidth(210)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        row.addWidget(self.nav)

        self.scroll = QScrollArea(self, objectName="SettingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="SettingsContent")
        outer = QHBoxLayout(content)
        outer.setContentsMargins(32, 24, 32, 32)
        column = QWidget(content)
        column.setMaximumWidth(CONTENT_WIDTH)
        column.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.column = QVBoxLayout(column)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(36)
        outer.addWidget(column, 1)
        outer.addStretch(0)
        self.scroll.setWidget(content)
        row.addWidget(self.scroll, 1)
        body.addLayout(row, 1)

        # Pages are built when first shown (or all at once for a search): fewer widgets
        # means a faster tab, and a faster theme switch while it is open.
        self._schema = {page.id: page for page in schema.pages()}
        for page in self._schema.values():
            item = QListWidgetItem(self.ctx.icons.symbol(page.icon, force=True), page.title)
            item.setData(Qt.ItemDataRole.UserRole, page.id)
            self.nav.addItem(item)
        self.empty = QLabel("No settings match. Try another word.", self, objectName="SettingHelp")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        self.column.addWidget(self.empty)
        self.column.addStretch(1)
        self.nav.currentItemChanged.connect(self._on_nav)

    # ==================================================================
    # Pages and search
    # ==================================================================
    def page_ids(self) -> List[str]:
        return list(self._schema)

    def page(self, page_id: str) -> SettingsPage:
        """The page's widget, built on first use (in schema order in the column)."""
        widget = self.pages.get(page_id)
        if widget is None:
            widget = self.pages[page_id] = SettingsPage(self, self._schema[page_id])
            order = list(self._schema)
            before = sum(1 for pid in self.pages if pid != page_id and order.index(pid) < order.index(page_id))
            self.column.insertWidget(before, widget)
            widget.hide()
        return widget

    def build_all(self) -> None:
        for page_id in self._schema:
            self.page(page_id)

    def show_page(self, page_id: str) -> None:
        if page_id not in self._schema:
            page_id = "appearance"
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            for page in self.pages.values():
                page.filter("")
        self._current = page_id
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == page_id:
                self.nav.blockSignals(True)
                self.nav.setCurrentRow(i)
                self.nav.blockSignals(False)
        self._show_only(page_id)

    def _show_only(self, page_id: str) -> None:
        self.page(page_id)
        for pid, page in self.pages.items():
            page.setVisible(pid == page_id)
        self.empty.hide()
        self.scroll.verticalScrollBar().setValue(0)

    def _on_nav(self, current, _previous) -> None:
        if current is not None:
            self.show_page(current.data(Qt.ItemDataRole.UserRole))

    def _filter(self, text: str) -> None:
        query = text.strip()
        if not query:
            for page in self.pages.values():
                page.filter("")
            self.show_page(self._current)
            return
        self.build_all()                                  # a search looks at every page
        self.nav.blockSignals(True)
        self.nav.clearSelection()
        self.nav.setCurrentRow(-1)
        self.nav.blockSignals(False)
        found = 0
        for page in self.pages.values():
            hit = page.filter(query)
            page.setVisible(hit)
            found += hit
        self.empty.setVisible(found == 0)
        self.scroll.verticalScrollBar().setValue(0)

    # ==================================================================
    # Keeping up with changes made elsewhere (menus, shortcuts, Reset…)
    # ==================================================================
    # settings a custom page shows -> that page's id
    CUSTOM_KEYS = {"shortcuts": "keyboard", "toolbar.items": "toolbar"}

    def _on_setting(self, key: str, value=None) -> None:
        row = self.rows.get(key)
        if row is not None:
            row.refresh()
            return
        page = self.pages.get(self.CUSTOM_KEYS.get(key, ""))
        if page is not None and page.custom is not None:
            page.custom.refresh()

    def _on_theme(self) -> None:
        super()._on_theme()
        for page in self.pages.values():
            page.restyle()
            if page.custom is not None:
                page.custom.on_theme()
        self.nav.doItemsLayout()                   # padding / font may differ: measure the rows again

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_measured", False):
            # Qt measured the page list's rows before the theme's padding reached it.
            self._measured = True
            self.nav.doItemsLayout()

    # ==================================================================
    # Tab protocol
    # ==================================================================
    @property
    def path(self) -> str:
        return ""                                  # (not about a folder: the sidebar highlights nothing)

    def focus_view(self) -> None:
        self.search.setFocus()

    def get_state(self) -> dict:
        return {"utility": self.KIND, "page": self._current}

    def restore(self, state: dict) -> None:
        self.show_page(state.get("page") or self._current)       # (build() has made every page by now)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.search.text():
            self.search.clear()
            return
        super().keyPressEvent(event)

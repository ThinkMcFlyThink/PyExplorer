"""
The main toolbar: described as data, built from it, and customizable.

    toolbar.items       the layout, a list of item ids ([] = DEFAULT below)
    toolbar.style       auto | icons | text_beside | text_under
    toolbar.icon_size   16 | 18 | 22

An item id is one of
    an action id            "edit.copy", "window.split"...  a button for that action (actions.py)
    "@new" "@sort" ...      a ready-made widget (WIDGETS below)
    "|"                     a separator line
    "~"                     flexible space: pushes what follows to the right

Settings -> Toolbar edits the list (ui/settings/toolbar_page.py). Right-click the
toolbar for its style, icon size and "Customize toolbar…". Ids that no longer
exist are skipped, so an older settings file can never break the toolbar.

Buttons share the actions' QActions, so their state (checked, enabled),
tooltips and shortcuts are always those of the menus and the keyboard.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QMenu, QSizePolicy, QToolBar, QToolButton,
                               QWidget)
from shiboken6 import isValid

from ..core.events import Topics

SEPARATOR, SPACER = "|", "~"
DEFAULT: List[str] = [
    "@new", SEPARATOR,
    "edit.cut", "edit.copy", "edit.paste", "edit.rename", "edit.trash", SEPARATOR,
    "@sort", "@views", "view.hidden", SEPARATOR,
    "window.sidebar", "window.split", "window.terminal", SEPARATOR,
    "tools.rename", "tools.history", "tools.transfers",
    SPACER, "@search", "net.new", "tools.preferences",
]
STYLES = [("auto", "Icons, with labels on menu buttons"), ("icons", "Icons only"),
          ("text_beside", "Icons and labels"), ("text_under", "Labels under icons")]
ICON_SIZES = [(16, "Small"), (18, "Medium"), (22, "Large")]
_QT_STYLE = {"icons": Qt.ToolButtonStyle.ToolButtonIconOnly,
             "text_beside": Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
             "text_under": Qt.ToolButtonStyle.ToolButtonTextUnderIcon}
# Actions that make no sense as buttons (dozens of themes; sort keys live in "@sort").
HIDDEN_PREFIXES = ("theme.", "sort.")


def items(settings) -> List[str]:
    """The current layout (the default if the user hasn't changed it)."""
    saved = settings.get("toolbar.items") or []
    return list(saved) if saved else list(DEFAULT)


# ---------------------------------------------------------------------------
# Ready-made widgets
# ---------------------------------------------------------------------------
def _menu_button(bar: "MainToolbar", text: str, icon: QIcon, tip: str, fill: Callable[[QMenu], None]) -> QToolButton:
    """A button that opens a menu (New ▾, Sort ▾). The menu is filled when it opens."""
    button = QToolButton(bar, objectName="ToolbarMenu", text=text, toolTip=tip)
    button.setIcon(icon)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    menu.aboutToShow.connect(lambda: (menu.clear(), fill(menu), bar.ctx.actions.sync_checks()))
    button.setMenu(menu)
    button.setProperty("menuButton", True)
    return button


def _new_button(bar: "MainToolbar") -> QWidget:
    a = bar.ctx.actions.qaction

    def fill(menu: QMenu) -> None:
        for action_id in ("file.new_folder", "file.new_file"):
            menu.addAction(a(action_id))
        menu.addSeparator()
        menu.addAction(a("tab.new"))
    return _menu_button(bar, "New", bar.ctx.icons.symbol("plus", "document-new"), "Create a folder, a file or a tab",
                        fill)


def _sort_button(bar: "MainToolbar") -> QWidget:
    a = bar.ctx.actions.qaction

    def fill(menu: QMenu) -> None:
        for key in ("name", "size", "type", "modified"):
            menu.addAction(a(f"sort.{key}"))
        menu.addSeparator()
        menu.addAction(a("view.sort_desc"))
        menu.addAction(a("view.folders_first"))
    return _menu_button(bar, "Sort", bar.ctx.icons.symbol("sort", "view-sort"), "Sort by name, size, type or date",
                        fill)


def _view_switch(bar: "MainToolbar") -> QWidget:
    """The view modes as one segmented control: [▦|☰|≣|⊢]. Each segment is the
    view mode's own action, so it is checked exactly when that mode is on."""
    from .views.registry import VIEW_TYPES
    frame = QFrame(bar, objectName="Segmented")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    row = QHBoxLayout(frame)
    row.setContentsMargins(1, 1, 1, 1)
    row.setSpacing(0)
    for n, mode in enumerate(VIEW_TYPES):
        seg = QToolButton(frame, objectName="Segment")
        seg.setDefaultAction(bar.ctx.actions.qaction(f"view.{mode}"))
        seg.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        seg.setProperty("position", "first" if n == 0 else "last" if n == len(VIEW_TYPES) - 1 else "middle")
        row.addWidget(seg)
    frame.setToolTip("View mode")
    return frame


def _zoom_slider(bar: "MainToolbar") -> QWidget:
    from PySide6.QtWidgets import QSlider
    from .views.registry import view_class
    ctx = bar.ctx
    slider = QSlider(Qt.Orientation.Horizontal, bar, objectName="ToolbarZoom", toolTip="Zoom (Ctrl + mouse wheel)")
    slider.setFixedWidth(100)

    def sync(**_) -> None:
        mode = ctx.settings.get("view.mode", "details")
        slider.blockSignals(True)
        slider.setRange(0, len(view_class(mode).ZOOM_SIZES) - 1)
        slider.setValue(ctx.settings.get(f"view.zoom.{mode}", 1))
        slider.blockSignals(False)
    slider.valueChanged.connect(ctx.commands.set_zoom)
    bar.listen(Topics.ZOOM_CHANGED, sync)
    bar.listen(Topics.VIEW_MODE_CHANGED, sync)
    sync()
    return slider


class QuickSearch(QLineEdit):
    """Type and press Enter: a Search tab for the current folder, looking for that name."""

    def __init__(self, bar: "MainToolbar") -> None:
        super().__init__(bar, objectName="QuickSearch", clearButtonEnabled=True)
        self.ctx = bar.ctx
        self.addAction(self.ctx.icons.symbol("search", force=True), QLineEdit.ActionPosition.LeadingPosition)
        self.setToolTip("Search this folder: type a name and press Enter (Ctrl+F for the full Search tab)")
        self.returnPressed.connect(self._search)
        bar.listen(Topics.ACTIVE_LOCATION_CHANGED, self._on_location)
        self._on_location()

    def _on_location(self, **_) -> None:
        tab = self.ctx.commands.active_tab() if self.ctx.main else None
        folder = getattr(tab, "current_folder", None) if tab is not None else None
        name = tab.title if folder and tab is not None else "the computer"
        self.setPlaceholderText(f"Search {name}")

    def _search(self) -> None:
        text = self.text().strip()
        if text:
            self.clear()
            self.ctx.commands.open_search(name=text)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            tab = self.ctx.commands.active_tab()
            if tab is not None:
                tab.focus_view()
            return
        super().keyPressEvent(event)


# id -> (label in the customize list, icon shape, factory)
WIDGETS: Dict[str, Tuple[str, str, Callable[["MainToolbar"], QWidget]]] = {
    "@new": ("New ▾ (folder, file, tab)", "plus", _new_button),
    "@sort": ("Sort ▾", "sort", _sort_button),
    "@views": ("View modes (segmented)", "view-icons", _view_switch),
    "@search": ("Search box", "search", QuickSearch),
    "@zoom": ("Zoom slider", "zoom-in", _zoom_slider),
}


def catalogue(ctx) -> List[Tuple[str, str, QIcon]]:
    """Everything that can go on the toolbar: (id, label, icon), for Settings -> Toolbar."""
    from ..actions import plain_label
    icons = ctx.icons
    out = [(wid, label, icons.symbol(shape, force=True)) for wid, (label, shape, _f) in WIDGETS.items()]
    for action in ctx.actions.all():
        if not action.id.startswith(HIDDEN_PREFIXES):
            out.append((action.id, plain_label(action.label), action.qaction.icon()))
    out.append((SEPARATOR, "Separator", icons.symbol("minus", force=True)))
    out.append((SPACER, "Flexible space", icons.symbol("more", force=True)))
    return out


def label_of(ctx, item_id: str) -> str:
    from ..actions import plain_label
    if item_id in WIDGETS:
        return WIDGETS[item_id][0]
    if item_id == SEPARATOR:
        return "Separator"
    if item_id == SPACER:
        return "Flexible space"
    action = ctx.actions.get(item_id)
    return plain_label(action.label) if action else item_id


# ---------------------------------------------------------------------------
# The toolbar
# ---------------------------------------------------------------------------
class MainToolbar(QToolBar):
    def __init__(self, ctx, window) -> None:
        super().__init__("Toolbar", window, objectName="MainToolbar")
        self.ctx = ctx
        self.setMovable(False)
        self.setFloatable(False)
        # (Right-click: the event goes on to the window, whose createPopupMenu() asks context_menu().)
        self._widget_unsubs: List[Callable[[], None]] = []
        self.widgets: List[QWidget] = []                # what rebuild() made (spacers, menu buttons...)
        self._unsubs = [ctx.bus.subscribe(Topics.SETTINGS_CHANGED, self._on_setting)]
        self.destroyed.connect(lambda *_: self._shutdown())
        self.rebuild()

    # -- building --------------------------------------------------------------
    def listen(self, topic: str, callback) -> None:
        """For widgets: subscribe to an event until the next rebuild."""
        self._widget_unsubs.append(self.ctx.bus.subscribe(topic, callback))

    def _add_widget(self, widget: QWidget) -> None:
        self.addWidget(widget)
        self.widgets.append(widget)

    def rebuild(self) -> None:
        for unsub in self._widget_unsubs:
            unsub()
        self._widget_unsubs = []
        # clear() deletes the separators and widget actions the toolbar made, but only lets go
        # of the widgets themselves (they'd live on, hidden): delete those. The shared QActions
        # belong to the window and stay.
        old, self.widgets = self.widgets, []
        self.clear()
        for widget in old:
            if isValid(widget):
                widget.deleteLater()
        s = self.ctx.settings
        style = s.get("toolbar.style", "auto")
        size = int(s.get("toolbar.icon_size", 18) or 18)
        self.setIconSize(QSize(size, size))
        self.setToolButtonStyle(_QT_STYLE.get(style, Qt.ToolButtonStyle.ToolButtonIconOnly))
        self.setProperty("labels", style)
        previous = None
        for item in items(s):
            if item == SEPARATOR:
                if previous not in (None, SEPARATOR, SPACER):
                    self.addSeparator()
                previous = item
                continue
            if item == SPACER:
                spacer = QWidget(self)
                spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                self._add_widget(spacer)
                previous = item
                continue
            widget = self._make(item, style)
            if widget is None:
                continue
            if isinstance(widget, QAction):
                self.addAction(widget)
            else:
                self._add_widget(widget)
            previous = item
        ext = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext is not None:                             # the "more" button when the window is narrow
            ext.setIcon(self.ctx.icons.symbol("more", force=True))
        self.style().unpolish(self)
        self.style().polish(self)

    def _make(self, item: str, style: str):
        if item in WIDGETS:
            widget = WIDGETS[item][2](self)
            if isinstance(widget, QToolButton):        # menu buttons: labelled unless "icons only"
                labelled = {"auto": Qt.ToolButtonStyle.ToolButtonTextBesideIcon, "icons":
                            Qt.ToolButtonStyle.ToolButtonIconOnly}
                widget.setToolButtonStyle(labelled.get(style, _QT_STYLE.get(style)))
                widget.setIconSize(self.iconSize())
            elif isinstance(widget, QFrame):
                for button in widget.findChildren(QToolButton):
                    button.setIconSize(self.iconSize())
            return widget
        return self.ctx.actions.qaction(item)            # None if it no longer exists

    # -- reacting --------------------------------------------------------------
    def _on_setting(self, key: str, value=None) -> None:
        if key in ("toolbar.items", "toolbar.style", "toolbar.icon_size"):
            self.rebuild()

    def _shutdown(self) -> None:
        for unsub in self._unsubs + self._widget_unsubs:
            unsub()
        self._unsubs, self._widget_unsubs = [], []

    # -- right-click ---------------------------------------------------------------
    def context_menu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Style, icon size, customize, and the window's bars."""
        ctx = self.ctx
        s = ctx.settings
        menu = QMenu(parent or self)
        labels = menu.addMenu("Labels")
        for value, label in STYLES:
            act = labels.addAction(label, lambda v=value: s.set("toolbar.style", v))
            act.setCheckable(True)
            act.setChecked(s.get("toolbar.style", "auto") == value)
        sizes = menu.addMenu("Icon size")
        for value, label in ICON_SIZES:
            act = sizes.addAction(label, lambda v=value: s.set("toolbar.icon_size", v))
            act.setCheckable(True)
            act.setChecked(int(s.get("toolbar.icon_size", 18) or 18) == value)
        menu.addAction(ctx.actions.qaction("toolbar.customize"))
        menu.addSeparator()
        for action_id in ("window.toolbar", "window.statusbar", "window.sidebar"):
            menu.addAction(ctx.actions.qaction(action_id))
        ctx.actions.sync_checks()
        return menu

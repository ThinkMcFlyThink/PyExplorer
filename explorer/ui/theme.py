"""
ThemeManager - colours and fonts for the whole app.

The colours live in themes.py (one short "spec" per theme, most modelled on
popular VS Code themes). Applying a theme:
  1. switches Qt to its "Fusion" style (looks identical on every OS),
  2. fills a QPalette from the colours (this restyles ALL standard widgets),
  3. adds a stylesheet that draws the window "chrome" the same way for every
     theme: 1-pixel lines between areas, VS Code-like tabs, quiet scroll bars.
The special theme "system" keeps your operating system's own look.

Our hand-painted widgets (breadcrumbs, folder icons, terminal) read colours
from ``theme.palette[...]``. Switching theme publishes THEME_CHANGED so they
repaint.

To add a theme: add a spec to themes.SPECS (about a dozen colours), done.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Dict, Optional

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from ..core.events import Topics
from .themes import PALETTES, display_name, grouped  # noqa: F401 - re-exported for menus/dialogs

THEME_NAMES = ["system"] + list(PALETTES)


def shade(color: str, factor: float) -> str:
    """Darken (factor < 1) or lighten (factor > 1) a colour."""
    c = QColor(color)
    return (c.lighter(int(factor * 100)) if factor >= 1 else c.darker(int(100 / max(factor, 0.01)))).name()


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a towards b by t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _palette_from_qt(pal: QPalette) -> Dict[str, str]:
    """Build our colour names from the OS palette (for the "system" theme)."""
    from .themes import build
    g = QPalette.ColorGroup.Active
    R = QPalette.ColorRole

    def col(role):
        return pal.color(g, role).name()
    bg, fg, base = col(R.Window), col(R.WindowText), col(R.Base)
    dark = QColor(bg).lightness() < 128
    spec = {"name": "System", "bg": base, "chrome": bg, "fg": col(R.Text), "dim": mix(col(R.Text), base, 0.45),
            "accent": col(R.Highlight), "select": col(R.Highlight), "select_fg": col(R.HighlightedText),
            "folder": "#e3b04b" if dark else "#e0a930",
            "red": "#f47067" if dark else "#c93c37", "green": "#57ab5a" if dark else "#2d8a3e",
            "yellow": "#e5c07b" if dark else "#a66a00", "blue": "#61afef" if dark else "#1f6fd1",
            "magenta": "#c678dd" if dark else "#8e44ad", "cyan": "#56b6c2" if dark else "#138a96"}
    p = build(spec)
    # The OS colours themselves, unchanged (build() may nudge colours for contrast).
    p.update({
        "bg": base, "surface": bg, "surface2": mix(bg, fg, 0.08), "border": mix(bg, fg, 0.2),
        "fg": col(R.Text), "fg_dim": mix(col(R.Text), base, 0.45), "accent": col(R.Highlight),
        "accent_fg": col(R.HighlightedText), "select": col(R.Highlight), "select_fg": col(R.HighlightedText),
        "hover": mix(base, col(R.Highlight), 0.12), "alt_row": col(R.AlternateBase), "entry": base,
        "page": "#e9edf3" if dark else "#ffffff",
        "terminal_bg": "#14161a" if dark else "#fbfbfc", "terminal_fg": "#d3d9e3" if dark else "#1f2329",
        "accent_soft": mix(bg, col(R.Highlight), 0.18), "accent_soft_hover": mix(bg, col(R.Highlight), 0.26),
        "card": mix(base, col(R.Text), 0.03), "line_soft": mix(mix(bg, fg, 0.2), base, 0.35),
    })
    return p


def _svg_icon(name: str, color: str, svg: str) -> str:
    """Write a tiny SVG (stylesheets need image FILES) and return its path, with
    forward slashes as Qt style sheets expect. One file per colour, reused."""
    folder = os.path.join(tempfile.gettempdir(), f"pyexplorer-theme-{os.getuid() if hasattr(os, 'getuid') else 0}")
    os.makedirs(folder, exist_ok=True)
    digest = hashlib.md5(f"{name}{color}{svg}".encode()).hexdigest()[:10]
    path = os.path.join(folder, f"{name}-{digest}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg.replace("COLOR", color))
    return path.replace("\\", "/")


_CLOSE_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
              '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="COLOR" stroke-width="1.4" '
              'stroke-linecap="round" fill="none"/></svg>')
_UP_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
           '<path d="M2 6.5l3-3 3 3" stroke="COLOR" stroke-width="1.4" stroke-linecap="round" '
           'stroke-linejoin="round" fill="none"/></svg>')
_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
              '<path d="M3.2 7.4l2.6 2.6 5-5.6" stroke="COLOR" stroke-width="1.8" stroke-linecap="round" '
              'stroke-linejoin="round" fill="none"/></svg>')
_ARROW_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
              '<path d="M2 3.5l3 3 3-3" stroke="COLOR" stroke-width="1.4" stroke-linecap="round" '
              'stroke-linejoin="round" fill="none"/></svg>')
_MENU_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                   '<path d="M3.5 8.5l3 3 6-6.5" stroke="COLOR" stroke-width="1.5" stroke-linecap="round" '
                   'stroke-linejoin="round" fill="none"/></svg>')
_MENU_DOT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                 '<circle cx="8" cy="8" r="3" fill="COLOR"/></svg>')


def metrics(font: Optional[QFont]) -> Dict[str, int]:
    """Sizes the style sheet derives from the interface font, so every control in a
    row has the same height and nothing is clipped, at any font size:

        text        content height of text fields, drop-downs, number boxes, buttons
        control     their full height (text + 2 x 3px padding + 2 x 1px border)
        cell_pad    side padding of cells in Details / Tree / table views (air between columns)
        header_pad  side padding of column headers: cell_pad + the 3px Qt draws text inside a
                    cell, so a column's title starts (or ends) exactly where its text does
        group_title room for a group box's title inside its frame
    """
    height = QFontMetrics(font).height() if font is not None else 16
    text = max(16, height + 2)
    cell_pad = 3
    return {"text": text, "control": text + 8, "cell_pad": cell_pad, "header_pad": cell_pad + 3,
            "group_title": height + 14}


class ThemeManager:
    def __init__(self, app: QApplication, ctx) -> None:
        self.app = app
        self.ctx = ctx
        # Remember the OS look before we change anything, for the "system" theme.
        self._native_style = app.style().name()
        self._native_palette = QPalette(app.palette())
        self._native_font = QFont(app.font())
        self.name = ctx.settings.get("appearance.theme", "dark")
        if self.name not in THEME_NAMES:
            self.name = "dark"
        self.palette: Dict[str, str] = {}

    # -- fonts -----------------------------------------------------------------
    # appearance.font_family     the font for the whole interface ("" = the system's)
    # appearance.terminal_font   the terminal's font, must be monospace ("" = the system's)
    # appearance.font_size       points for both (0 = the system's size)
    def _sized(self, font: QFont) -> QFont:
        size = int(self.ctx.settings.get("appearance.font_size", 0) or 0)
        if size > 0:
            font.setPointSize(size)
        return font

    def ui_font(self) -> QFont:
        font = QFont(self._native_font)
        family = self.ctx.settings.get("appearance.font_family", "") or ""
        if family and family in QFontDatabase.families():   # an uninstalled font falls back quietly
            font.setFamily(family)
        return self._sized(font)

    def mono_font(self) -> QFont:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        family = self.ctx.settings.get("appearance.terminal_font", "") or ""
        if family and family in QFontDatabase.families():
            font.setFamily(family)
        if not self.ctx.settings.get("appearance.font_size", 0):
            font.setPointSize(self._native_font.pointSize())
        return self._sized(font)

    # -- applying ----------------------------------------------------------------
    def apply(self, name: str = None) -> None:
        if name:
            self.name = name if name in THEME_NAMES else "dark"
            self.ctx.settings.set("appearance.theme", self.name)
        app = self.app
        font = self.ui_font()
        app.setFont(font)                  # every widget follows, unless it set its own font
        sizes = metrics(font)

        if self.name == "system":
            app.setStyle(QStyleFactory.create(self._native_style) or QStyleFactory.create("Fusion"))
            app.setPalette(self._native_palette)
            self.palette = _palette_from_qt(self._native_palette)
            app.setStyleSheet(self._stylesheet(self.palette, sizes, minimal=True))
        else:
            p = self.palette = dict(PALETTES[self.name])
            app.setStyle(QStyleFactory.create("Fusion"))
            app.setPalette(self._qpalette(p))
            app.setStyleSheet(self._stylesheet(p, sizes))
        self._hint_color_scheme()
        self.ctx.bus.publish(Topics.THEME_CHANGED, name=self.name)

    def _hint_color_scheme(self) -> None:
        """Tell the OS whether we are light or dark, so window title bars match the theme
        (Windows 11, macOS; GNOME/KDE through their portal). "System" leaves it to the OS."""
        try:
            from PySide6.QtCore import Qt
            hints = QGuiApplication.styleHints()
            if self.name == "system":
                hints.unsetColorScheme()
            else:
                hints.setColorScheme(Qt.ColorScheme.Dark if self.is_dark() else Qt.ColorScheme.Light)
        except (AttributeError, RuntimeError):
            pass                            # Qt older than 6.8: the OS decides

    @staticmethod
    def _qpalette(p: Dict[str, str]) -> QPalette:
        pal = QPalette()
        R = QPalette.ColorRole
        roles = {
            R.Window: p["surface"], R.WindowText: p["fg"], R.Base: p["bg"], R.AlternateBase: p["alt_row"],
            R.ToolTipBase: p["surface2"], R.ToolTipText: p["fg"], R.Text: p["fg"], R.Button: p["surface2"],
            R.ButtonText: p["fg"], R.BrightText: p["error"], R.Highlight: p["select"],
            R.HighlightedText: p["select_fg"], R.Link: p["accent"], R.PlaceholderText: p["fg_dim"],
            R.Light: shade(p["surface2"], 1.3), R.Midlight: shade(p["surface2"], 1.15), R.Mid: p["border"],
            R.Dark: shade(p["surface"], 0.7), R.Shadow: p["border"],     # Fusion's frame lines
        }
        for role, color in roles.items():
            pal.setColor(role, QColor(color))
        dis = QPalette.ColorGroup.Disabled
        for role in (R.WindowText, R.Text, R.ButtonText):
            pal.setColor(dis, role, QColor(p["fg_dim"]))
        # Keep the selection colour when a view loses focus (looks calmer, KDE-style).
        pal.setColor(QPalette.ColorGroup.Inactive, R.Highlight, QColor(p["select_inactive"]))
        pal.setColor(QPalette.ColorGroup.Inactive, R.HighlightedText, QColor(p["fg"]))
        return pal

    @staticmethod
    def _stylesheet(p: Dict[str, str], m: Optional[Dict[str, int]] = None, minimal: bool = False) -> str:
        """The "system" theme only gets a few touches (the OS style does the rest).
        Every other theme gets the full set below. Layout of the window chrome:

            menu bar + toolbar      chrome, 1px line below
            sidebar | panels        sidebar: chrome; 1px lines between areas
            tab strip               chrome; 1px line below that the current tab "opens" into
            address bar + view      bg (the current tab's colour, so they read as one sheet)
            status bar              chrome, 1px line above

        ``m`` (from metrics()) keeps every control one height: text fields, drop-downs,
        number boxes and buttons line up in any row, whatever the font.
        """
        m = m or metrics(None)
        line = f"1px solid {p['border']}"
        soft = f"1px solid {p['line_soft']}"
        chevron = _svg_icon("arrow", p["fg_dim"], _ARROW_SVG)
        check_menu = _svg_icon("mcheck", p["fg"], _MENU_CHECK_SVG)
        check_menu_sel = _svg_icon("mcheck", p["select_fg"], _MENU_CHECK_SVG)
        dot_menu = _svg_icon("mdot", p["fg"], _MENU_DOT_SVG)
        # -- shared by every theme, "system" included: our own widgets ------------------------
        sheet = f"""
        QLabel#Dim {{ color: {p['fg_dim']}; }}
        QTabBar::tab {{ min-width: 90px; max-width: 240px; }}
        QTreeWidget#Sidebar {{ padding: 6px 4px; }}
        QTreeWidget#Sidebar::item {{ padding: 3px 2px; }}
        QSplitter::handle:horizontal {{ width: 1px; }}
        QSplitter::handle:vertical {{ height: 1px; }}

        /* utility tabs: tinted header band, panels, footers */
        QFrame#UtilityHeader {{ background: {p['utility_bg']}; border: none;
                                border-bottom: 1px solid {p['utility_line']}; }}
        QLabel#UtilityTitle {{ color: {p['fg']}; background: transparent; }}
        QLabel#UtilitySubtitle {{ color: {p['fg_dim']}; background: transparent; }}
        QFrame#UtilityPanel {{ background: {p['surface']}; border: none; border-bottom: {line}; }}
        QFrame#UtilityFooter {{ background: {p['surface']}; border: none; border-top: {line}; }}
        QLabel#UtilitySection {{ color: {p['fg_dim']}; font-weight: bold; background: transparent; }}
        QFrame#LocationChip {{ background: {p['surface2']}; border: {line}; border-radius: {m['control'] // 2 - 2}px; }}
        QFrame#LocationChip:hover {{ border-color: {p['accent']}; }}
        QLabel#ChipLabel {{ background: transparent; color: {p['fg']}; }}
        QToolButton#ChipClose {{ background: transparent; border: none; border-radius: 8px; color: {p['fg_dim']};
                                  padding: 0 4px; font-weight: bold; }}
        QToolButton#ChipClose:hover {{ background: {p['chrome_hover']}; color: {p['fg']}; }}
        QToolButton#FoldButton {{ padding-left: 0; padding-right: 6px; border: none; font-weight: bold;
                                   color: {p['fg_dim']}; background: transparent; }}
        QToolButton#FoldButton:hover {{ color: {p['fg']}; }}

        /* toolbar pieces (ui/toolbar.py) */
        QFrame#Segmented {{ background: transparent; border: {line}; border-radius: 7px; }}
        QFrame#Segmented QToolButton {{ border: none; border-left: {soft}; border-radius: 0; margin: 0;
                                         padding: 4px 6px; background: transparent; }}
        QFrame#Segmented QToolButton[position="first"] {{ border-left: none; border-top-left-radius: 6px;
                                                           border-bottom-left-radius: 6px; }}
        QFrame#Segmented QToolButton[position="last"] {{ border-top-right-radius: 6px;
                                                          border-bottom-right-radius: 6px; }}
        QFrame#Segmented QToolButton:hover {{ background: {p['chrome_hover']}; }}
        QFrame#Segmented QToolButton:checked {{ background: {p['accent_soft']}; }}
        QToolButton#ToolbarMenu {{ padding-right: 17px; }}
        QToolButton#ToolbarMenu::menu-indicator {{ image: url("{chevron}"); subcontrol-origin: padding;
                                                   subcontrol-position: center right; right: 4px;
                                                   width: 10px; height: 10px; }}
        QLineEdit#QuickSearch {{ min-width: 190px; max-width: 260px; }}

        /* Settings tab (ui/settings/) */
        QListWidget#SettingsNav {{ background: {p['surface']}; border: none; border-right: {line};
                                   padding: 10px 8px; outline: 0; }}
        QListWidget#SettingsNav::item {{ padding: 6px 8px; margin: 1px 0; border-radius: 6px; color: {p['fg']}; }}
        QListWidget#SettingsNav::item:hover {{ background: {p['chrome_hover']}; }}
        QListWidget#SettingsNav::item:selected {{ background: {p['select']}; color: {p['select_fg']}; }}
        QScrollArea#SettingsScroll {{ background: {p['bg']}; border: none; }}
        QWidget#SettingsContent {{ background: {p['bg']}; }}
        QLabel#SettingPageTitle {{ color: {p['fg']}; }}
        QLabel#SettingGroup {{ color: {p['fg_dim']}; font-weight: bold; }}
        QLabel#SettingLabel {{ color: {p['fg']}; }}
        QLabel#SettingHelp {{ color: {p['fg_dim']}; }}
        QFrame#SettingCard {{ background: {p['card']}; border: {line}; border-radius: 8px; }}
        QFrame#SettingRow {{ background: transparent; border: none; border-top: {soft}; }}
        QFrame#SettingRow[first="true"] {{ border-top: none; }}
        QToolButton#SettingReset {{ padding: 3px; border-radius: 5px; }}
        QTreeWidget#SettingsTable, QTableWidget#SettingsTable, QListWidget#SettingsList {{
            background: {p['card']}; border: {line}; border-radius: 8px; padding: 4px; outline: 0; }}
        QListWidget#SettingsList::item {{ padding: 4px 6px; border-radius: 5px; }}
        QPushButton#DangerButton {{ color: {p['error']}; }}
        """
        close = _svg_icon("close", p["fg_dim"], _CLOSE_SVG)
        close_hover = _svg_icon("close", p["fg"], _CLOSE_SVG)
        if minimal:
            return sheet + f"""
        QTabBar::close-button {{ image: url("{close}"); }}
        QTabBar::close-button:hover {{ image: url("{close_hover}"); }}
        QWidget#PanelFrame[marked="true"] {{ border-top: 2px solid {p['accent']}; }}
        QWidget#PanelFrame[marked="false"] {{ border-top: 2px solid transparent; }}
        QFrame#FilterBar, QFrame#NavBar {{ background: {p['surface']}; }}
        QTabBar {{ qproperty-drawBase: 1; }}
        """
        arrow = _svg_icon("arrow", p["fg_dim"], _ARROW_SVG)
        up = _svg_icon("up", p["fg_dim"], _UP_SVG)
        check = _svg_icon("check", p["accent_fg"], _CHECK_SVG)
        h = m["text"]                       # content height of every text control
        return sheet + f"""
        /* ---- window chrome ------------------------------------------------ */
        QMainWindow {{ background: {p['surface']}; }}
        QMainWindow::separator {{ background: {p['border']}; width: 1px; height: 1px; }}
        QMenuBar {{ background: {p['surface']}; padding: 2px 4px; }}
        QMenuBar::item {{ padding: 4px 9px; border-radius: 5px; background: transparent; }}
        QMenuBar::item:selected {{ background: {p['chrome_hover']}; }}
        QToolBar {{ background: {p['surface']}; border: none; border-bottom: {line}; spacing: 2px; padding: 4px 6px; }}
        QToolBar::separator {{ background: {p['border']}; width: 1px; margin: 6px 6px; }}
        QLineEdit#QuickSearch {{ border-radius: {m['control'] // 2}px; padding-left: 4px; }}
        QToolBar QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
        QToolBar[labels="text_beside"] QToolButton {{ padding: 4px 8px; }}
        QToolBar[labels="text_under"] QToolButton {{ padding: 3px 8px; }}
        QToolBar QToolButton#ToolbarMenu {{ padding: 4px 17px 4px 6px; }}
        QToolBar[labels="icons"] QToolButton#ToolbarMenu {{ padding: 4px 16px 4px 4px; }}
        QToolBar QToolButton:hover {{ background: {p['chrome_hover']}; }}
        QToolBar QToolButton:pressed {{ background: {p['border']}; }}
        QToolBar QToolButton:checked {{ background: {p['accent_soft']}; }}
        QToolBar QToolButton:checked:hover {{ background: {p['accent_soft_hover']}; }}
        QStatusBar {{ background: {p['surface']}; border-top: {line}; color: {p['fg_dim']}; }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel {{ color: {p['fg_dim']}; }}
        QDockWidget {{ titlebar-close-icon: none; }}
        QWidget#TerminalBar {{ background: {p['surface']}; border-top: {line}; border-bottom: {line}; }}
        QDockWidget::title {{ background: {p['surface']}; padding: 5px 8px; border-bottom: {line}; }}
        QSplitter::handle {{ background: {p['border']}; }}
        QWidget#PanelFrame {{ border: none; }}

        /* ---- tabs: VS Code style -------------------------------------------- */
        QWidget#TabStrip {{ background: {p['surface']}; border-bottom: {line}; }}
        QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
        QTabBar::tab {{ background: {p['surface']}; color: {p['fg_dim']}; padding: 6px 8px 6px 10px;
                        border: none; border-right: {line}; border-bottom: {line};
                        border-top: 2px solid transparent; }}
        QTabBar::tab:hover:!selected {{ background: {p['chrome_hover']}; color: {p['fg']}; }}
        QTabBar::tab:selected {{ background: {p['bg']}; color: {p['fg']};
                                 border-bottom: 1px solid {p['bg']}; border-top: 2px solid transparent; }}
        QWidget#PanelFrame[active="true"] QTabBar::tab:selected {{ border-top: 2px solid {p['accent']}; }}
        QTabBar::close-button {{ image: url("{close}"); subcontrol-position: right; border-radius: 4px;
                                 margin: 1px; }}
        QTabBar::close-button:hover {{ image: url("{close_hover}"); background: {p['chrome_hover']}; }}
        QTabBar QToolButton {{ background: {p['surface']}; border: none; border-bottom: {line}; }}
        QToolButton#NewTabButton {{ border: none; border-bottom: {line}; border-radius: 0; padding: 0; }}
        QToolButton#NewTabButton:hover {{ background: {p['chrome_hover']}; }}
        QTabWidget::pane {{ border: {line}; top: -1px; background: {p['bg']}; }}
        QTabWidget QTabBar::tab:selected {{ border-top: 2px solid {p['accent']}; }}

        /* ---- address bar + views ------------------------------------------------ */
        QFrame#NavBar, QFrame#FilterBar {{ background: {p['bg']}; }}
        QTreeView, QListView, QTableView {{ border: none; outline: 0; background: {p['bg']};
                                            alternate-background-color: {p['alt_row']}; }}
        QTreeView::item, QListView::item {{ border-radius: 3px; }}
        QTreeView::item, QTableView::item {{ padding-left: {m['cell_pad']}px; padding-right: {m['cell_pad']}px; }}
        QTreeView::item:hover, QListView::item:hover {{ background: {p['hover']}; }}
        QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
            background: {p['select']}; color: {p['select_fg']}; }}
        QTreeView::item:selected:!active, QListView::item:selected:!active, QTableView::item:selected:!active {{
            background: {p['select_inactive']}; color: {p['fg']}; }}
        QHeaderView {{ background: {p['bg']}; border: none; }}
        QHeaderView::section {{ background: {p['bg']}; color: {p['fg_dim']}; border: none;
                                border-right: 1px solid {p['line_soft']}; border-bottom: {line};
                                padding: 4px {m['header_pad']}px; }}
        QHeaderView::section:hover {{ background: {p['hover']}; color: {p['fg']}; }}
        QHeaderView::section:last {{ border-right: none; }}
        QTableCornerButton::section {{ background: {p['bg']}; border: none; border-bottom: {line}; }}

        /* ---- sidebar ---------------------------------------------------------- */
        QTreeWidget#Sidebar {{ background: {p['surface']}; border: none; }}
        QTreeWidget#Sidebar::item:hover {{ background: {p['chrome_hover']}; }}
        QTreeWidget#Sidebar::item:selected {{ background: {p['select']}; color: {p['select_fg']}; }}
        QTreeWidget#Sidebar::item:selected:!active {{ background: {p['select_inactive']}; color: {p['fg']}; }}

        /* ---- menus ------------------------------------------------------------ */
        QToolTip {{ background: {p['surface2']}; color: {p['fg']}; border: {line}; padding: 4px 6px; }}
        QMenu {{ background: {p['surface']}; color: {p['fg']}; border: {line}; padding: 4px; }}
        QMenu::item {{ padding: 5px 28px 5px 28px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {p['select']}; color: {p['select_fg']}; }}
        QMenu::item:disabled {{ color: {p['fg_dim']}; }}
        QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
        QMenu::icon {{ padding-left: 8px; }}
        QMenu::indicator {{ width: 16px; height: 16px; left: 2px; }}
        QMenu::indicator:non-exclusive:checked {{ image: url("{check_menu}"); }}
        QMenu::indicator:non-exclusive:checked:selected {{ image: url("{check_menu_sel}"); }}
        QMenu::indicator:exclusive:checked {{ image: url("{dot_menu}"); }}

        /* ---- controls: every text control is {m['control']}px tall --------------------------- */
        QToolButton {{ border: 1px solid transparent; border-radius: 5px; padding: 3px 6px; }}
        QToolButton:hover {{ background: {p['chrome_hover']}; }}
        QToolButton:pressed {{ background: {p['border']}; }}
        QToolButton:checked {{ background: {p['accent_soft']}; }}
        QToolButton::menu-indicator {{ image: none; }}
        QPushButton {{ background: {p['surface2']}; color: {p['fg']}; border: {line}; border-radius: 5px;
                       padding: 3px 14px; min-height: {h}px; }}
        QPushButton:hover {{ background: {p['chrome_hover']}; border-color: {p['fg_dim']}; }}
        QPushButton:pressed {{ background: {p['border']}; }}
        QPushButton:default {{ border-color: {p['accent']}; }}
        QPushButton:disabled {{ color: {p['fg_dim']}; }}
        QPushButton#DangerButton {{ border-color: {p['error']}; }}
        QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
            background: {p['entry']}; color: {p['fg']}; border: {line}; border-radius: 5px;
            padding: 3px 7px; selection-background-color: {p['select']}; selection-color: {p['select_fg']}; }}
        QLineEdit, QAbstractSpinBox, QComboBox {{ min-height: {h}px; }}
        QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
            border-color: {p['accent']}; }}
        QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color: {p['fg_dim']}; }}
        QAbstractSpinBox {{ padding-right: 22px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 18px;
                                                                     border: none; background: transparent;
                                                                     border-radius: 3px; }}
        QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin: 2px 2px 0 0; }}
        QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 2px 2px 0; }}
        QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {p['chrome_hover']}; }}
        QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
        QAbstractSpinBox::down-arrow {{ image: url("{arrow}"); width: 9px; height: 9px; }}
        QDateEdit::drop-down {{ subcontrol-origin: border; subcontrol-position: center right; width: 20px;
                                border: none; }}
        QDateEdit::down-arrow {{ image: url("{arrow}"); width: 10px; height: 10px; }}
        QComboBox {{ padding-right: 24px; }}
        QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: center right; border: none;
                                width: 22px; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 10px; }}
        QComboBox QAbstractItemView {{ background: {p['surface']}; border: {line}; outline: 0; padding: 2px;
                                       selection-background-color: {p['select']};
                                       selection-color: {p['select_fg']}; }}
        QCheckBox, QRadioButton {{ spacing: 7px; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {p['fg_dim']}; border-radius: 3px;
                                background: {p['entry']}; }}
        QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
        QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']};
                                        image: url("{check}"); }}
        QCheckBox::indicator:disabled {{ border-color: {p['border']}; }}
        QAbstractItemView::indicator {{ width: 14px; height: 14px; border: 1px solid {p['fg_dim']}; border-radius: 3px;
                                        background: {p['entry']}; }}
        QAbstractItemView::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']};
                                                image: url("{check}"); }}
        QGroupBox {{ border: {line}; border-radius: 8px; margin-top: 0; padding-top: {m['group_title']}px; }}
        QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left; left: 11px; top: 7px;
                            padding: 0; color: {p['fg_dim']}; font-weight: bold; }}
        QDialog {{ background: {p['surface']}; }}

        /* ---- scroll bars, progress, slider ------------------------------------------ */
        QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
        QScrollBar::handle {{ background: {p['scroll']}; border-radius: 4px; margin: 2px; }}
        QScrollBar::handle:hover {{ background: {p['scroll_hover']}; }}
        QScrollBar::handle:vertical {{ min-height: 30px; }}
        QScrollBar::handle:horizontal {{ min-width: 30px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QAbstractScrollArea::corner {{ background: transparent; }}
        QProgressBar {{ background: {p['surface2']}; border: none; border-radius: 3px; height: 8px;
                        text-align: center; color: transparent; }}
        QProgressBar::chunk {{ background: {p['accent']}; border-radius: 3px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {p['border']}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {p['fg']}; width: 12px; height: 12px; margin: -4px 0;
                                       border-radius: 6px; }}
        QSlider::handle:horizontal:hover {{ background: {p['accent']}; }}
        """
    def is_dark(self) -> bool:
        return QColor(self.palette.get("bg", "#ffffff")).lightness() < 128


def system_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        from PySide6.QtCore import Qt
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False

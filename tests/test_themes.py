"""
Tests for the colour themes.

    python tests/test_themes.py

1. Every theme meets minimum contrast rules (readable text, visible lines,
   visible selection), checked with the WCAG contrast formula.
2. Every theme applies without a single Qt style-sheet warning.
3. Menus and the Preferences dialog list the themes in Dark / Light groups,
   and choosing one in Preferences saves the theme's key.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pyx-theme-")

from explorer.ui.themes import PALETTES, SPECS, contrast, ensure_contrast, grouped, mix  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


print("colour helpers")
check(round(contrast("#000000", "#ffffff"), 1) == 21.0, "black on white is 21:1")
check(mix("#000000", "#ffffff", 0.5) in ("#808080", "#7f7f7f"), "mix half-way")
nudged = ensure_contrast("#2a2a2a", "#262626", 1.3)
check(contrast(nudged, "#262626") >= 1.3 and nudged != "#2a2a2a", f"too-faint colour is nudged: {nudged}")
check(ensure_contrast("#ffffff", "#000000", 4.5) == "#ffffff", "a colour that is fine stays unchanged")

print(f"contrast rules for all {len(PALETTES)} themes")
RULES = [  # (what, colour key, against key, minimum ratio)
    ("text on content", "fg", "bg", 4.5), ("text on sidebar/tabs", "fg", "surface", 4.5),
    ("dim text on content", "fg_dim", "bg", 3.0), ("dim text on sidebar/tabs", "fg_dim", "surface", 3.0),
    ("selected text", "select_fg", "select", 4.5), ("selection visible", "select", "bg", 1.25),
    ("accent line visible", "accent", "bg", 1.8), ("text on accent", "accent_fg", "accent", 3.0),
    ("lines visible", "border", "surface", 1.12), ("folder icons visible", "folder", "bg", 1.6),
    ("terminal text", "terminal_fg", "terminal_bg", 4.5), ("error text", "error", "surface", 3.0),
    ("current tab stands out", "surface", "bg", 1.03),
]
failures = []
for key, p in PALETTES.items():
    for what, a, b, minimum in RULES:
        ratio = contrast(p[a], p[b])
        if ratio < minimum:
            failures.append(f"{key}: {what} {ratio:.2f} < {minimum}")
check(not failures, "every theme passes every rule" + ("" if not failures else ":\n  " + "\n  ".join(failures)))
needed = {"bg", "surface", "surface2", "border", "fg", "fg_dim", "accent", "accent_fg", "select", "select_fg",
          "hover", "alt_row", "entry", "folder", "page", "error", "ok", "terminal_bg", "terminal_fg",
          "chrome_hover", "select_inactive", "line_soft", "scroll", "scroll_hover", "ansi_red", "ansi_brightwhite"}
check(all(needed <= set(p) for p in PALETTES.values()), "every palette has every key the app reads")
groups = dict(grouped())
check(len(groups["Dark"]) >= 30 and len(groups["Light"]) >= 12,
      f"{len(groups['Dark'])} dark and {len(groups['Light'])} light themes")
check(sorted(groups["Dark"] + groups["Light"]) == sorted(SPECS), "every theme is in exactly one group")

# ---------------------------------------------------------------------------
print("applying every theme in the real interface")
from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

warnings = []


def on_qt_message(kind, context, text):
    if kind != QtMsgType.QtDebugMsg and "propagateSizeHints" not in text:
        warnings.append(text)


qInstallMessageHandler(on_qt_message)
from explorer.app import build_context  # noqa: E402

app = QApplication([])
ctx = build_context(app)
ctx.main.restore(None)
ctx.dispatcher.start()
ctx.main.resize(1000, 650)
ctx.main.show()


def spin(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


spin(0.3)
ctx.commands.open_search([os.path.expanduser("~")], name="zzz-no-match")   # utility tabs are drawn in every theme too
ctx.commands.open_rename()
ctx.commands.toggle_split()
spin(0.2)
start = time.perf_counter()
for key in ["system"] + list(PALETTES) + ["system"]:
    ctx.theme.apply(key)
    app.processEvents()
    ctx.main.grab()                                  # forces a full paint with the new style sheet
elapsed = (time.perf_counter() - start) / (len(PALETTES) + 2)
check(not warnings, "no style-sheet warnings from Qt" + ("" if not warnings else f": {warnings[:3]}"))
print(f"    switching theme takes about {elapsed * 1000:.0f} ms")
tab_bar = ctx.main.panel_area.panels[0].tab_bar
check(tab_bar.drawBase(), "back on 'system', the tab bar's own base line is restored")

ctx.theme.apply("dracula")
spin(0.1)
check(ctx.settings.get("appearance.theme") == "dracula", "the chosen theme is saved")
icon = ctx.icons.symbol("back", "go-previous")
check(not icon.pixmap(16, 16).isNull(), "the nav bar's line icons draw in a colour theme")

print("theme menu and Preferences")
from explorer.ui.menubar import _theme_items  # noqa: E402

items = _theme_items()
check(items[0] == "theme.system" and [i[0] for i in items if isinstance(i, tuple)] == ["Dark themes", "Light themes"],
      "View > Theme: System, then Dark themes / Light themes sub-menus")
check(ctx.actions.qaction("theme.one_dark_pro").text() == "One Dark Pro", "menu entries use the themes' names")
from explorer.ui.dialogs import PreferencesDialog  # noqa: E402

dialog = PreferencesDialog(ctx.main, ctx)
combo = dialog.widgets["appearance.theme"][1]           # (kind, widget)
check(combo.currentData() == "dracula" and combo.currentText() == "Dracula", "Preferences shows the current theme")
combo.setCurrentIndex(combo.findData("github_light"))
dialog._apply()                                   # what the OK button does
spin(0.1)
check(ctx.theme.name == "github_light", "choosing a theme in Preferences applies it")
print("fonts")
from explorer.ui.font_picker import installed_families  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402

families = installed_families()
mono = installed_families(monospace=True)
check(families and set(mono) <= set(families), f"{len(families)} installed fonts, {len(mono)} of them monospace")
check(all(QFontDatabase.isFixedPitch(f) for f in mono), "the terminal list only offers fixed-width fonts")
native = QFont(app.font()).family()
pick = next((f for f in families if f != native and QFontDatabase.WritingSystem.Latin in QFontDatabase.writingSystems(f)
             and not QFontDatabase.isFixedPitch(f)), families[0])
dialog = PreferencesDialog(ctx.main, ctx)
dialog.widgets["appearance.font_family"][1].setCurrentIndex(dialog.widgets["appearance.font_family"][1].findData(pick))
dialog.widgets["appearance.font_size"][1].setValue(13)
if mono:
    dialog.widgets["appearance.terminal_font"][1].setCurrentIndex(
        dialog.widgets["appearance.terminal_font"][1].findData(mono[-1]))
dialog._apply()
spin(0.2)
check(app.font().family() == pick and app.font().pointSize() == 13, f"the interface font is now {pick} 13pt")
header = ctx.commands.active_tab().view.horizontalHeader() if hasattr(ctx.commands.active_tab().view,
                                                                       "horizontalHeader") else None
if header is not None:
    check(header.font().family() == pick and header.font().bold(), "column headers follow it (and stay bold)")
sidebar = ctx.main.sidebar
check(sidebar.font().family() == pick, "the sidebar follows it")
if mono:
    check(ctx.theme.mono_font().family() == mono[-1], f"the terminal font is {mono[-1]}")
ctx.settings.set("appearance.font_family", "No Such Font 123")
ctx.theme.apply()
check(app.font().family() == native, "a font that is no longer installed falls back to the system font")
ctx.settings.set("appearance.font_family", "")
ctx.settings.set("appearance.font_size", 0)
ctx.settings.set("appearance.terminal_font", "")
ctx.theme.apply()
check(app.font().family() == native, "'System default' restores the system font")
check(not warnings, "still no Qt warnings")
ctx.runner.shutdown()
print("\nTHEME TESTS PASSED")

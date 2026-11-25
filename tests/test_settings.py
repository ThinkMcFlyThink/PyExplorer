"""
Tests for the Settings tab, the toolbar, the line icons and the formats.

    python tests/test_settings.py

1. The settings schema (core/settings_schema.py) covers every setting, and
   every entry is well-formed: real keys, real commands, real icons.
2. The Settings tab: opens with Ctrl+, (once), every page builds, the search
   box finds settings on any page, a change applies straight away, ↺ resets,
   and changes made elsewhere (menus) show in the tab.
3. The toolbar is built from data, can be customized, and never leaks widgets.
4. Every line icon draws; every action has one.
5. Dates and sizes follow Settings -> Views -> Formats.
6. Export / import / reset of the whole settings file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pyx-settings-")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


# ---------------------------------------------------------------------------
print("formats (plain Python)")
from explorer.core import formatting  # noqa: E402

formatting.configure("binary", "relative")
check(formatting.human_size(1536) == "1.5 KB" and formatting.human_size(512) == "512 B", "binary: 1536 B = 1.5 KB")
formatting.configure("decimal", "relative")
check(formatting.human_size(1500) == "1.5 kB" and formatting.human_size(2_000_000) == "2.0 MB",
      "decimal: 1500 B = 1.5 kB, 2,000,000 B = 2.0 MB")
formatting.configure("iec", "relative")
check(formatting.human_size(1536) == "1.5 KiB", "IEC: 1536 B = 1.5 KiB")
stamp = time.mktime((2024, 5, 1, 9, 12, 0, 0, 0, -1))
formatting.configure("binary", "%d/%m/%Y %H:%M")
check(formatting.human_time(stamp) == "01/05/2024 09:12", "a date pattern of your own: 01/05/2024 09:12")
formatting.configure("binary", "not a pattern")
check(formatting.human_time(stamp) == "2024-05-01 09:12", "an invalid pattern falls back to the default")
formatting.configure("binary", "relative")
check(formatting.human_time(time.time()).startswith("Today "), "relative dates: Today hh:mm")

# ---------------------------------------------------------------------------
print("settings schema")
from explorer.core import settings_schema as schema  # noqa: E402
from explorer.core.settings import DEFAULTS  # noqa: E402


def leaves(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict) and k not in ("zoom", "columns"):
            yield from leaves(v, prefix + k + ".")
        else:
            yield prefix + k


all_keys = set(leaves(DEFAULTS))
listed = {s.key for _p, _g, s in schema.all_settings() if s.key}
missing = sorted(k for k in all_keys - listed if k not in schema.INTERNAL and not k.startswith("shortcuts"))
check(not missing, "every setting is on a page (or deliberately internal)" + (f": missing {missing}" if missing else ""))
unknown = sorted(k for k in listed if k not in all_keys)
check(not unknown, "every key on a page is a real setting" + (f": unknown {unknown}" if unknown else ""))
keys = [s.key for _p, _g, s in schema.all_settings() if s.key]
check(len(keys) == len(set(keys)), "no setting is listed twice")
from explorer.commands import Commands  # noqa: E402

bad_apply = [s.apply for _p, _g, s in schema.all_settings() if s.apply and not hasattr(Commands, s.apply)]
check(not bad_apply, "every 'apply' names a real command" + (f": {bad_apply}" if bad_apply else ""))
check(all(s.kind in schema.KINDS for _p, _g, s in schema.all_settings()), "every kind is known")
check(all(s.options or s.source for _p, _g, s in schema.all_settings() if s.kind == "choice"),
      "every drop-down has choices")
check(all(s.minimum < s.maximum for _p, _g, s in schema.all_settings() if s.kind == "int"), "number ranges make sense")
check(all(s.button and s.apply for _p, _g, s in schema.all_settings() if s.kind == "action"),
      "every action has a button and a command")
for _p, _g, s in schema.all_settings():
    if s.kind == "choice" and s.options and s.key:
        default = DEFAULTS
        for part in s.key.split("."):
            default = default[part]
        assert default in [v for v, _l in s.options], f"{s.key}: default {default!r} is not one of its choices"
check(True, "every drop-down's default is one of its choices")
check(schema.matches("move trash", "Delete moves to the trash") and not schema.matches("zebra", "Rows"),
      "search matching: every word must appear")
linux_only = [s.key for _p, _g, s in schema.all_settings() if s.platforms == ("linux",)]
check(all(s.key not in linux_only for p in schema.pages("windows") for g in p.groups for s in g.settings),
      f"settings for one system only are left out elsewhere ({', '.join(linux_only)})")

# ---------------------------------------------------------------------------
print("the real interface")
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])
QMessageBox.exec = lambda self: 0
QMessageBox.clickedButton = lambda self: self.buttons()[0] if self.buttons() else None
from explorer.app import build_context  # noqa: E402
from explorer.ui import symbols  # noqa: E402


def spin(seconds=0.2, cond=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if cond and cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return cond() if cond else True


ctx = build_context(app)
main = ctx.main
main.restore(None)
ctx.dispatcher.start()
main.resize(1280, 800)
main.show()
spin(0.3)

print("line icons")
img = QImage(32, 32, QImage.Format.Format_ARGB32)
blank = []
for name in symbols.names():
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    symbols.draw(p, QRectF(0, 0, 32, 32), name, QColor("#000000"))
    p.end()
    if not any(img.pixelColor(x, y).alpha() for x in range(32) for y in range(32)):
        blank.append(name)
check(not blank, f"all {len(symbols.names())} line icons draw something" + (f" (blank: {blank})" if blank else ""))
no_icon = [a.id for a in ctx.actions.all() if a.qaction.icon().isNull() and not a.id.startswith(("theme.", "sort."))]
check(not no_icon, "every command has an icon" + (f" (missing: {no_icon})" if no_icon else ""))
check(all(symbols.exists(p.icon) for p in schema.PAGES), "every Settings page's icon exists")
check(not ctx.actions.qaction("window.split").isIconVisibleInMenu() and
      ctx.actions.qaction("edit.copy").isIconVisibleInMenu(), "checkable commands show a check mark in menus, not an icon")
check(ctx.actions.qaction("window.split").toolTip() == "Split view (F3)", "tooltips say the shortcut")

print("toolbar")
from explorer.ui import toolbar as toolbar_mod  # noqa: E402

bar = main.toolbar
ids = toolbar_mod.items(ctx.settings)
check(ids == toolbar_mod.DEFAULT, "the default layout is used when nothing is customized")
names = {w.objectName() for w in bar.findChildren(object) if hasattr(w, "objectName")}
check({"Segmented", "QuickSearch", "ToolbarMenu"} <= names, "segmented view switch, search box and menu buttons are built")
from explorer.ui.toolbar import QuickSearch  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402


def flush_deletes():
    """deleteLater() runs when control returns to the event loop - which a test doesn't run."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    spin(0.05)


flush_deletes()
made = len(bar.widgets)
alive = len(app.allWidgets())
for _ in range(5):
    bar.rebuild()
flush_deletes()
check(len(bar.widgets) == made and len(bar.findChildren(QuickSearch)) == 1 and
      sum(isinstance(w, QuickSearch) for w in app.allWidgets()) == 1 and len(app.allWidgets()) == alive,
      f"rebuilding the toolbar 5 times leaves no widgets behind ({len(app.allWidgets())} widgets, {alive} before)")
ctx.settings.set("toolbar.items", ["edit.copy", "|", "|", "no.such.action", "~", "@sort"])
spin(0.1)
check([a.text() for a in bar.actions() if not a.isSeparator() and a.text()][:1] == ["Copy"] and
      sum(a.isSeparator() for a in bar.actions()) == 1, "custom layout: unknown ids skipped, double separators merged")
ctx.settings.set("toolbar.style", "text_beside")
spin(0.1)
check(bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon, "style: icons and labels")
ctx.settings.set("toolbar.icon_size", 22)
spin(0.1)
check(bar.iconSize().width() == 22, "icon size follows the setting")
menu = bar.context_menu()
check([a.text() for a in menu.actions()][:3] == ["Labels", "Icon size", "Customize toolbar…"],
      "right-click: labels, icon size, customize")
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

shown = []
Window = type(main)
original_popup_menu = Window.createPopupMenu


def spy(self):                                          # what Qt calls on a right-click
    menu = original_popup_menu(self)
    shown.append([act.text() for act in menu.actions()])
    return menu


Window.createPopupMenu = spy
button = bar.widgetForAction(bar.actions()[0])          # right-click the first button
QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(3, 3),
                                                 button.mapToGlobal(QPoint(3, 3))))
Window.createPopupMenu = original_popup_menu
for popup in [w for w in app.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible()]:
    popup.close()
check(shown and shown[-1][:3] == ["Labels", "Icon size", "Customize toolbar…"],
      "right-clicking the real toolbar opens that menu")
for key in ("toolbar.items", "toolbar.style", "toolbar.icon_size"):
    ctx.settings.reset_key(key)
spin(0.1)
check(bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and bar.iconSize().width() == 18,
      "↺ back to the default toolbar")

print("Settings tab")
ctx.actions.trigger("tools.preferences")
spin(0.3)
st = ctx.commands.active_tab()
check(getattr(st, "KIND", "") == "settings" and st.TITLE == "Settings", "Ctrl+, opens the Settings tab")
ctx.commands.active_tab().panel.activate(0)
ctx.actions.trigger("tools.preferences")
check(ctx.commands.active_tab() is st and sum(getattr(t, "KIND", "") == "settings" for t in ctx.commands.all_tabs()) == 1,
      "...and goes back to the same one")
check(list(st.pages) == ["appearance"], "only the page you look at is built (fast to open, fast theme changes)")
st.build_all()
check(st.page_ids() == [p.id for p in schema.pages()] and set(st.pages) == set(st.page_ids()),
      f"every page builds: {', '.join(st.page_ids())}")
check([st.column.itemAt(i).widget() for i in range(len(st.pages))] == [st.pages[p] for p in st.page_ids()],
      "...in the order of the page list")
on_pages = {s.key for p in schema.pages() for g in p.groups for s in g.settings if s.key}
check(set(st.rows) == on_pages, f"every setting has a row ({len(st.rows)})")
st.show_page("views")
spin(0.1)
check(st.pages["views"].isVisible() and not st.pages["appearance"].isVisible(), "a page shows on its own")

st.search.setText("trash")
spin(0.1)
check(st.rows["behaviour.use_trash"].isVisible() and st.rows["behaviour.confirm_delete"].isVisible()
      and not st.rows["view.mode"].isVisibleTo(st), "the search box finds settings on any page")
st.search.setText("ctrl+shift+h")
spin(0.1)
check(st.pages["keyboard"].isVisibleTo(st), "...and commands on the Keyboard page by their keys")
st.search.setText("zzqq nothing")
spin(0.1)
check(st.empty.isVisibleTo(st), "no match: says so")
st.search.clear()
spin(0.1)
check(st.pages["views"].isVisibleTo(st) and not st.empty.isVisibleTo(st), "clearing the search shows the page again")

data = tempfile.mkdtemp()
open(os.path.join(data, ".secret"), "w").close()
open(os.path.join(data, "seen.txt"), "w").close()
folder_tab = ctx.commands.area.active_panel.tabs[0]
folder_tab.navigate(data)
spin(2, lambda: not folder_tab._loading)
check(folder_tab.qmodel.rowCount() == 1, "hidden file not shown")
row = st.rows["view.show_hidden"]
row.commit(True)
spin(0.2)
check(folder_tab.qmodel.rowCount() == 2 and ctx.actions.qaction("view.hidden").isChecked(),
      "a switch applies straight away (hidden files show, the menu's check mark follows)")
check(row.reset_btn.isVisibleTo(st), "a changed setting offers ↺")
row.reset()
spin(0.2)
check(folder_tab.qmodel.rowCount() == 1 and not row.reset_btn.isVisibleTo(st) and not row.editor.get(),
      "↺ puts it back (and the change applies)")
ctx.actions.trigger("view.hidden")                     # changed elsewhere: the tab follows
spin(0.1)
check(row.editor.get() is True, "changes made from the menu show in the Settings tab")
ctx.actions.trigger("view.hidden")
st.rows["view.mode"].commit("icons")
spin(0.2)
check(folder_tab.view.mode == "icons" and ctx.settings.get("view.mode") == "icons", "the view mode applies to the tabs")
st.rows["view.mode"].commit("details")
spin(0.1)
st.rows["view.date_format"].commit("%Y/%m/%d")
spin(0.1)
modified = folder_tab.qmodel.columns.index("modified")
check("/" in folder_tab.qmodel.data(folder_tab.qmodel.index(0, modified)), "the date format applies to file lists")
st.rows["view.date_format"].reset()
st.rows["view.size_units"].commit("iec")
check(formatting.human_size(2048) == "2.0 KiB", "the size units apply")
st.rows["view.size_units"].reset()
st.rows["view.alternate_rows"].commit(False)
spin(0.1)
check(not folder_tab.view.alternatingRowColors(), "striped rows can be turned off")
st.rows["view.alternate_rows"].reset()
st.rows["behaviour.new_tab_position"].commit("end")
panel = folder_tab.panel
panel.activate(0)
panel.open_tab(None)
check(panel.tab_widget.indexOf(panel.active_tab) == panel.tab_widget.count() - 1, "new tabs can open at the end")
panel.close_tab(panel.active_tab, remember=False)
st.rows["behaviour.new_tab_position"].reset()
st.rows["behaviour.watch_interval_ms"].commit(3000)
check(ctx.watcher.interval_ms == 3000, "the folder-watch interval applies without a restart")
st.rows["behaviour.watch_interval_ms"].reset()
st.rows["appearance.show_statusbar"].commit(False)
spin(0.1)
check(not main.statusBar().isVisible() and not ctx.actions.qaction("window.statusbar").isChecked(),
      "hiding the status bar applies")
st.rows["appearance.show_statusbar"].reset()
spin(0.1)
check(main.statusBar().isVisible(), "...and ↺ shows it again")
st.rows["toolbar.style"].commit("icons")
spin(0.1)
check(main.toolbar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and
      main.toolbar.property("labels") == "icons", "toolbar style from Settings")
st.rows["toolbar.style"].reset()

print("Keyboard page")
kp = st.pages["keyboard"].custom
check(len(ctx.actions.qaction("go.up").shortcuts()) == 2, "Up has two shortcuts (Alt+Up, Backspace)")
kp.assign("go.home", "Backspace")
up_keys = [s.toString() for s in ctx.actions.qaction("go.up").shortcuts()]
check(ctx.actions.qaction("go.home").shortcut().toString() == "Backspace" and up_keys == ["Alt+Up"],
      "taking one of a command's two shortcuts leaves it the other")
check("moved" in kp.note.text() and "Up" in kp.note.text(), f"...and says so: “{kp.note.text()}”")
kp.reset_all()
check(len(ctx.actions.qaction("go.up").shortcuts()) == 2, "Reset all shortcuts brings both back")

print("Toolbar page")
tp = st.pages["toolbar"].custom
before = tp.ids()
tp.current.setCurrentRow(0)
tp.remove()
check(toolbar_mod.items(ctx.settings) == before[1:], "Remove takes an item off the real toolbar")
tp.available.setCurrentRow(next(i for i in range(tp.available.count())
                                 if tp.available.item(i).data(Qt.ItemDataRole.UserRole) == "go.home"))
tp.current.setCurrentRow(0)
tp.add()
check(toolbar_mod.items(ctx.settings)[1] == "go.home", "Add puts it after the selected item")
tp.move(-1)
check(toolbar_mod.items(ctx.settings)[0] == "go.home", "Move up")
tp.reset()
check(ctx.settings.get("toolbar.items") == [] and tp.ids() == toolbar_mod.DEFAULT, "Reset: the default toolbar")

print("export / import / reset")
from explorer.ui import dialogs  # noqa: E402

out = os.path.join(tempfile.mkdtemp(), "exported.json")
ctx.settings.set("view.sort_key", "size")
ctx.settings.export_to(out)
check(json.load(open(out))["view"]["sort_key"] == "size" and "window" not in json.load(open(out)),
      "export writes the settings (without the window layout)")
ctx.settings.set("view.sort_key", "name")
ctx.settings.import_from(out)
ctx.commands.apply_all()
spin(0.2)
check(ctx.settings.get("view.sort_key") == "size" and st.rows["view.sort_key"].editor.get() == "size",
      "import brings them back (and the Settings tab shows it)")
bad = os.path.join(tempfile.mkdtemp(), "bad.json")
open(bad, "w").write("[1, 2, 3]")
try:
    ctx.settings.import_from(bad)
    raised = False
except ValueError:
    raised = True
check(raised and ctx.settings.get("view.sort_key") == "size", "a file that isn't settings is refused, nothing changes")
ctx.settings.set("window.geometry", "abc")
dialogs.confirm = lambda *a, **k: True
ctx.commands.reset_settings()
spin(0.2)
check(ctx.settings.get("view.sort_key") == "name" and ctx.settings.get("window.geometry") == "abc",
      "Reset all: defaults again, the window layout kept")

print("session")
state = st.get_state()
check(state == {"utility": "settings", "page": st._current}, "the tab saves which page was open")
ctx.runner.shutdown()
print("\nSETTINGS TESTS PASSED")
sys.stdout.flush()
os._exit(0)

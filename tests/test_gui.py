"""
End-to-end test of the real Qt interface, run without a screen
(QT_QPA_PLATFORM=offscreen). Drives navigation, all view modes, zoom,
filter, sort, hidden files, tree expansion, search + index, tabs, split view,
copying between panels, rename, delete, clipboard, keyboard shortcuts,
terminal cd-sync, auto-refresh, session save/restore and a 100k-file folder.

Run:  python tests/test_gui.py           (add --shots to save screenshots)
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()        # trash goes here, not your real trash
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog  # noqa: E402

SHOTS = "--shots" in sys.argv
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")

app = QApplication([])
from explorer.app import build_context  # noqa: E402

# Auto-answer dialogs (there is nobody to click them).
QMessageBox.exec = lambda self: (self.setProperty("clicked", True), 0)[1]
QMessageBox.clickedButton = lambda self: self.buttons()[0] if self.buttons() else None
QInputDialog.exec = lambda self: 1


def spin(cond=lambda: False, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return cond()


def shot(name):
    if SHOTS:
        os.makedirs(SHOT_DIR, exist_ok=True)
        spin(timeout=0.3)
        ctx.main.grab().save(os.path.join(SHOT_DIR, name + ".png"))


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


def loaded(tab, count=None):
    return lambda: not tab._loading and (count is None or tab.listing.total == count)


# ---------------------------------------------------------------- test data
data = tempfile.mkdtemp(prefix="pyx-")
for i in range(5):
    os.mkdir(os.path.join(data, f"folder{i}"))
for i in range(2000):
    with open(os.path.join(data, f"file{i}.{'txt' if i % 3 else 'png'}"), "w") as fh:
        fh.write("x" * i)
open(os.path.join(data, ".hidden"), "w").close()
f1 = os.path.join(data, "folder1")
for j in range(50):
    open(os.path.join(f1, f"inner{j}.py"), "w").close()
os.mkdir(os.path.join(f1, "deeper"))
open(os.path.join(f1, "deeper", "leaf.txt"), "w").close()

ctx = build_context(app)
main = ctx.main
main.restore(None)
ctx.dispatcher.start()
ctx.watcher.start()
main.resize(1300, 820)
main.show()
spin(timeout=0.3)

print("navigation & loading")
tab = ctx.commands.active_tab()
tab.navigate(data)
check(spin(loaded(tab, 2006)), "folder with 2006 entries loads")
check(tab.qmodel.rowCount() == 2005, "hidden file not shown (2005 rows)")
check([e.name for e in tab.qmodel.top_entries()[:6]] == ["folder0", "folder1", "folder2", "folder3", "folder4", "file0.png"],
      "folders first, natural order")
shot("01_details")

print("view modes + zoom")
for mode in ["icons", "list", "tree", "details"]:
    ctx.commands.set_view_mode(mode)
    spin(timeout=0.15)
    check(tab.view.mode == mode and tab.view.model().rowCount() == 2005, f"{mode} view shows all rows")
    before = tab.view.iconSize().width()
    ctx.commands.zoom(1)
    spin(timeout=0.05)
    check(tab.view.iconSize().width() > before, f"{mode} zoom in grows icons")
    ctx.commands.zoom(-1)
    if mode == "icons":
        shot("02_icons")

print("keyboard selection")
tab.view.setFocus()
tab.view.setCurrentIndex(tab.qmodel.index(0, 0))
QTest.keyClick(tab.view, Qt.Key.Key_Down)
check([e.name for e in tab.selected_entries()] == ["folder1"], "Down selects next item")
QTest.keyClick(tab.view, Qt.Key.Key_End, Qt.KeyboardModifier.ShiftModifier)
check(len(tab.selected_entries()) == 2004, "Shift+End extends selection")
QTest.keyClick(tab.view, Qt.Key.Key_Home)

print("instant filter (type to filter)")
QTest.keyClicks(tab.view, "fold")
spin(timeout=0.2)
check(tab.filter_bar.isVisible() and tab.qmodel.rowCount() == 5, "typing 'fold' filters to 5 folders")
tab.hide_filter()
spin(timeout=0.1)
check(tab.qmodel.rowCount() == 2005, "closing the filter restores the list")

print("hidden files + sorting")
ctx.commands.toggle_hidden()
spin(timeout=0.1)
check(tab.qmodel.rowCount() == 2006, "Ctrl+Shift+H shows hidden file")
ctx.commands.toggle_hidden()
ctx.commands.set_sort("size")
spin(timeout=0.1)
check(tab.qmodel.top_entries()[5].name == "file1999.txt", "sort by size, biggest first")
ctx.commands.set_sort("name", toggle=False)
ctx.commands.toggle_sort_reverse()
ctx.commands.toggle_sort_reverse()
spin(timeout=0.1)

print("selection survives refresh")
tab.view.select_paths([os.path.join(data, "file7.txt"), os.path.join(data, "file9.png")])
open(os.path.join(data, "zz_new.txt"), "w").close()
ctx.bus.publish("fs.changed", paths=[data])
check(spin(loaded(tab, 2007)), "refresh picks up a new file")
check(sorted(e.name for e in tab.selected_entries()) == ["file7.txt", "file9.png"], "selection kept after refresh")

print("tree view")
ctx.commands.set_view_mode("tree")
spin(timeout=0.1)
idx = tab.qmodel.index_for_path(f1)
tab.view.expand(idx)
check(spin(lambda: tab.qmodel.rowCount(tab.qmodel.index_for_path(f1)) == 51), "expanding folder1 loads 51 children")
deeper = os.path.join(f1, "deeper")
tab.view.expand(tab.qmodel.index_for_path(deeper))
check(spin(lambda: tab.qmodel.index_for_path(os.path.join(deeper, "leaf.txt")).isValid()), "nested expand works")
shot("03_tree")
ctx.bus.publish("fs.changed", paths=[data])
spin(loaded(tab))
spin(timeout=0.3)
check(tab.view.isExpanded(tab.qmodel.index_for_path(f1)), "expanded folders survive a refresh")
state = tab.get_state()
check(sorted(state["view_state"]["expanded"]) == sorted([f1, deeper]), "expanded folders are saved in the session")
ctx.commands.set_view_mode("details")
spin(timeout=0.1)

print("navigation history")
tab.navigate(f1)
check(spin(loaded(tab, 51)), "open folder1")
tab.go_up()
check(spin(loaded(tab, 2007)), "go up")
check([e.path for e in tab.selected_entries()] == [f1], "going up re-selects the folder we came from")
tab.go_back()
check(spin(lambda: tab.path == f1 and not tab._loading), "back")
tab.go_forward()
check(spin(lambda: tab.path == data and not tab._loading), "forward")

print("Search tab (Ctrl+F)")
from explorer.ui.context_menu import build_item_menu  # noqa: E402
panel = main.panel_area.active_panel
ctx.actions.qaction("edit.search").trigger()
st = ctx.commands.active_tab()
check(getattr(st, "KIND", "") == "search" and st.spec.locations == [data] and st.is_utility,
      "Ctrl+F opens a Search tab for the current folder")
check(panel.tab_widget.indexOf(st) == panel.tab_widget.indexOf(tab) + 1, "...right after the folder tab")


def searched():
    return not st._running and st._stats is not None


st.name_edit.setText("inner2*")
check(spin(searched, 15), "it searches as you type")
check(st.qmodel.rowCount() == 11 and "location" in st.qmodel.columns, "11 results, with a Location column")
first_build = st._stats.built_now
st.name_edit.setText("ext:py")
check(spin(lambda: searched() and st.qmodel.rowCount() == 50, 15), "50 .py files")
check(first_build and not st._stats.built_now and st._stats.seconds < 0.5, "the second search answers from the index")
check(st.title == "Search: ext:py" and panel.tab_bar.tabText(panel.tab_widget.indexOf(st)) == st.title,
      "the tab is titled after the search")
one = st.listing.visible[0]
st.view.select_paths([one.path])
spin(timeout=0.1)
menu = build_item_menu(ctx, st, [one])
labels = [a.text() for a in menu.actions()]
check(any("containing folder" in t for t in labels) and any(t.startswith("Copy") for t in labels),
      "results have the normal right-click menu (+ Open containing folder)")
before = len(panel.tabs)
st.navigate(one.parent, select=[one.path])
check(len(panel.tabs) == before + 1 and panel.active_tab.path == one.parent, "Open containing folder: a folder tab next to it")
panel.close_tab(panel.active_tab, remember=False)
panel.activate(panel.tab_widget.indexOf(st))
st.filters_btn.setChecked(True)
st.size_combo.setCurrentIndex(st.size_combo.findData("min"))
st.size_a.setValue(1)
st.size_a_unit.setCurrentIndex(0)                       # at least 1 byte
check(spin(lambda: searched() and st.qmodel.rowCount() == 0, 10), "filters apply as they change (no .py file has content)")
st._reset_filters()
check(spin(lambda: searched() and st.qmodel.rowCount() == 50, 10), "Reset filters")
st.view.select_paths([e.path for e in st.listing.visible[:3]])
spin(timeout=0.1)
ctx.commands.rename()                                    # F2 with several selected
rt = ctx.commands.active_tab()
check(getattr(rt, "KIND", "") == "rename" and len(rt.files) == 3, "F2 on several results opens the Rename tab for them")
check(spin(lambda: len(rt.rows) == 3, 10), "...listing exactly those 3")
check(rt.model.COLUMNS == ["", "Current name", "New name", "Folder"], "the list has no Status column")
spin(timeout=0.3)
need = rt.cards.needed_height()
check(rt.rules_scroll.height() <= need and rt.split.sizes()[0] >= rt.table.verticalHeader().defaultSectionSize() * 6,
      f"short window: the rules take what's left above ~6 list rows ({rt.rules_scroll.height()} of {need} px, scrolling)")
size_before = main.size()
main.resize(main.width(), main.height() + need)
spin(timeout=0.4)
check(rt.rules_scroll.height() == rt.cards.needed_height() and not rt.rules_scroll.verticalScrollBar().isVisible(),
      f"tall enough: the rules panel is exactly as tall as its contents ({rt.rules_scroll.height()} px), no scroll bar")
main.resize(size_before)
spin(timeout=0.2)
shot("04_search")
panel.close_tab(rt)
panel.close_tab(st)
check(all(not getattr(t, "is_utility", False) for t in panel.tabs), "utility tabs close like any tab")
check(ctx.closed_tabs[-1].get("utility") == "search", "...and can be reopened (Ctrl+Shift+T)")
ctx.commands.reopen_closed_tab()
spin(timeout=0.3)
again = ctx.commands.active_tab()
check(getattr(again, "KIND", "") == "search" and again.name_edit.text() == "ext:py", "reopened with its search")
check(spin(lambda: not again._running and again.qmodel.rowCount() == 50, 10), "...and runs it again")
panel.close_tab(again, remember=False)
panel.activate(panel.tab_widget.indexOf(tab))
tab.navigate("computer://")
spin(loaded(tab))
ctx.commands.open_search(name="zzz")
whole = ctx.commands.active_tab()
check(whole.whole_cb.isChecked() and "whole computer" in whole.header.subtitle_label.text(),
      "Ctrl+F in the Computer view searches the whole computer")
panel.close_tab(whole, remember=False)                  # (closing cancels the first-time indexing)
panel.activate(panel.tab_widget.indexOf(tab))
tab.navigate(data)
spin(loaded(tab, 2007))

print("tabs + split view")
ctx.commands.new_tab(f1)
spin(timeout=0.2)
left = main.panel_area.panels[0]
check(len(left.tabs) == 2, "new tab opens")
QTest.keyClick(main, Qt.Key.Key_F3)
spin(timeout=0.3)
area = main.panel_area
check(area.is_split and [len(p.tabs) for p in area.panels] == [1, 1], "F3 splits; current tab moves right")
right = area.panels[1]
check(right.active_tab.path == f1 and area.active_panel is right, "right panel shows folder1 and is active")
right.open_tab(None)
spin(timeout=0.2)
check(len(right.tabs) == 2, "a new tab opens on the side it was opened from")
shot("05_split")

print("copy to other panel")
left_tab = left.active_tab
left_tab.view.setFocus()
spin(lambda: area.active_panel is left)
left_tab.view.select_paths([os.path.join(data, "file5.txt")])
right.activate(0)
left_tab.view.setFocus()
spin(lambda: area.active_panel is left)
QTest.keyClick(main, Qt.Key.Key_F5, Qt.KeyboardModifier.ShiftModifier)
check(spin(lambda: os.path.exists(os.path.join(f1, "file5.txt"))), "Shift+F5 copies to the other panel")
check(spin(lambda: right.active_tab.listing.total == 52), "the other panel refreshes")

print("move tab between panels, unsplit")
ctx.commands.move_tab_to_other_panel(right.tabs[1])
spin(timeout=0.2)
check([len(p.tabs) for p in area.panels] == [2, 1], "move tab to other panel")
ctx.commands.toggle_split()
spin(timeout=0.2)
check(not area.is_split and len(area.panels[0].tabs) == 3, "F3 again merges tabs back")

print("rename, new folder, delete")
tab = ctx.commands.active_tab()
tab.navigate(f1)
spin(loaded(tab))
ctx.commands.rename_to(tab, os.path.join(f1, "file5.txt"), "renamed.txt")
check(spin(lambda: any(e.name == "renamed.txt" for e in tab.listing.all_entries)), "rename shows up")
check(spin(lambda: [e.name for e in tab.selected_entries()] == ["renamed.txt"]), "renamed file is selected")
ctx.fileops.make_folder(f1, "New folder")
check(spin(lambda: any(e.name == "New folder" for e in tab.listing.all_entries)), "new folder appears")
ctx.commands.delete([os.path.join(f1, "New folder")])
check(spin(lambda: not os.path.exists(os.path.join(f1, "New folder"))), "delete (to trash) works")

print("clipboard (shared with other apps)")
ctx.commands.copy([os.path.join(f1, "renamed.txt")])
from explorer.ui import system_clipboard  # noqa: E402
check(system_clipboard.get_files() == ([os.path.join(f1, "renamed.txt")], False), "copy puts file URLs on the system clipboard")
ctx.commands.paste(os.path.join(f1, "deeper"))
check(spin(lambda: os.path.exists(os.path.join(f1, "deeper", "renamed.txt"))), "paste copies the file")
from explorer.ui import dialogs  # noqa: E402
dialogs.ask_conflict = lambda parent, src, dst: ("rename", False)     # answer "Keep both"
ctx.commands.cut([os.path.join(f1, "deeper", "renamed.txt")])
check(system_clipboard.get_files()[1] is True, "cut is marked as cut for other file managers")
ctx.commands.paste(f1)
check(spin(lambda: os.path.exists(os.path.join(f1, "renamed (2).txt"))), "pasting a cut moves (keeping both names)")
check(not os.path.exists(os.path.join(f1, "deeper", "renamed.txt")), "cut source is gone")

print("drag & drop + inline rename")
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from explorer.ui.models.file_model import paths_to_mime  # noqa: E402
src_file = os.path.join(f1, "inner1.py")
deeper_idx = tab.qmodel.index_for_path(os.path.join(f1, "deeper"))
pos = tab.view.visualRect(deeper_idx).center()
mime1 = paths_to_mime([src_file])        # keep a reference: the event does not own it
drop = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime1,
                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier)
tab.view.dropEvent(drop)
check(spin(lambda: os.path.exists(os.path.join(f1, "deeper", "inner1.py"))), "Ctrl+drop onto a folder copies into it")
mime2 = paths_to_mime([os.path.join(f1, "inner2.py")])
drop = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime2,
                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
tab.view.dropEvent(drop)
check(spin(lambda: os.path.exists(os.path.join(f1, "deeper", "inner2.py")) and not os.path.exists(os.path.join(f1, "inner2.py"))),
      "plain drop on the same disk moves")
spin(loaded(tab))
tab.view.select_paths([os.path.join(f1, "inner3.py")])
ctx.commands.rename()
spin(timeout=0.2)
from PySide6.QtWidgets import QLineEdit  # noqa: E402
editor = tab.view.findChild(QLineEdit)
check(editor is not None and editor.selectedText() == "inner3", "F2 opens the editor with the name (minus extension) selected")
editor.setText("edited.py")
QTest.keyClick(editor, Qt.Key.Key_Return)
check(spin(lambda: os.path.exists(os.path.join(f1, "edited.py"))), "pressing Enter renames the file")
from explorer.ui.dialogs import ShortcutsDialog  # noqa: E402
sd = ShortcutsDialog(main, ctx)
from PySide6.QtGui import QKeySequence  # noqa: E402
sd.editors["tab.new"].setKeySequence(QKeySequence("Ctrl+N"))
sd._apply()
check(ctx.actions.qaction("tab.new").shortcut().toString() == "Ctrl+N" and ctx.settings.get("shortcuts") == {"tab.new": "Ctrl+N"},
      "shortcut editor changes and saves a shortcut")
sd._defaults()
sd._apply()
check(ctx.settings.get("shortcuts") == {}, "restore defaults")

print("folder colour")
ctx.commands.set_folder_color([f1], "#ff0000")
check(ctx.folder_colors.get(f1) == "#ff0000", "folder colour saved")

print("terminal")
main.activateWindow()          # (Qt's offscreen platform activates every window shown, e.g. the transfer window)
spin(timeout=0.2)
QTest.keyClick(main, Qt.Key.Key_F4)
spin(timeout=1.0)
check(main.terminal_visible and main.terminal.backend is not None, "F4 opens the terminal")
tab.navigate(data)
spin(loaded(tab))
spin(timeout=0.6)
check(main.terminal.cwd == data, "terminal follows navigation")
term = main.terminal.term
QTest.keyClicks(term, "echo hello-$((40+2))")
QTest.keyClick(term, Qt.Key.Key_Return)
check(spin(lambda: "hello-42" in "\n".join(term.screen.display) + "".join(
    "".join(line[c].data for c in range(term.cols)) for line in term.screen.history.top), 3), "typed command runs")
QTest.keyClicks(term, "pwd")
QTest.keyClick(term, Qt.Key.Key_Return)
check(spin(lambda: any(data in line for line in term.screen.display), 3), "shell is in the current folder")
shot("06_terminal")
QTest.keyClick(term, Qt.Key.Key_F4)
spin(timeout=0.2)
check(not main.terminal_visible, "F4 inside the terminal closes it")

print("context menus + misc commands")
from explorer.ui.context_menu import build_background_menu, build_item_menu  # noqa: E402
m1 = build_item_menu(ctx, tab, tab.qmodel.top_entries()[:2])
m2 = build_background_menu(ctx, tab)
check(len(m1.actions()) > 10 and len(m2.actions()) > 8, "context menus build")
for aid in ["view.zoom_in", "view.zoom_out", "go.home", "edit.select_all", "tab.new", "tab.close", "tab.reopen",
            "window.sidebar", "window.sidebar", "window.toolbar", "window.toolbar"]:
    ctx.actions.trigger(aid)
    spin(timeout=0.05)
check(main.sidebar_visible, "toggling actions leaves sidebar visible")
tab = ctx.commands.active_tab()
tab.navigate("computer://")
check(spin(loaded(tab)) and tab.listing.total >= 1, "Computer view lists drives")
tab.navigate("/definitely/not/here")
spin(loaded(tab))
check("Cannot open" in tab.view._message, "a missing folder shows an error message")
tab.navigate(data)
spin(loaded(tab))

print("auto-refresh (watcher)")
open(os.path.join(data, "external_new.txt"), "w").close()
check(spin(lambda: any(e.name == "external_new.txt" for e in tab.listing.all_entries), 6), "external change detected")

print("long names in List / Icons view")
# Regression: those views measured ONE item (whichever streamed in first) and used its size
# for all. A long first name made every item wider than its column: items overlapped.
longs = tempfile.mkdtemp(prefix="pyx-long-")
for i in range(30):
    open(os.path.join(longs, f"A rather long downloaded file name number {i} from some website (final).pdf"), "w").close()
open(os.path.join(longs, "a.txt"), "w").close()
tab.navigate(longs)
check(spin(loaded(tab, 31)), "folder with long names loaded")
for mode in ("list", "icons"):
    ctx.commands.set_view_mode(mode)
    spin(timeout=0.3)
    view = tab.view
    cell = view.gridSize()
    rects = [view.visualRect(view.model().index(r, 0)) for r in range(view.model().rowCount())]
    check(all(r.width() <= cell.width() and r.height() <= cell.height() for r in rects) and
          len({(r.width(), r.height()) for r in rects}) == 1,
          f"{mode} view: every item fits its {cell.width()}x{cell.height()} cell "
          f"(items {rects[0].width()}x{rects[0].height()})")

print("big folder (100,000 files)")
big = tempfile.mkdtemp(prefix="pyx-big-")
for i in range(100_000):
    open(os.path.join(big, f"item_{i}.dat"), "w").close()
ctx.commands.set_view_mode("details")
t0 = time.time()
tab.navigate(big)
first = spin(lambda: tab.qmodel.rowCount() > 0, 10)
t_first = time.time() - t0
check(spin(loaded(tab, 100_000), 60), f"100k folder loads (first rows after {t_first:.2f}s, all after {time.time() - t0:.1f}s)")
gaps = []
last = time.perf_counter()
for mode in ["icons", "list", "tree", "details"]:
    t = time.perf_counter()
    ctx.commands.set_view_mode(mode)
    app.processEvents()
    gaps.append((mode, time.perf_counter() - t))
print("    view switch times:", ", ".join(f"{m} {s * 1000:.0f} ms" for m, s in gaps))
t = time.perf_counter()
tab.show_filter("item_9999")
spin(lambda: tab.qmodel.rowCount() == 11, 5)
print(f"    filter 100k -> {tab.qmodel.rowCount()} rows in {(time.perf_counter() - t) * 1000:.0f} ms")
tab.hide_filter()
tab.navigate(data)
spin(loaded(tab))

print("history tab (Ctrl+H)")
keys = lambda aid: [s.toString() for s in ctx.actions.qaction(aid).shortcuts()]
check("Ctrl+H" in keys("tools.history") and "Ctrl+H" not in keys("view.hidden"), "Ctrl+H opens History (hidden files: Ctrl+Shift+H)")
check(len(ctx.history) > 0 and {"rename", "copy", "move", "create"} <= {r.kind for r in ctx.history.records()},
      f"earlier renames, copies, moves and new folders were recorded ({len(ctx.history)} actions)")
src10 = os.path.join(data, "file10.txt")
ctx.commands.rename_to(tab, src10, "hist_renamed.txt")
check(spin(lambda: ctx.history.records()[0].kind == "rename" and
           ctx.history.records()[0].items[0]["src"] == src10), "a rename appears at the top of the history")
ctx.actions.trigger("tools.history")
ht = ctx.commands.active_tab()
check(getattr(ht, "KIND", "") == "history" and ht.model.rowCount() == len(ctx.history), "Ctrl+H opens the History tab")
check(ht.model.data(ht.model.index(0, 0)) == "Renamed" and "file10.txt" in ht.model.data(ht.model.index(0, 1)),
      "newest action first, showing old → new name")
ctx.commands.active_tab().panel.activate(0)
ctx.actions.trigger("tools.history")
check(ctx.commands.active_tab() is ht and sum(getattr(t, "KIND", "") == "history" for t in ctx.commands.all_tabs()) == 1,
      "Ctrl+H again goes to the same History tab")
ht.tree.setCurrentIndex(ht.model.index(0, 0))
check(ht.undo_btn.isEnabled() and ht.undo_btn.text() == "Undo 1 item", "selecting it enables Undo")
ht.undo_selected()
check(spin(lambda: os.path.exists(src10) and not os.path.exists(os.path.join(data, "hist_renamed.txt"))),
      "Undo renames the file back")
check(spin(lambda: ht.model.data(ht.model.index(0, 4)) == "Undone" and not ht.undo_btn.isEnabled()),
      "the action shows as Undone")
trash_rows = [r for r in range(ht.model.rowCount()) if ht.model.rows[r].kind in ("trash", "delete")]
check(trash_rows and ht.model.data(ht.model.index(trash_rows[0], 4)).startswith("Can't be undone"),
      "deletes are listed but can't be undone")
ht.find_edit.setText("hist_renamed")
check(ht.model.rowCount() == 1, "Find narrows the list")
ht.find_edit.setText("")
shot("history")

print("drive usage bars")
from explorer.ui.sidebar import USAGE_ROLE  # noqa: E402
check(spin(lambda: ctx.drive_usage.get("/") is not None, 10), "the system disk gets measured in the background")
sb = main.sidebar
rows = [sb.topLevelItem(i).child(j) for i in range(sb.topLevelItemCount()) for j in range(sb.topLevelItem(i).childCount())]
check(any(r.data(0, USAGE_ROLE) == "/" for r in rows), "the Root (/) sidebar row has a usage bar")
check(not any(r.data(0, USAGE_ROLE) and r.text(0) == "Home" for r in rows), "plain folders (Home) don't")
tab.panel.activate(tab.panel.tab_widget.indexOf(tab))
tab.navigate("computer://")
spin(loaded(tab))
ctx.commands.set_view_mode("details")
spin(timeout=0.2)
root_row = next(r for r in range(tab.qmodel.rowCount()) if tab.qmodel.top_entries()[r].path == "/")
size_col = tab.qmodel.columns.index("size")
check((tab.qmodel.data(tab.qmodel.index(root_row, size_col)) or "").endswith(" free") and
      "free of" in tab.qmodel.data(tab.qmodel.index(root_row, 0), Qt.ItemDataRole.ToolTipRole),
      "Computer view: the Size column says how much is free")
shot("drive_usage_details")
ctx.commands.set_view_mode("icons")
spin(timeout=0.3)
shot("drive_usage_icons")
ctx.actions.trigger("view.drive_usage")
check(ctx.drive_usage.get("/") is None and not ctx.settings.get("appearance.drive_usage"), "View → Drive usage bars turns them off")
ctx.actions.trigger("view.drive_usage")
ctx.commands.set_view_mode("details")
tab.navigate(data)
spin(loaded(tab))

print("transfers (copy window)")
from explorer.core import transfer_model as tm  # noqa: E402
tsrc = tempfile.mkdtemp(prefix="pyx-tsrc-")
tdst = tempfile.mkdtemp(prefix="pyx-tdst-")
for i in range(12):
    with open(os.path.join(tsrc, f"t{i}.bin"), "wb") as fh:
        fh.write(os.urandom(50_000))
before = len(ctx.transfer_log.transfers)
ctx.commands.copy([os.path.join(tsrc, f"t{i}.bin") for i in range(12)])
ctx.commands.paste(tdst)
tr = ctx.transfer_log.transfers[-1]
check(len(ctx.transfer_log.transfers) == before + 1 and ctx.transfer_ui.window.isVisible(),
      "paste starts a transfer in the pop-up window")
check(spin(lambda: tr.state == tm.DONE, 20) and tr.ok and {f.state for f in tr.files} == {tm.VERIFIED},
      "all 12 files copied and verified (on by default)")
tp = ctx.transfer_ui.panel
spin(timeout=0.4)
check(tp.selected is tr and tp.files.rowCount() == 12 and tp.files.data(tp.files.index(0, 0)) == "✓",
      "the window lists every file with a green tick")
check(ctx.transfer_ui.window.isVisible(), "the window stays open when done (default)")
ctx.commands.paste(tdst)                                  # again: "keep both" -> a second transfer, first one kept
check(spin(lambda: not ctx.transfer_log.transfers[-1].active, 20) and len(ctx.transfer_log.transfers) == before + 2
      and tp.list.topLevelItemCount() == len(ctx.transfer_log.transfers), "a second transfer is added; the first stays listed")
tp.mode_btn.click()
spin(timeout=0.3)
tt = ctx.transfer_ui.tab
check(tt is not None and getattr(tt, "KIND", "") == "transfers" and not ctx.transfer_ui.window.isVisible()
      and tp.isVisibleTo(tt), "'Open as tab' moves it into a Transfers tab")
check(tt.progress is None and tt.title == "Transfers", "no shading when nothing runs")
fake = tm.Transfer("copy", ["/x"], "/y")
fake.state, fake.total_bytes, fake.done_bytes = tm.RUNNING, 100, 40
ctx.transfer_log.add(fake)
check(abs(tt.progress - 0.4) < 1e-6 and tt.title == "Transfers 40 %", "running: the tab is shaded as a progress bar")
check(ctx.transfer_ui.overall()[0] == 0.4, "...and the taskbar gets the same progress")
dialogs_confirm = dialogs.confirm
dialogs.confirm = lambda *a, **k: False
check(not ctx.transfer_ui.confirm_quit(), "quitting while copying asks first")
dialogs.confirm = dialogs_confirm
ctx.transfer_log.transfers.remove(fake)
tp.mode_btn.click()
spin(timeout=0.3)
check(ctx.transfer_ui.tab is None and ctx.transfer_ui.window.isVisible(), "and back to its own window")
ctx.transfer_ui.window.hide()
main.activateWindow()
spin(timeout=0.2)
# (Leaving FILES on the clipboard makes Qt's offscreen platform crash at exit: it frees our
#  Python-made QMimeData after Python has shut down. Not an issue on X11; see system_clipboard.py.)
system_clipboard.put_text("")

print("session save + restore")
tab.panel.activate(tab.panel.tab_widget.indexOf(tab))   # (from the History tab: Ctrl+F2 needs a folder)
ctx.commands.open_rename()                               # a utility tab of each kind is saved too
ctx.commands.active_tab().rule_widgets["add_prefix"].setText("x_")
ctx.commands.open_search([data], name="inner")
spin(timeout=0.3)
state = main.session_state()
ctx.session.save(state)
ctx.settings.save()
history_ids = [r.id for r in ctx.history.records()]
ctx.history.save()
transfer_ids = [t.id for t in ctx.transfer_log.transfers]
ctx.transfer_log.save()
paths = [t.get("path") or t["utility"] for p in state["panels"] for t in p["tabs"]]
ctx.watcher.stop()
ctx.runner.shutdown()
main.terminal.shutdown()
main.close()

ctx2 = build_context(app)
ctx2.main.restore(ctx2.session.load())
ctx2.dispatcher.start()
spin(timeout=0.5)
restored = [t.KIND if getattr(t, "is_utility", False) else t.path for p in ctx2.main.panel_area.panels for t in p.tabs]
check(restored == paths, f"tabs restored: {restored}")
r2 = next(t for p in ctx2.main.panel_area.panels for t in p.tabs if getattr(t, "KIND", "") == "rename")
s2 = next(t for p in ctx2.main.panel_area.panels for t in p.tabs if getattr(t, "KIND", "") == "search")
check(r2.read_rules().add_prefix == "x_" and s2.name_edit.text() == "inner", "utility tabs come back with their settings")
check(ctx2.settings.get("view.mode") == "details", "view mode remembered")
check([t.id for t in ctx2.transfer_log.transfers] == transfer_ids and len(transfer_ids) >= 2,
      f"the transfer list ({len(transfer_ids)} transfers) is kept between sessions")
check([r.id for r in ctx2.history.records()] == history_ids and any(r.undone for r in ctx2.history.records()),
      f"history ({len(history_ids)} actions) kept between sessions, with what was undone")
# Regression: tabs restored before the window appeared used to get a 0-pixel-tall tab bar.
ctx2.main.resize(1100, 700)
ctx2.main.show()
spin(timeout=0.5)
bars = [p.tab_bar for p in ctx2.main.panel_area.panels]
check(all(b.height() >= 20 and b.isVisible() for b in bars),
      f"restored tabs are visible at startup (tab bar heights {[b.height() for b in bars]})")
ctx2.runner.shutdown()
print("\nGUI TESTS PASSED")

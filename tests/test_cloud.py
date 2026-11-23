"""
Tests for cloud-file support (OneDrive, Dropbox, ...) using SIMULATED file
attributes, so they run on any OS:
  * decoding Windows attributes / macOS flags into states,
  * finding which cloud folder a path belongs to,
  * macOS folder detection (with a fake home folder),
  * the "keep on device" / "free up space" commands that get sent,
  * badges, Status column, tooltips and the no-thumbnail rule in the real Qt model.

Run:  python tests/test_cloud.py
"""
import os
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from explorer.core import cloud  # noqa: E402
from explorer.services.cloud_providers import CloudProviders, CloudRoot, detect_mac, set_keep_on_device  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


print("state decoding")
check(cloud.state_from_attributes(cloud.WIN_RECALL_ON_DATA_ACCESS | 0x20) == cloud.ONLINE, "Windows cloud-only placeholder")
check(cloud.state_from_attributes(cloud.WIN_OFFLINE) == cloud.ONLINE, "Windows offline attribute = cloud only")
check(cloud.state_from_attributes(cloud.WIN_PINNED | 0x20) == cloud.PINNED, "Windows pinned = always keep")
check(cloud.state_from_attributes(0x20) == cloud.NONE, "ordinary Windows file")
check(cloud.state_from_attributes(0, cloud.MAC_DATALESS) == cloud.ONLINE, "macOS dataless = cloud only")
check(cloud.state_from_stat(SimpleNamespace(st_file_attributes=cloud.WIN_PINNED)) == cloud.PINNED, "from a stat result")
check(cloud.state_from_stat(os.stat(__file__)) == cloud.NONE, "a real local file is not a cloud file")
check(cloud.effective_state(cloud.NONE, True) == cloud.LOCAL, "plain file inside a cloud folder = on this device")
check(cloud.effective_state(cloud.NONE, False) == cloud.NONE, "plain file elsewhere = no badge")
check(cloud.effective_state(cloud.ONLINE, True) == cloud.ONLINE, "OS state wins")

print("finding cloud folders")
base = tempfile.mkdtemp()
od = os.path.join(base, "OneDrive")
od_work = os.path.join(base, "OneDrive - Contoso")
os.makedirs(os.path.join(od, "Docs"))
os.makedirs(od_work)
providers = CloudProviders([CloudRoot("OneDrive", "OneDrive", od), CloudRoot("OneDrive", "OneDrive - Contoso", od_work),
                            CloudRoot("OneDrive", "OneDrive", od)])
check(len(providers.roots) == 2, "duplicate registrations are merged")
check(providers.find(os.path.join(od, "Docs", "a.txt")).label == "OneDrive", "file inside OneDrive")
check(providers.find(os.path.join(od_work, "x")).label == "OneDrive - Contoso", "'OneDrive - Contoso' not mistaken for 'OneDrive'")
check(providers.find(os.path.join(base, "elsewhere")) is None, "outside any cloud folder")
check(providers.find("sftp://me@host:22/x") is None, "network paths are never cloud folders")
check(providers.root_at(od) is not None and providers.root_at(os.path.join(od, "Docs")) is None, "root_at")

print("macOS detection (fake home)")
home = tempfile.mkdtemp()
for name in ("OneDrive-Personal", "Dropbox", "GoogleDrive-me@example.com"):
    os.makedirs(os.path.join(home, "Library", "CloudStorage", name))
os.makedirs(os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs"))
found = {r.provider: r for r in detect_mac(home)}
check(set(found) == {"OneDrive", "Dropbox", "Google Drive", "iCloud Drive"}, f"found {sorted(found)}")
check(found["OneDrive"].label == "OneDrive - Personal" and not found["OneDrive"].can_pin, "labels; no pinning on macOS")

print("keep on device / free up space commands")
calls = []
set_keep_on_device(r"C:\Users\me\OneDrive\Docs", True, True, run=calls.append)
check(calls == [["+P", "-U", r"C:\Users\me\OneDrive\Docs"], ["+P", "-U", os.path.join(r"C:\Users\me\OneDrive\Docs", "*"), "/S", "/D"]],
      "folder: pin the folder and everything inside")
calls.clear()
set_keep_on_device(r"C:\Users\me\OneDrive\a.pdf", False, False, run=calls.append)
check(calls == [["-P", "+U", r"C:\Users\me\OneDrive\a.pdf"]], "file: free up space")

# ---------------------------------------------------------------- the Qt side
print("badges in the real interface")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from explorer.app import build_context  # noqa: E402
from explorer.ui.models.file_model import COLUMN_TITLES  # noqa: E402

ctx = build_context(app)
ctx.cloud.set_roots([CloudRoot("OneDrive", "OneDrive", od, None, can_pin=True)])
for name in ("report.pdf", "notes.txt", "photo.png", "kept.docx"):
    open(os.path.join(od, name), "w").close()
img = QImage(200, 150, QImage.Format.Format_RGB32)
img.fill(QColor("#3a7bd5"))
img.save(os.path.join(od, "photo.png"))
ctx.main.restore(None)
ctx.dispatcher.start()
ctx.main.show()


def spin(cond=lambda: False, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


tab = ctx.commands.active_tab()
tab.navigate(od)
check(spin(lambda: not tab._loading and tab.listing.total == 5), "OneDrive folder loads")
states = {"report.pdf": cloud.ONLINE, "photo.png": cloud.ONLINE, "kept.docx": cloud.PINNED}
for e in tab.listing.all_entries:          # simulate what Windows would report
    e.cloud = states.get(e.name, cloud.NONE)
ctx.settings.set("view.visible_columns", ["name", "size", "type", "modified", "status"])
spin(timeout=0.2)
m = tab.qmodel
check("status" in m.columns and COLUMN_TITLES["status"] == "Status", "Status column can be shown")


def row(name, col="name", role=Qt.ItemDataRole.DisplayRole):
    path = os.path.join(od, name)
    return m.data(m.index_for_path(path, m.columns.index(col)), role)


check(row("report.pdf", "status") == "OneDrive · Cloud only", "status text: cloud only")
check(row("notes.txt", "status") == "OneDrive · On this device", "status text: downloaded")
check(row("kept.docx", "status") == "OneDrive · Always on device", "status text: pinned")
check("cloud only" in row("report.pdf", role=Qt.ItemDataRole.ToolTipRole), "tooltip says cloud only")
icon_online = row("report.pdf", role=Qt.ItemDataRole.DecorationRole)
icon_plain = ctx.icons.for_entry(next(e for e in m.top_entries() if e.name == "report.pdf"))
check(icon_online is not None and icon_online.cacheKey() != icon_plain.cacheKey(), "cloud files get a badged icon")
check(row("Docs", "status") == "OneDrive · On this device", "folders get a state too")

ctx.commands.set_view_mode("icons")
ctx.commands.set_zoom(4)
spin(timeout=1.0)
thumb_requests = []
orig_get = ctx.thumbnails.get
ctx.thumbnails.get = lambda path, *a, **k: (thumb_requests.append(path), orig_get(path, *a, **k))[1]
row("photo.png", role=Qt.ItemDataRole.DecorationRole)
check(os.path.join(od, "photo.png") not in thumb_requests, "no thumbnail for a cloud-only image (would download it)")
for e in m.top_entries():
    if e.name == "photo.png":
        e.cloud = cloud.NONE
row("photo.png", role=Qt.ItemDataRole.DecorationRole)
check(os.path.join(od, "photo.png") in thumb_requests, "downloaded images still get thumbnails")
ctx.thumbnails.get = orig_get
for e in m.top_entries():
    e.cloud = states.get(e.name, cloud.NONE)

from explorer.ui.context_menu import build_item_menu  # noqa: E402
entries = [e for e in m.top_entries() if e.name == "report.pdf"]
labels = [a.text() for a in build_item_menu(ctx, tab, entries).actions()]
check("Always keep on this device" in labels and "Free up space" in labels, "context menu offers keep / free up")
check("Show in system file manager" in labels, "context menu offers 'Show in system file manager'")

if "--shots" in sys.argv:
    shots = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
    os.makedirs(shots, exist_ok=True)
    ctx.commands.set_theme("light")
    for mode, zoom in (("icons", 4), ("details", 3)):
        ctx.commands.set_view_mode(mode)
        ctx.commands.set_zoom(zoom)
        spin(timeout=1.0)
        ctx.main.grab().save(os.path.join(shots, f"cloud_{mode}.png"))
ctx.watcher.stop()
ctx.runner.shutdown()
print("\nCLOUD TESTS PASSED")

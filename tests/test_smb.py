"""
Tests for SMB shares (smb://) against a REAL Samba server.

Needs: pip install smbprotocol, and a server. By default it uses
smb://nasuser:secret123@127.0.0.1 with the shares "media" (with Music/ and
readme.txt) and "backups" - set PYX_SMB_HOST / PYX_SMB_USER / PYX_SMB_PASS /
PYX_SMB_SHARE to test against your own NAS (it only writes inside a
"pyexplorer-test" folder it creates and deletes).

Run:  python tests/test_smb.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("PYEXPLORER_CONFIG_DIR", tempfile.mkdtemp())

from explorer.core.connections import Connections  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.query import compile_query  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.core.tasks import CancelToken, JobManager, TaskRunner, UIDispatcher  # noqa: E402
from explorer.services.fileops import FileOperations  # noqa: E402
from explorer.services.walker import auto_workers, parallel_walk  # noqa: E402
from explorer.vfs.registry import VfsRegistry  # noqa: E402
from explorer.vfs.sftp import CredentialsProvider  # noqa: E402

HOST = os.environ.get("PYX_SMB_HOST", "127.0.0.1")
USER = os.environ.get("PYX_SMB_USER", "nasuser")
PASS = os.environ.get("PYX_SMB_PASS", "secret123")
SHARE = os.environ.get("PYX_SMB_SHARE", "media")


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


class Answers(CredentialsProvider):
    """Plays the user answering the login box."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def ask_login(self, conn, reason=""):
        self.asked.append(reason)
        return self.answers.pop(0) if self.answers else None


bus = EventBus()
cfg = os.environ["PYEXPLORER_CONFIG_DIR"]
answers = Answers([(USER, "wrong-password"), (USER, PASS)])
vfs = VfsRegistry(Connections(JsonStore(cfg + "/c.json"), bus), answers)

print("logging in")
root = f"smb://{HOST}/"
fs = vfs.resolve(root)
shares = [e.name for e in fs.list_dir(root)]
check(len(answers.asked) == 2 and "Wrong" in answers.asked[1], "asks for login, then again after a wrong password")
check(SHARE in shares and not any(s.endswith("$") for s in shares), f"lists the shares: {shares}")
check(vfs.from_user_input(f"\\\\{HOST}\\{SHARE}\\Music") == f"smb://{HOST}/{SHARE}/Music", "\\\\server\\share typed on Linux")

print("browsing")
share_uri = fs.join(root, SHARE)
names = {e.name: e for e in fs.list_dir(share_uri)}
check(len(names) > 0, f"lists the share ({len(names)} items)")
some_dir = next((e for e in names.values() if e.is_dir), None)
check(some_dir is not None and some_dir.path.startswith("smb://"), "folders come back as smb:// paths")
check(fs.stat(share_uri).is_dir, "stat on the share root")
usage = fs.disk_usage(share_uri)
check(usage is not None and usage[0] > 0, "free space of the share")
check([c[0] for c in fs.breadcrumbs(some_dir.path)][-2:] == [SHARE, some_dir.name], "breadcrumbs")

print("changing files")
test_dir = fs.join(share_uri, "pyexplorer-test")
if fs.exists(test_dir):
    fs.remove_tree(test_dir)
fs.mkdir(test_dir)
with fs.open_write(fs.join(test_dir, "hello.txt")) as fh:
    fh.write(b"hello from pyexplorer")
with fs.open_read(fs.join(test_dir, "hello.txt")) as fh:
    check(fh.read() == b"hello from pyexplorer", "write + read a file")
fs.rename(fs.join(test_dir, "hello.txt"), fs.join(test_dir, "renamed.txt"))
check(fs.exists(fs.join(test_dir, "renamed.txt")), "rename")
fs.set_mtime(fs.join(test_dir, "renamed.txt"), 1_600_000_000)
check(abs(fs.stat(fs.join(test_dir, "renamed.txt")).mtime - 1_600_000_000) < 2, "set modification time")

disp = UIDispatcher(lambda ms, fn: None)
runner = TaskRunner(disp)


def pump_until(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end and not cond():
        disp.pump_once()
        time.sleep(0.01)
    disp.pump_once()
    return cond()


errors = []
ops = FileOperations(vfs, runner, JobManager(disp, bus), lambda s, d: ("rename", True),
                     lambda title, errs: errors.append(errs), lambda folders: None)
local = tempfile.mkdtemp()
os.mkdir(os.path.join(local, "album"))
for i in range(20):
    with open(os.path.join(local, "album", f"t{i}.flac"), "wb") as fh:
        fh.write(os.urandom(50_000))
done = []
ops.transfer([os.path.join(local, "album")], test_dir, move=False, on_done=done.append)
check(pump_until(lambda: done), "copy a local folder to the share")
check(len(list(fs.list_dir(fs.join(test_dir, "album")))) == 20 and not errors, "all 20 files arrived")
back = tempfile.mkdtemp()
done.clear()
ops.transfer([fs.join(test_dir, "album")], back, move=False, on_done=done.append)
check(pump_until(lambda: done) and len(os.listdir(os.path.join(back, "album"))) == 20, "copy it back from the share")
with open(os.path.join(back, "album", "t3.flac"), "rb") as a, open(os.path.join(local, "album", "t3.flac"), "rb") as b:
    check(a.read() == b.read(), "contents survive the round trip")
done.clear()
ops.transfer([fs.join(test_dir, "album")], test_dir, move=False, on_done=done.append)
check(pump_until(lambda: done) and fs.exists(fs.join(test_dir, "album (2)")), "duplicate inside the share")

print("search (parallel walk over the network)")
seen = []
t0 = time.time()
parallel_walk(fs, share_uri, CancelToken(), seen.extend, workers=auto_workers(fs))
q = compile_query("*.flac")
check(len([e for e in seen if q.match(e)]) == 40, f"walked {len(seen)} items in {time.time() - t0:.2f}s, found 40 .flac")

print("deleting")
fs.remove_tree(test_dir)
check(not fs.exists(test_dir), "delete a folder tree on the share")
runner.shutdown()
vfs.close_all()

# ---------------------------------------------------------------- the real interface
print("in the interface")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from explorer.app import build_context  # noqa: E402
from explorer.ui import dialogs  # noqa: E402

app = QApplication([])
logins = []
dialogs.ask_login = lambda parent, title, message, user="", remember_where="", remember=True: (
    logins.append(title), (USER, PASS, False))[1]   # (user, password, remember)
ctx = build_context(app)
ctx.main.restore(None)
ctx.dispatcher.start()
ctx.main.resize(1200, 760)
ctx.main.show()


def spin(cond=lambda: False, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


tab = ctx.commands.active_tab()
tab.navigate(f"\\\\{HOST}")                 # typed like on Windows
check(spin(lambda: not tab._loading and tab.listing.total >= 1), "typing \\\\server opens the server")
check(len(logins) == 1 and tab.path.startswith("smb://"), "the login box appeared once")
check(SHARE in [e.name for e in tab.listing.all_entries], "its shares are shown as folders")
share_entry = next(e for e in tab.listing.all_entries if e.name == SHARE)
tab.on_view_activate([share_entry], False)
check(spin(lambda: not tab._loading and tab.path.endswith("/" + SHARE)), "double-click opens the share")
check([c[0] for c in tab.breadcrumb.crumbs][-1] == SHARE, "breadcrumbs show server > share")
check(spin(lambda: "free of" in ctx.main.statusBar().space.text()), "status bar shows the share's free space")
ctx.commands.set_view_mode("tree")
spin(timeout=0.3)
sub = next((e for e in tab.listing.all_entries if e.is_dir), None)
if sub is not None:
    tab.view.expand(tab.qmodel.index_for_path(sub.path))
    check(spin(lambda: tab.qmodel.rowCount(tab.qmodel.index_for_path(sub.path)) > 0), "tree view expands a folder on the share")
ctx.commands.set_view_mode("details")
share_uri = tab.path
ctx.commands.open_search([share_uri], name="ext:mp3")
st = ctx.commands.active_tab()
check(spin(lambda: not st._running and st._stats is not None, 30), "the Search tab searches inside the share: " + st.summary())
check(st._stats.built_now and all(e.path.startswith("smb://") for e in st.listing.visible),
      f"...indexed over SMB ({st._stats.indexed} items, {st.listing.total} .mp3 results)")
st.panel.close_tab(st, remember=False)
spin(lambda: not tab._loading)
if "--shots" in sys.argv:
    shots = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
    os.makedirs(shots, exist_ok=True)
    tab.navigate(f"smb://{HOST}/")
    spin(lambda: not tab._loading)
    spin(timeout=0.5)
    ctx.main.grab().save(os.path.join(shots, "smb_shares.png"))
print("opening a video from the share in its app (no download)")
from explorer.services import platform_ops  # noqa: E402
launched = []
platform_ops.launch = lambda argv: launched.append(argv)
video_dir = f"smb://{HOST}/{SHARE}/Films"
tab.navigate(video_dir)
if spin(lambda: not tab._loading and any(e.name.endswith(".mkv") for e in tab.listing.all_entries)):
    video = next(e for e in tab.listing.all_entries if e.name.endswith(".mkv"))
    downloads_before = len(ctx.jobs.jobs)
    tab.on_view_activate([video], False)
    check(spin(lambda: launched, 15), "double-click starts the app")
    target = launched[-1][-1]
    check(target.startswith("http://127.0.0.1:") or os.path.exists(target),
          f"the app gets a stream/path, not a downloaded copy: {launched[-1][0]} … {target[:60]}")
    check(len(ctx.jobs.jobs) == downloads_before, "nothing was downloaded")
    from explorer.ui.context_menu import build_item_menu  # noqa: E402
    menu = build_item_menu(ctx, tab, [video])
    open_with = next((a.menu() for a in menu.actions() if a.text() == "Open With"), None)
    if ctx.desktop_apps is not None and open_with is not None:
        names = [a.text() for a in open_with.actions()]
        check(any("mpv" in n for n in names) and "Other application…" in names, f"Open With lists the apps: {names}")
    ctx.settings.set("network.open_files", "download")
    launched.clear()
    tab.on_view_activate([video], False)
    check(spin(lambda: launched, 30) and launched[-1][-1].startswith(tempfile.gettempdir()),
          "with 'download first' chosen in Preferences, it downloads and then opens")
    ctx.settings.set("network.open_files", "stream")
else:
    print("  (no .mkv in Films on this server - skipping)")
saved = ctx.connections.save_connection({"name": "TrueNAS", "protocol": "smb", "host": HOST, "port": 445,
                                          "user": USER, "password": "", "initial_path": "/"})
check(ctx.connections.root_uri(saved) == f"smb://{USER}@{HOST}:445/", "a saved SMB connection")
ctx.watcher.stop()
ctx.runner.shutdown()
ctx.vfs.close_all()
print("\nSMB TESTS PASSED")

"""Tests for the History tab's engine: core/file_history.py (records, budget, saving)
and FileOperations recording + undo. No GUI. Run:  python tests/test_history.py"""
import os, sys, time, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
from explorer.core.file_history import FileHistory, Record, SAVE_BUDGET_ITEMS, MAX_AGE_DAYS
from explorer.core.storage import JsonStore
from explorer.core.tasks import UIDispatcher, TaskRunner, JobManager
from explorer.core.events import EventBus
from explorer.core.connections import Connections
from explorer.vfs.registry import VfsRegistry
from explorer.vfs.sftp import CredentialsProvider
from explorer.services import fileops as fileops_mod
from explorer.services.fileops import FileOperations

cfg = os.environ["PYEXPLORER_CONFIG_DIR"]

# ---------------------------------------------------------------- FileHistory
changes = []
keep = {"on": True}
store = JsonStore(os.path.join(cfg, "history.json"))
h = FileHistory(store, keep=lambda: keep["on"], on_change=lambda: changes.append(1))
assert len(h) == 0
r1 = h.record("rename", [{"src": "/a/x", "dst": "/a/y"}])
assert h.record("move", []) is None, "empty actions are not recorded"
r2 = h.record("trash", [{"src": "/a/z", "dst": None}])
assert [r.id for r in h.records()] == [r2.id, r1.id], "newest first"
assert r1.pending() == [0] and r2.pending() == [], "trash can't be undone"
h.mark_undone(r1.id, [0])
assert r1.undone and not r1.pending() and r1.undone_at
assert changes, "changes are announced"
h.save()
data = json.load(open(store.path))
assert len(data["records"]) == 2
h2 = FileHistory(store, keep=lambda: True)
assert [r.id for r in h2.records()] == [r2.id, r1.id] and h2.get(r1.id).undone, "reloaded with undo state"

# partly undone, by (src, dst) pairs
r3 = h.record("rename", [{"src": "/b/1", "dst": "/b/2"}, {"src": "/b/3", "dst": "/b/4"}])
h.mark_undone_pairs(r3.id, [("/b/3", "/b/4")])
assert r3.partly_undone and r3.pending() == [0]

# blocked items never count as pending
r4 = h.record("move", [{"src": "/c/1", "dst": "/d/1", "blocked": "Merged"}, {"src": "/c/2", "dst": "/d/2"}])
assert r4.pending() == [1]

# size budget: a huge record stays for this session only, smaller ones still fit
huge = h.record("rename", [{"src": f"/h/{i}", "dst": f"/h/{i}x"} for i in range(SAVE_BUDGET_ITEMS + 1)])
small = h.record("create", [{"src": None, "dst": "/n/new"}])
h.save()
saved_ids = {d["id"] for d in json.load(open(store.path))["records"]}
assert huge.id not in saved_ids and small.id in saved_ids and r1.id in saved_ids, saved_ids
assert huge.session_only and not small.session_only
assert os.path.getsize(store.path) < 100_000

# old records are dropped
old = Record("old1", "rename", time.time() - (MAX_AGE_DAYS + 1) * 86400, [{"src": "/o", "dst": "/p"}])
h._records.insert(0, old)
h.save()
assert "old1" not in {d["id"] for d in json.load(open(store.path))["records"]}

# "keep after closing" off: the file is deleted and nothing is loaded
keep["on"] = False
h.save()
assert not store.path.exists()
assert len(FileHistory(store, keep=lambda: False)) == 0
keep["on"] = True
h.remove([small.id])
assert h.get(small.id) is None
h.clear()
assert len(h) == 0
print("FileHistory OK")

# ---------------------------------------------------------------- FileOperations + undo
trashed = []
fileops_mod.move_to_trash = lambda p: (trashed.append(p), os.rename(p, p + ".TRASHED"))   # keep the real trash clean

disp = UIDispatcher(lambda ms, fn: None)
runner = TaskRunner(disp)
bus = EventBus()
jobs = JobManager(disp, bus)
vfs = VfsRegistry(Connections(JsonStore(os.path.join(cfg, "c.json")), bus), CredentialsProvider())
hist = FileHistory(None)
answers = {"choice": ("rename", True)}
ops = FileOperations(vfs, runner, jobs, lambda s, d: answers["choice"], lambda t, e: print("ERR", t, e),
                     lambda folders: None, lambda kind, items: disp.post(hist.record, kind, items))


def pump_until(cond, t=10):
    end = time.time() + t
    while time.time() < end and not cond():
        disp.pump_once()
        time.sleep(0.005)
    assert cond()


def run(start):
    """Start an operation with on_done=... and wait for it; returns its result."""
    out = []
    start(out.append)
    pump_until(lambda: out)
    for _ in range(20):                      # the history record is posted right after on_done
        disp.pump_once()
    return out[0]


def latest():
    return hist.records()[0]


def undo(rec, indexes=None):
    items = [rec.items[i] for i in (indexes if indexes is not None else rec.pending())]
    res = run(lambda cb: ops.undo(rec.kind, items, on_done=cb))
    todo = indexes if indexes is not None else rec.pending()
    hist.mark_undone(rec.id, [todo[n] for n, err in res.items() if not err])
    return res


root = tempfile.mkdtemp()
P = lambda *a: os.path.join(root, *a)
def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)

# -- single rename + undo
write(P("a.txt"), "A")
run(lambda cb: ops.rename(P("a.txt"), "b.txt", on_done=cb, on_error=cb))
rec = latest()
assert rec.kind == "rename" and rec.items[0]["src"] == P("a.txt") and rec.items[0]["dst"] == P("b.txt")
assert undo(rec) == {0: None} and os.path.exists(P("a.txt")) and not os.path.exists(P("b.txt"))
assert rec.undone

# -- undo refuses to overwrite: rename, then a new file takes the old name
run(lambda cb: ops.rename(P("a.txt"), "c.txt", on_done=cb, on_error=cb))
rec = latest()
write(P("a.txt"), "NEW")
res = undo(rec)
assert res[0] and "already exists" in res[0], res
assert open(P("a.txt")).read() == "NEW" and os.path.exists(P("c.txt")), "nothing overwritten"
assert not rec.undone and rec.pending() == [0]
os.remove(P("a.txt"))
assert undo(rec) == {0: None} and open(P("a.txt")).read() == "A", "retry works once the name is free"

# -- undo of a swap (Rename tab style: a<->b in one record)
write(P("s", "one"), "1"); write(P("s", "two"), "2")
from explorer.services.bulk_rename import rename_all
res = rename_all(vfs, [(P("s", "one"), P("s", "two")), (P("s", "two"), P("s", "one"))])
rec = hist.record("rename", [{"src": o, "dst": n} for o, n in res.done], source="Rename tab")
assert open(P("s", "one")).read() == "2"
assert all(e is None for e in undo(rec).values()) and open(P("s", "one")).read() == "1", "swap undone"

# -- move (same volume) + undo; folder with content
write(P("m", "src", "folder", "f.txt"), "F")
os.makedirs(P("m", "dst"))
run(lambda cb: ops.transfer([P("m", "src", "folder")], P("m", "dst"), move=True, on_done=cb))
rec = latest()
assert rec.kind == "move" and rec.items[0]["dst"] == P("m", "dst", "folder") and rec.items[0]["dir"]
assert undo(rec) == {0: None} and open(P("m", "src", "folder", "f.txt")).read() == "F"
assert not os.path.exists(P("m", "dst", "folder"))

# -- move back across "volumes" (copy + delete path), keeping the original name after "keep both"
write(P("k", "src", "doc.txt"), "ORIG"); write(P("k", "dst", "doc.txt"), "OTHER")
run(lambda cb: ops.transfer([P("k", "src", "doc.txt")], P("k", "dst"), move=True, on_done=cb))
rec = latest()
assert rec.items[0]["dst"] == P("k", "dst", "doc (2).txt"), rec.items
assert not rec.items[0].get("blocked"), "keep-both is not a merge"
same_volume = type(vfs.local).same_volume
type(vfs.local).same_volume = lambda self, a, b: False          # force the copy path
try:
    assert undo(rec) == {0: None}
finally:
    type(vfs.local).same_volume = same_volume
assert open(P("k", "src", "doc.txt")).read() == "ORIG" and open(P("k", "dst", "doc.txt")).read() == "OTHER"
assert not os.path.exists(P("k", "dst", "doc (2).txt"))

# -- a folder merged into an existing one can't be undone (it would take the old contents too)
write(P("g", "src", "pics", "new.jpg")); write(P("g", "dst", "pics", "old.jpg"))
run(lambda cb: ops.transfer([P("g", "src", "pics")], P("g", "dst"), move=True, on_done=cb))
rec = latest()
assert rec.items[0].get("blocked") and rec.pending() == []

# -- copy + undo -> the copies go to the trash; the originals stay
write(P("c", "a.txt")); write(P("c", "b.txt")); os.makedirs(P("c2"))
run(lambda cb: ops.transfer([P("c", "a.txt"), P("c", "b.txt")], P("c2"), move=False, on_done=cb))
rec = latest()
assert rec.kind == "copy" and len(rec.items) == 2
assert undo(rec, [1]) == {0: None} and trashed[-1] == P("c2", "b.txt"), "undo one file of an action"
assert rec.partly_undone and os.path.exists(P("c2", "a.txt")) and os.path.exists(P("c", "b.txt"))
undo(rec)
assert rec.undone and not os.path.exists(P("c2", "a.txt")) and os.path.exists(P("c", "a.txt"))

# -- copy that replaced a file: listed, but not undoable
write(P("r", "x.txt"), "new"); write(P("r2", "x.txt"), "old")
answers["choice"] = ("overwrite", True)
run(lambda cb: ops.transfer([P("r", "x.txt")], P("r2"), move=False, on_done=cb))
answers["choice"] = ("rename", True)
assert latest().items[0].get("blocked") and latest().pending() == []

# -- new folder / file: undo trashes them, but only while empty
run(lambda cb: ops.make_folder(root, "Fresh", on_done=cb, on_error=cb))
rec_folder = latest()
run(lambda cb: ops.make_file(root, "note.txt", on_done=cb, on_error=cb))
rec_file = latest()
assert rec_folder.kind == "create" and rec_folder.items[0]["dst"] == P("Fresh")
write(P("note.txt"), "typed something")
assert undo(rec_file)[0], "a file that has content now is not removed"
assert undo(rec_folder) == {0: None} and not os.path.exists(P("Fresh"))

# -- delete / trash are recorded, never undoable
write(P("d", "gone.txt")); write(P("d", "trash.txt"))
run(lambda cb: ops.delete([P("d", "gone.txt")], permanent=True, on_done=cb))
assert latest().kind == "delete" and latest().items[0]["src"] == P("d", "gone.txt") and latest().pending() == []
run(lambda cb: ops.delete([P("d", "trash.txt")], permanent=False, on_done=cb))
assert latest().kind == "trash" and "trash" not in latest().items[0] and latest().pending() == []

# -- failures are not recorded as done
before = len(hist)
run(lambda cb: ops.rename(P("does-not-exist"), "x", on_done=cb, on_error=cb))
assert len(hist) == before

runner.shutdown()
print("ALL OK")

"""Tests for the transfer engine (services/transfer_engine.py) and its model
(core/transfer_model.py): verify, keep going after failures, stop only when
the drive is full, refuse to start when it would not fit, moves, pause/cancel,
the saved log. No GUI. Run:  python tests/test_transfers.py"""
import errno, os, sys, time, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
from explorer.core.tasks import UIDispatcher, TaskRunner, JobManager
from explorer.core.events import EventBus
from explorer.core.storage import JsonStore
from explorer.core.connections import Connections
from explorer.core import transfer_model as tm
from explorer.core.transfer_model import Transfer, TransferLog, FileItem
from explorer.vfs.registry import VfsRegistry
from explorer.vfs.sftp import CredentialsProvider
from explorer.services import transfer_engine as te
from explorer.services.transfer_engine import TransferEngine, space_needed, is_disk_full

cfg = os.environ["PYEXPLORER_CONFIG_DIR"]
disp = UIDispatcher(lambda ms, fn: None)
runner = TaskRunner(disp)
bus = EventBus()
jobs = JobManager(disp, bus)
vfs = VfsRegistry(Connections(JsonStore(os.path.join(cfg, "c.json")), bus), CredentialsProvider())
LocalFS = type(vfs.local)
opts = {"verify": True, "algorithm": "blake2b", "verify_from_disk": True}
events, history, answers = [], [], {"choice": ("rename", True)}
log = TransferLog(None)
engine = TransferEngine(vfs, runner, jobs, log, lambda: dict(opts), lambda s, d: answers["choice"],
                        lambda folders: None, lambda kind, items: history.append((kind, items)),
                        lambda ev, t: events.append((ev, t.id)))


def wait(t, timeout=20):
    end = time.time() + timeout
    while time.time() < end and t.active:
        disp.pump_once()
        time.sleep(0.005)
    for _ in range(10):
        disp.pump_once()
    assert not t.active, t.state
    return t


def states(t):
    return {f.rel: f.state for f in t.files}


root = tempfile.mkdtemp()
P = lambda *a: os.path.join(root, *a)
def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)

# -- helpers
assert is_disk_full(OSError(errno.ENOSPC, "No space left on device"))
assert not is_disk_full(OSError(errno.EACCES, "Permission denied"))
assert space_needed([FileItem("a", "b", "b", 1), FileItem("c", "d", "d", 5000), FileItem("e", "f", "f", 0, True)]) \
    == 4096 + 8192 + 4096
assert space_needed([FileItem("a", "b", "b", 10 ** 9, action="rename")]) == 0, "renames take no space"

# -- 1. copy a tree: everything verified, contents identical
write(P("src", "photos", "a.jpg"), os.urandom(300_000))
write(P("src", "photos", "sub", "b.jpg"), os.urandom(5_000_000))
write(P("src", "photos", "empty.txt"), b"")
os.makedirs(P("dst"))
t = wait(engine.start([P("src", "photos")], P("dst"), move=False))
assert t.state == tm.DONE and t.ok, (t.state, t.message, states(t))
assert set(states(t).values()) == {tm.VERIFIED}, states(t)
assert open(P("dst", "photos", "sub", "b.jpg"), "rb").read() == open(P("src", "photos", "sub", "b.jpg"), "rb").read()
assert t.file_count == 3 and t.total_bytes == 5_300_000 and t.done_bytes == 5_300_000 and t.progress == 1.0
assert t.average_speed > 0 and t.finished >= t.started
assert ("started", t.id) in events and ("finished", t.id) in events
assert history[-1][0] == "copy" and history[-1][1][0]["dst"] == P("dst", "photos")
assert t in log.transfers

# -- 2. one unreadable file: marked failed, the rest still copied and verified
write(P("src2", "ok1.txt"), b"1"); write(P("src2", "locked.txt"), b"2"); write(P("src2", "ok2.txt"), b"3")
os.chmod(P("src2", "locked.txt"), 0)
can_read_locked = os.access(P("src2", "locked.txt"), os.R_OK)        # (root can read anything)
os.makedirs(P("dst2"))
t = wait(engine.start([P("src2")], P("dst2"), move=False))
s = states(t)
assert t.state == tm.DONE and s["src2/ok1.txt"] == tm.VERIFIED and s["src2/ok2.txt"] == tm.VERIFIED, s
if not can_read_locked:
    assert s["src2/locked.txt"] == tm.FAILED and t.failed == 1 and not t.ok
    assert s["src2"] == tm.FAILED and "1 item inside" in next(f.error for f in t.files if f.rel == "src2"), \
        "a folder with a failure inside is not a green tick"
    assert not os.path.exists(P("dst2", "src2", "locked.txt")), "no half-written file"
    assert not history or history[-1][1][0]["src"] != P("src2"), "incomplete: not offered for undo"
os.chmod(P("src2", "locked.txt"), 0o644)

# -- 3. verification catches a bad copy (simulated: the copy is changed before it is read back)
real_meta = TransferEngine._copy_metadata
def corrupt(src_fs, dst_fs, f):
    if f.rel.endswith("bad.bin"):
        with open(f.dst, "r+b") as fh:
            fh.write(b"\0")
    real_meta(src_fs, dst_fs, f)
TransferEngine._copy_metadata = staticmethod(corrupt)
real_same = type(vfs.local).same_volume
type(vfs.local).same_volume = lambda self, a, b: False          # a move across drives (same drive = rename)
write(P("src3", "good.bin"), b"good" * 1000); write(P("src3", "bad.bin"), b"\xff" * 4000)
os.makedirs(P("dst3"))
t = wait(engine.start([P("src3", "good.bin"), P("src3", "bad.bin")], P("dst3"), move=True))
TransferEngine._copy_metadata = staticmethod(real_meta)
type(vfs.local).same_volume = real_same
s = states(t)
assert s == {"good.bin": tm.VERIFIED, "bad.bin": tm.VERIFY_FAILED}, s
assert "checksums differ" in next(f.error for f in t.files if f.rel == "bad.bin")
assert not os.path.exists(P("src3", "good.bin")), "move: verified original removed"
assert os.path.exists(P("src3", "bad.bin")), "move: an original whose copy failed verification is KEPT"

# -- 4. verification off
opts["verify"] = False
write(P("src4", "f.txt"), b"abc"); os.makedirs(P("dst4"))
t = wait(engine.start([P("src4", "f.txt")], P("dst4"), move=False))
assert states(t) == {"f.txt": tm.COPIED} and t.ok
opts["verify"] = True

# -- 5. would not fit: nothing starts, the user is told
real_usage = LocalFS.disk_usage
LocalFS.disk_usage = lambda self, p: (10 ** 9, 10 ** 9 - 40 * 1024 * 1024, 40 * 1024 * 1024)   # 40 MB free
write(P("src5", "big.bin"), b"\0" * (9 * 1024 * 1024))
os.makedirs(P("dst5"))
t = wait(engine.start([P("src5", "big.bin")], P("dst5"), move=False))
LocalFS.disk_usage = real_usage
assert t.state == tm.NO_SPACE and "Not enough space" in t.message and "Nothing was copied" in t.message, t.message
assert states(t) == {"big.bin": tm.NOT_COPIED} and not os.listdir(P("dst5"))
assert ("no_space", t.id) in events

# -- 6. drive fills up mid-way: stop there (earlier files fine, the rest "not copied")
real_open_write = LocalFS.open_write
def full_on_third(self, path):
    if path.endswith("f3.bin"):
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_open_write(self, path)
LocalFS.open_write = full_on_third
for i in range(1, 6):
    write(P("src6", f"f{i}.bin"), bytes([i]) * 1000)
os.makedirs(P("dst6"))
t = wait(engine.start([P("src6", f"f{i}.bin") for i in range(1, 6)], P("dst6"), move=False))
LocalFS.open_write = real_open_write
s = states(t)
assert t.state == tm.NO_SPACE and "full" in t.message, t.message
assert s["f1.bin"] == s["f2.bin"] == tm.VERIFIED and s["f3.bin"] == tm.FAILED
assert s["f4.bin"] == s["f5.bin"] == tm.NOT_COPIED, s

# -- 7. other errors never stop it (a write error on one file in the middle)
def eacces_on_2(self, path):
    if path.endswith("f2.bin"):
        raise OSError(errno.EACCES, "Permission denied")
    return real_open_write(self, path)
LocalFS.open_write = eacces_on_2
os.makedirs(P("dst7"))
t = wait(engine.start([P("src6", f"f{i}.bin") for i in range(1, 6)], P("dst7"), move=False))
LocalFS.open_write = real_open_write
s = states(t)
assert t.state == tm.DONE and s["f2.bin"] == tm.FAILED and [s[f"f{i}.bin"] for i in (1, 3, 4, 5)] == [tm.VERIFIED] * 4

# -- 8. move within one drive = rename (instant, recorded for undo)
write(P("m", "folder", "x.txt"), b"x"); os.makedirs(P("m2"))
t = wait(engine.start([P("m", "folder")], P("m2"), move=True))
assert states(t) == {"folder": tm.MOVED} and os.path.exists(P("m2", "folder", "x.txt")) and not os.path.exists(P("m", "folder"))
assert history[-1] == ("move", [{"src": P("m", "folder"), "dst": P("m2", "folder"), "dir": True}])

# -- 9. move across drives (forced): copied, verified, originals and emptied folders removed
real_same = LocalFS.same_volume
LocalFS.same_volume = lambda self, a, b: False
write(P("x", "tree", "a.txt"), b"a"); write(P("x", "tree", "deep", "b.txt"), b"b"); os.makedirs(P("x2"))
t = wait(engine.start([P("x", "tree")], P("x2"), move=True))
LocalFS.same_volume = real_same
assert t.ok and set(states(t).values()) == {tm.VERIFIED}, states(t)
assert not os.path.exists(P("x", "tree")), "emptied source folders removed"
assert open(P("x2", "tree", "deep", "b.txt")).read() == "b"

# -- 10. name conflicts: keep both / skip
write(P("c", "doc.txt"), b"new"); write(P("c2", "doc.txt"), b"old")
t = wait(engine.start([P("c", "doc.txt")], P("c2"), move=False))
assert states(t) == {"doc (2).txt": tm.VERIFIED} and open(P("c2", "doc.txt"), "rb").read() == b"old"
answers["choice"] = ("skip", True)
t = wait(engine.start([P("c", "doc.txt")], P("c2"), move=False))
assert states(t) == {"doc.txt": tm.SKIPPED} and t.state == tm.DONE
answers["choice"] = ("rename", True)

# -- 11. pause / resume / cancel (a slow reader so there is time)
real_read = LocalFS.open_read
class Slow:
    def __init__(self, fh): self.fh = fh
    def read(self, n): time.sleep(0.05); return self.fh.read(64 * 1024)
    def __enter__(self): return self
    def __exit__(self, *a): self.fh.close()
LocalFS.open_read = lambda self, p: Slow(real_read(self, p)) if p.endswith(".slow") else real_read(self, p)
write(P("s", "big.slow"), os.urandom(3 * 1024 * 1024)); write(P("s", "after.txt"), b"z"); os.makedirs(P("s2"))
t = engine.start([P("s", "big.slow"), P("s", "after.txt")], P("s2"), move=False)
end = time.time() + 5
while time.time() < end and t.done_bytes == 0:
    time.sleep(0.01)
engine.pause(t)
time.sleep(0.2)
paused_at = t.done_bytes
time.sleep(0.3)
assert t.state == tm.PAUSED and t.done_bytes <= paused_at + 64 * 1024, "paused: no progress"
engine.resume(t)
time.sleep(0.8)                                  # (speed is sampled every 0.5 s of copying)
assert t.state == tm.RUNNING and t.done_bytes > paused_at
engine.cancel(t)
wait(t)
LocalFS.open_read = real_read
assert t.state == tm.CANCELLED and states(t) == {"big.slow": tm.NOT_COPIED, "after.txt": tm.NOT_COPIED}, states(t)
assert not os.path.exists(P("s2", "big.slow")), "cancelled file removed"
assert len(t.samples) >= 1, "speed samples for the plot"

# -- 12. copying into the same folder = duplicate; a folder into itself is refused
t = wait(engine.start([P("c", "doc.txt")], P("c"), move=False))
assert states(t) == {"doc (2).txt": tm.VERIFIED}
t = wait(engine.start([P("x2", "tree")], P("x2", "tree", "deep"), move=False))
assert list(states(t).values()) == [tm.FAILED] and "inside itself" in t.files[0].error

# -- 13. the log: kept between sessions within a budget; failed rows always kept; clear on demand
store = JsonStore(os.path.join(cfg, "transfers.json"))
saved = TransferLog(store)
for tr in log.transfers:
    saved.add(tr)
tm_budget = tm.SAVE_BUDGET_FILES
tm.SAVE_BUDGET_FILES = 3
saved.save()
tm.SAVE_BUDGET_FILES = tm_budget
data = json.load(open(store.path))
assert len(data["transfers"]) == len(log.transfers)
again = TransferLog(store)
first = again.transfers[0]
assert first.state == tm.DONE and first.total_bytes == 5_300_000 and first.file_count == 3, "summary always kept"
bad = next(tr for tr in again.transfers if tr.message.startswith("Stopped"))
assert {f.rel for f in bad.files if f.state in tm.BAD | {tm.NOT_COPIED}} >= {"f3.bin"}, "failed files kept"
assert again.transfers[-1].files, "newest transfers keep their full file list"
running = Transfer("copy", ["/a"], "/b")
again.add(running)
again.clear()
assert again.transfers == [running], "Clear keeps running transfers"
assert TransferLog(store, keep=lambda: False).transfers == []
off = TransferLog(store, keep=lambda: False)
off.save()
assert not os.path.exists(store.path), "not kept: the file is removed"

runner.shutdown()
print("ALL OK")

"""Tests for the non-GUI parts (query language, model, scanner, search + index,
file operations, trash, terminal). Run:  python tests/test_core.py"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
from explorer.core.query import compile_query
from explorer.core.fileinfo import FileEntry
from explorer.core.folder_model import FolderModel
from explorer.core.sorting import natural_key
from explorer.core.tasks import UIDispatcher, TaskRunner, JobManager
from explorer.core.events import EventBus
from explorer.core.settings import Settings
from explorer.core.storage import JsonStore
from explorer.core.connections import Connections
from explorer.vfs.registry import VfsRegistry
from explorer.vfs.sftp import CredentialsProvider
from explorer.services.scanner import DirectoryScanner
from explorer.services.search import SearchService
from explorer.services.fileops import FileOperations
from explorer.services.terminal import PtyBackend, SimpleShellBackend
from explorer.core.categories import Categories
from explorer.core.platform_info import default_categories

E = lambda n, d=False, s=0, m=0: FileEntry(n, "/x/"+n, "/x", d, s, m)
q = compile_query("ext:jpg;png !thumb")
assert q.match(E("a.JPG")) and not q.match(E("thumb.jpg")) and not q.match(E("a.gif"))
assert compile_query("*.py").match(E("x.py")) and not compile_query("*.py").match(E("x.pyc"))
assert compile_query("size:>1kb").match(E("a", s=2000)) and not compile_query("size:>1kb").match(E("a", s=10))
assert compile_query("foo|bar folder:").match(E("barx", True))
assert compile_query('"a b"').match(E("xa by")) 
assert compile_query("regex:^IMG_\\d+").match(E("IMG_123.jpg"))
assert compile_query("dm:7d").match(E("a", m=time.time()))
assert sorted(["f10","f2","F1"], key=natural_key) == ["F1","f2","f10"]

m = FolderModel()
h=E(".h"); h.hidden=True; m.add([E("b"), E("A", True), h]); m.add([E("a10"), E("a2")])
assert [e.name for e in m.visible] == ["A","a2","a10","b"], [e.name for e in m.visible]
m.set_show_hidden(True); assert len(m.visible)==5
m.set_sort("name", True, True); assert [e.name for e in m.visible][0]=="A"
m.set_query(compile_query("a")); assert [e.name for e in m.visible] == ["A","a10","a2"]

# dispatcher pumped manually
calls=[]
disp = UIDispatcher(lambda ms, fn: None); runner = TaskRunner(disp)
def pump_until(cond, t=10):
    end=time.time()+t
    while time.time()<end and not cond(): disp.pump_once(); time.sleep(0.005)
    assert cond()
bus = EventBus(); settings = Settings(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"]+"/s.json"), bus)
vfs = VfsRegistry(Connections(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"]+"/c.json"), bus), CredentialsProvider())
root = tempfile.mkdtemp()
for i in range(30):
    d = os.path.join(root, f"d{i}"); os.mkdir(d)
    for j in range(100): open(os.path.join(d, f"file{j}.txt"), "w").write("x"*j)
scanner = DirectoryScanner(vfs, runner)
got=[]; done=[]
scanner.scan(root, got.extend, done.append, lambda e: done.append(e))
pump_until(lambda: done); assert len(got)==30, len(got)
# search (in depth: tests/test_search.py)
from explorer.core.search_spec import SearchSpec
search = SearchService(vfs, runner, settings, cache_dir=tempfile.mkdtemp())
def run_search(spec):
    res, st = [], []
    search.search(spec, res.extend, lambda p: None, st.append, st.append)
    pump_until(lambda: st)
    return res, st[0]
t0=time.time(); res, st = run_search(SearchSpec(locations=[root], name="file5*"))
print("search walk", st, len(res), time.time()-t0)
assert len(res)==30*11 and st.built_now, (len(res), st)
res2, st2 = run_search(SearchSpec(locations=[os.path.join(root,"d3")], name="ext:txt"))
assert not st2.built_now and len(res2)==100, (st2, len(res2))
res3, st3 = run_search(SearchSpec(locations=[root], kind="files", max_results=500))
assert len(res3)==500 and st3.truncated, (len(res3), st3)
# file ops
jobs = JobManager(disp, bus); changed=[]
ops = FileOperations(vfs, runner, jobs, lambda s,d: ("rename", True), lambda t, e: print("ERR", t, e), changed.append)
dest = tempfile.mkdtemp(); out=[]
ops.transfer([os.path.join(root,"d1"), os.path.join(root,"d2")], dest, move=False, on_done=out.append)
pump_until(lambda: out); assert len(os.listdir(os.path.join(dest,"d1")))==100
out=[]; ops.transfer([os.path.join(root,"d1")], dest, move=False, on_done=out.append)
pump_until(lambda: out); print("merge copy ->", len(os.listdir(os.path.join(dest,"d1"))))
out=[]; ops.transfer([os.path.join(dest,"d2")], os.path.join(dest,"d2"), move=True, on_done=out.append)
pump_until(lambda: out)
out=[]; ops.transfer([os.path.join(dest,"d2")], os.path.join(dest,"d1"), move=True, on_done=out.append)
pump_until(lambda: out); assert os.path.isdir(os.path.join(dest,"d1","d2"))
out=[]; ops.delete([os.path.join(dest,"d1","file3 (2).txt")], permanent=True, on_done=out.append)
pump_until(lambda: out); assert not os.path.exists(os.path.join(dest,"d1","file3 (2).txt"))
out=[]; ops.make_folder(dest, "New folder", on_done=out.append); pump_until(lambda: out); print(out)
out=[]; ops.rename(dest+"/New folder", "Renamed", on_done=out.append, on_error=out.append); pump_until(lambda: out); print(out)
# trash (freedesktop fallback or gio)
os.environ["XDG_DATA_HOME"]=tempfile.mkdtemp()
out=[]; ops.delete([os.path.join(dest,"Renamed")], permanent=False, on_done=out.append); pump_until(lambda: out); print("trashed", out, os.path.exists(os.path.join(dest,"Renamed")))
# parser
# pty
outbuf=[]; ex=[]
b = PtyBackend(outbuf.append, ex.append, "/bin/bash --norc --noprofile")
b.start(root, 100, 24); time.sleep(0.5); b.write("echo hello-$((40+2))\r"); time.sleep(0.5); b.change_directory(os.path.join(root,"d4")); b.write("pwd\r"); time.sleep(0.6)
txt="".join(outbuf); print(repr(txt[-300:])); assert "hello-42" in txt and "/d4" in txt
b.close()
# simple (line-by-line) shell, used on Windows without pywinpty
outbuf=[]; sb = SimpleShellBackend(outbuf.append, ex.append); sb.start(root, 80, 24)
sb.write("cd d5"); sb.write("echo simple-ok"); time.sleep(0.8)
txt="".join(outbuf); assert "simple-ok" in txt and sb.cwd.endswith("d5"), txt
cats = Categories(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"]+"/cat.json"), bus, default_categories)
c = cats.all(); print([ (x["name"], len(x["items"])) for x in c])
i = cats.add_item(c[0]["id"], "/tmp", "tmp"); cats.move_item(i, c[2]["id"], 0); assert cats.all()[2]["items"][0]["label"]=="tmp"
runner.shutdown(); print("ALL OK")

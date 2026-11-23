"""
Tests for the search engine (index, fast scan, filters, contents, updates).

    python tests/test_search.py

The core check: for many queries and filter combinations, the fast index
search must return EXACTLY what a slow, obvious search (os.walk + the full
query on every file) returns. Then: searching inside files, keeping the index
current after changes, saving/loading, and speed on a real disk.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()

from explorer.core.connections import Connections  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.fileinfo import FileEntry  # noqa: E402
from explorer.core.query import QueryError, compile_query, scan_terms  # noqa: E402
from explorer.core.search_spec import DAY, KB, SearchSpec  # noqa: E402
from explorer.core.settings import Settings  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.core.tasks import TaskRunner, UIDispatcher  # noqa: E402
from explorer.services.content_search import ContentMatcher  # noqa: E402
from explorer.services.file_index import FileIndex, build_local, pattern_for  # noqa: E402
from explorer.services.search import SearchService  # noqa: E402
from explorer.vfs.registry import VfsRegistry  # noqa: E402
from explorer.vfs.sftp import CredentialsProvider  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


def write(path, text="", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# A test tree with a bit of everything
# ---------------------------------------------------------------------------
root = tempfile.mkdtemp(prefix="pyx-search-")
now = time.time()
write(f"{root}/Holiday Photos/IMG_0001.JPG", "x" * 5000, now - 400 * DAY)
write(f"{root}/Holiday Photos/IMG_0002.jpg", "x" * 300_000, now - 2 * DAY)
write(f"{root}/Holiday Photos/thumbs/IMG_0001_thumb.jpg", "x" * 100)
write(f"{root}/Holiday Photos/notes.txt", "Hotel in Málaga\nInvoice 2024-07 for the CAR\n")
write(f"{root}/Documents/report_2024.docx", "binary\0stuff Invoice", now - 30 * DAY)
write(f"{root}/Documents/report_2023.pdf", "x" * 2_000_000, now - 300 * DAY)
write(f"{root}/Documents/Taxes/invoice.TXT", "nothing to see\nreal invoice here\n")
write(f"{root}/Documents/Taxes/budget.xlsx", "x")
write(f"{root}/code/main.py", "def invoice_total():\n    return 1\n# invoices\n")
write(f"{root}/code/node_modules/lib/index.js", "invoice")
write(f"{root}/code/.git/config", "[core]")
write(f"{root}/.hidden_file.txt", "secret invoice")
write(f"{root}/İstanbul.txt", "unicode name whose lower() changes length")
write(f"{root}/Ünïcode Ñame.md", "ÉLAN vital")
os.makedirs(f"{root}/empty folder")
os.symlink(f"{root}/Documents", f"{root}/link to docs")
for i in range(300):
    write(f"{root}/many/file_{i:03d}.log", "", now - i * DAY)

print("building an index")
idx = build_local(root)
on_disk = sum(len(d) + len(f) for _, d, f in os.walk(root))
check(idx.count == on_disk, f"every entry is indexed ({idx.count} = {on_disk} from os.walk)")
names = {idx.path(i) for i in range(idx.count)}
check(f"{root}/İstanbul.txt" in names and f"{root}/Ünïcode Ñame.md" in names, "unicode names survive")
link = next(i for i in range(idx.count) if idx.name(i) == "link to docs")
check(idx.entry(link).is_link and idx.entry(link).is_dir, "a link to a folder is listed but not followed")
check(not any(p.startswith(f"{root}/link to docs/") for p in names), "...nothing is indexed twice through it")
e = idx.entry(next(i for i in range(idx.count) if idx.name(i) == "IMG_0002.jpg"))
check(e.size == 300_000 and abs(e.mtime - (now - 2 * DAY)) < 2, "sizes and dates are recorded")
check(idx.is_hidden(next(i for i in range(idx.count) if idx.name(i) == "config")), "files inside .git count as hidden")


# ---------------------------------------------------------------------------
# The reference: slow and obvious
# ---------------------------------------------------------------------------
def reference(spec: SearchSpec, location: str = root) -> set:
    query = compile_query(spec.name, spec.case_sensitive)
    out = set()
    for folder, dirs, files in os.walk(location):
        rel_parts = os.path.relpath(folder, location).split(os.sep)
        if any(p in spec.exclude for p in rel_parts):
            dirs[:] = []
            continue
        hidden_tree = any(p.startswith(".") for p in os.path.relpath(folder, root).split(os.sep) if p != ".")
        for name in dirs + files:
            path = os.path.join(folder, name)
            st = os.lstat(path)
            is_dir = os.path.isdir(path)
            if name in dirs and name in spec.exclude:
                continue
            e = FileEntry(name, path, folder, is_dir, 0 if is_dir else st.st_size, st.st_mtime,
                          is_link=os.path.islink(path))
            if not spec.include_hidden and (name.startswith(".") or hidden_tree):
                continue
            if spec.kind == "files" and is_dir or spec.kind == "folders" and not is_dir:
                continue
            if spec.extensions and (is_dir or e.ext not in spec.extensions):
                continue
            if spec.size_min is not None and (is_dir or e.size < spec.size_min):
                continue
            if spec.size_max is not None and (is_dir or e.size > spec.size_max):
                continue
            if spec.modified_after is not None and e.mtime < spec.modified_after:
                continue
            if spec.modified_before is not None and e.mtime > spec.modified_before:
                continue
            if query.match(e):
                out.add(path)
    return out


disp = UIDispatcher(lambda ms, fn: None)
runner = TaskRunner(disp)


def pump_until(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end and not cond():
        disp.pump_once()
        time.sleep(0.002)
    assert cond(), "timed out"


bus = EventBus()
settings = Settings(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"] + "/s.json"), bus)
vfs = VfsRegistry(Connections(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"] + "/c.json"), bus), CredentialsProvider())
cache = tempfile.mkdtemp(prefix="pyx-index-cache-")
updated = []
search = SearchService(vfs, runner, settings, cache_dir=cache, on_index_updated=updated.append)


def run(spec: SearchSpec, timeout=30):
    got, done, err, prog = [], [], [], []
    search.search(spec, got.extend, prog.append, done.append, err.append)
    pump_until(lambda: done or err, timeout)
    if err:
        raise err[0]
    return {e.path for e in got}, done[0], got, prog


print("index search == slow reference search")
QUERIES = ["", "img", "IMG", "img_000?.*", "*.jpg", "ext:jpg", "ext:jpg;txt", "jpg|txt", "report 2024",
           "!thumb img", "regex:^IMG_\\d+\\.", "photos", "folder:", "file: invoice", "size:>1kb", "dm:7d",
           "path:taxes", "İstanbul", "istanbul", "ünïcode", "case: IMG", "\"Holiday Photos\"", "*", "zzz",
           "file_1*", "file_??5.log", "notes|budget main"]
SPECS = [
    SearchSpec(),
    SearchSpec(include_hidden=True),
    SearchSpec(kind="files"), SearchSpec(kind="folders"),
    SearchSpec(extensions=["jpg", "txt"]),
    SearchSpec(size_min=10 * KB), SearchSpec(size_max=1 * KB, include_hidden=True),
    SearchSpec(modified_after=now - 10 * DAY), SearchSpec(modified_before=now - 100 * DAY),
    SearchSpec(exclude=["node_modules", "thumbs"], include_hidden=True),
    SearchSpec(case_sensitive=True),
]
compared = 0
for base in SPECS:
    for q in QUERIES:
        spec = SearchSpec.from_dict(dict(base.to_dict(), name=q, locations=[root]))
        got, stats, _, _ = run(spec)
        want = reference(spec)
        if got != want:
            raise AssertionError(f"query {q!r} with {base}:\n  missing {sorted(want - got)[:5]}\n  extra {sorted(got - want)[:5]}")
        compared += 1
check(True, f"{compared} query/filter combinations give exactly the reference results")

sub = f"{root}/Documents"
got, _, _, _ = run(SearchSpec(locations=[sub], name="report"))
check(got == reference(SearchSpec(name="report"), sub), "a sub-folder of an indexed folder is searched from the same index")
got, _, _, _ = run(SearchSpec(locations=[f"{root}/Documents", f"{root}/code", f"{root}/Documents/Taxes"], name="i"))
want = reference(SearchSpec(name="i"), f"{root}/Documents") | reference(SearchSpec(name="i"), f"{root}/code")
check(got == want, "several locations at once (a location inside another is not searched twice)")
got, stats, _, _ = run(SearchSpec(locations=[root], name="file_", max_results=50))
check(len(got) == 50 and stats.truncated, "results stop at the limit, and say so")
try:
    run(SearchSpec(locations=[root], name="regex:[unclosed"))
    raise AssertionError("bad regex accepted")
except QueryError:
    check(True, "a bad regex is reported, not crashed on")
terms, exact = scan_terms("holiday *.jpg")
check(exact and [t[0] for t in terms] == ["literal", "wildcard"], "plain words and wildcards need no second check")
check(not scan_terms("photo !thumb")[1], "negation is checked afterwards on the candidates")
p = pattern_for("wildcard", "IMG_????.jpg", False)
check(p.literal == ".jpg" or p.literal == "img_", f"wildcards scan for their longest plain part ({p.literal!r})")

print("searching inside files")
m = ContentMatcher("invoice")
hit = m.match_local(f"{root}/Documents/Taxes/invoice.TXT")
check(hit == "real invoice here", "finds the line with the text")
check(hit[hit.start:hit.end] == "invoice", "...and knows where in it the match is (shown in bold)")
long_line = ContentMatcher("needle").match_local(f"{root}/code/main.py")
write(f"{root}/long.txt", "x" * 500 + " the needle " + "y" * 500)
far = ContentMatcher("needle").match_local(f"{root}/long.txt")
check(len(far) <= 130 and far[far.start:far.end] == "needle" and far.startswith("…"),
      f"a long line is shortened around the match: {far[:40]!r}…")
os.remove(f"{root}/long.txt")
check(m.match_local(f"{root}/Documents/report_2024.docx") is None, "binary files are skipped...")
check(ContentMatcher("invoice", include_binary=True).match_local(f"{root}/Documents/report_2024.docx") is not None,
      "...unless asked")
check(ContentMatcher("málaga").match_local(f"{root}/Holiday Photos/notes.txt") == "Hotel in Málaga",
      "case-insensitive works for accented letters too")
check(ContentMatcher("élan").match_local(f"{root}/Ünïcode Ñame.md") is not None, "É matches é")
check(ContentMatcher("CAR", case_sensitive=True).match_local(f"{root}/Holiday Photos/notes.txt") is not None and
      ContentMatcher("car", case_sensitive=True).match_local(f"{root}/Holiday Photos/notes.txt") is None, "case-sensitive")
check(ContentMatcher("invoice", whole_word=True).match_local(f"{root}/code/main.py") is None and
      ContentMatcher("invoices", whole_word=True).match_local(f"{root}/code/main.py") == "# invoices",
      "whole words only")
check(ContentMatcher(r"\d{4}-\d{2}", regex=True).match_local(f"{root}/Holiday Photos/notes.txt").startswith("Invoice"),
      "regular expressions")
got, stats, entries, _ = run(SearchSpec(locations=[root], content="invoice", include_hidden=True))
want = {f"{root}/Documents/Taxes/invoice.TXT", f"{root}/Holiday Photos/notes.txt", f"{root}/code/main.py",
        f"{root}/code/node_modules/lib/index.js", f"{root}/.hidden_file.txt"}
check(got == want, f"a content search finds exactly the files containing the text ({len(got)})")
check(all(e.extra and e.extra.get("snippet") for e in entries), "each result carries the matching line")
got, _, _, _ = run(SearchSpec(locations=[root], name="ext:txt", content="invoice"))
check(got == {f"{root}/Documents/Taxes/invoice.TXT", f"{root}/Holiday Photos/notes.txt"},
      "name filters + contents together (hidden files excluded by default)")

print("keeping the index current")
write(f"{root}/Documents/new_invoice_2025.txt", "fresh")
write(f"{root}/Documents/Brand New/deep/inside.txt", "deep")
os.remove(f"{root}/Documents/report_2023.pdf")
shutil.rmtree(f"{root}/Documents/Taxes")
updated.clear()
search.folder_changed(f"{root}/Documents")
pump_until(lambda: updated, 10)
got, _, _, _ = run(SearchSpec(locations=[root], name="new_invoice|inside|report|budget"))
check(got == reference(SearchSpec(name="new_invoice|inside|report|budget")),
      "after a change: new files and new folders appear, deleted ones and their contents vanish")
check(updated == [root], "open Search tabs are told the index changed")
got, _, _, _ = run(SearchSpec(locations=[root], name=""))
check(got == reference(SearchSpec(name="")), "the patched index matches the disk exactly")

new_dir = f"{root}/made later"
write(f"{new_dir}/later.txt", "x")
got, _, _, _ = run(SearchSpec(locations=[new_dir], name="later"))
check(got == {f"{new_dir}/later.txt"}, "a folder made after indexing is read on the spot")

print("saving and loading")
manager = search.indexes
for r in manager.roots():
    manager._save(manager.index_for(r))
fresh = SearchService(vfs, runner, settings, cache_dir=cache)
check(fresh.indexes.load_saved() >= 1 and fresh.indexes.index_for(root) is not None, "indexes are saved and loaded")
old = manager.index_for(root)
new = fresh.indexes.index_for(root)
check(new.count == old.count and new.names == old.names and list(new.size) == list(old.size), "...unchanged")

print("speed on a real disk")
big_root = "/usr" if os.path.isdir("/usr/share") else os.path.expanduser("~")
t = time.perf_counter()
big = build_local(big_root)
build_s = time.perf_counter() - t
print(f"    indexed {big_root}: {big.count:,} entries in {build_s:.1f}s ({big.count / build_s:,.0f}/s)")
search.indexes._store(big)
timings = []
for q in ["python", "*.so", "ext:png;svg", "zzzzzz", "lib config", "regex:^test_.*\\.py$"]:
    t = time.perf_counter()
    got, stats, _, _ = run(SearchSpec(locations=[big_root], name=q, include_hidden=True, max_results=1_000_000))
    timings.append((q, len(got), time.perf_counter() - t))
for q, n, s in timings:
    print(f"    {q!r:28} {n:>8,} results  {s * 1000:6.0f} ms")
check(all(s < 1.5 for q, n, s in timings if n < 50_000), "name searches over the real disk are fast")
t = time.perf_counter()
got, _, _, _ = run(SearchSpec(locations=[big_root], size_min=10 * 1024 * 1024, include_hidden=True))
print(f"    files over 10 MB: {len(got)} in {(time.perf_counter() - t) * 1000:.0f} ms")
state = FileIndex.from_state(big.to_state())
check(state.count == big.count, "big index round-trips")
runner.shutdown()
shutil.rmtree(root, ignore_errors=True)
print("\nSEARCH TESTS PASSED")

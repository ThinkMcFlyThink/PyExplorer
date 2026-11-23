"""
SearchService - runs the Search tab's searches ("Everything"-style).

    token = search.search(spec, on_batch, on_progress, on_done, on_error)

Steps for every location in the spec:
  1. find an index that covers it (services/index_manager.py) - or build one
     first (the whole computer takes a while once; after that it is saved);
  2. the fast name scan over the index's big name string (file_index.py) -
     milliseconds, even for millions of files;
  3. the filters (type, extensions, size, date, hidden, excluded folders) over
     the index's compact arrays;
  4. the full query check, only for query parts the scan could not settle;
  5. if "containing text" was asked: read the remaining FILES (in parallel)
     and keep those that contain it (services/content_search.py).

Results are streamed to ``on_batch`` (UI thread) as they are found; the
search stops after ``spec.max_results``. A stale index still answers at once,
and a fresh one is built in the background (open Search tabs re-run when it
is ready - see IndexManager.on_updated).
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

from ..core.fileinfo import FileEntry
from ..core.query import compile_query, scan_terms
from ..core.search_spec import SearchSpec
from ..core.tasks import CancelToken, TaskRunner
from .content_search import ContentMatcher
from .file_index import DIR, HIDDEN, Cancelled, FileIndex, is_inside, pattern_for
from .index_manager import IndexManager, default_cache_dir

__all__ = ["SearchService", "SearchStats", "is_inside"]

BATCH = 2000                 # results handed to the UI at a time
CONTENT_WORKERS = 4          # files read at once when searching inside files


@dataclass
class SearchStats:
    matches: int
    seconds: float
    index_age: float          # seconds since the (oldest) index used was built
    indexed: int              # entries in the indexes searched
    built_now: bool           # an index had to be built for this search
    truncated: bool
    content_checked: int = 0  # files whose contents were read


class SearchService:
    def __init__(self, vfs, runner: TaskRunner, settings, cache_dir: Optional[str] = None,
                 devices: Callable[[], list] = lambda: [], on_index_updated: Callable[[str], None] = lambda r: None
                 ) -> None:
        self._vfs = vfs
        self._runner = runner
        self._settings = settings
        self.indexes = IndexManager(vfs, settings, cache_dir or default_cache_dir(), devices,
                                    post=runner.dispatcher.post, on_updated=on_index_updated)
        self.indexes.loaded.set()                     # nothing to wait for until start() is called

    # -- life cycle --------------------------------------------------------------
    def start(self) -> None:
        """Load the indexes saved last time, in the background (startup stays fast).
        Without start() (e.g. in tests) nothing is loaded and searches don't wait."""
        self.indexes.loaded.clear()
        self._runner.submit(self.indexes.load_saved, pool="search")

    def close(self) -> None:
        self.indexes.close()

    def folder_changed(self, folder: str) -> None:
        self.indexes.folder_changed(folder)

    # -- searching ------------------------------------------------------------------
    def search(self, spec: SearchSpec, on_batch: Callable[[List[FileEntry]], None],
               on_progress: Callable[[str], None], on_done: Callable[[SearchStats], None],
               on_error: Callable[[BaseException], None]) -> CancelToken:
        token = CancelToken()
        post = self._runner.dispatcher.post

        def deliver(batch: List[FileEntry]) -> None:          # UI thread
            if not token.cancelled:
                on_batch(batch)

        def progress(text: str) -> None:
            post(lambda t=text: None if token.cancelled else on_progress(t))

        def work() -> SearchStats:
            return self._run(spec, token, lambda b: post(deliver, b), progress)

        self._runner.submit(work, pool="search", token=token, on_done=on_done, on_error=on_error)
        return token

    def locations(self, spec: SearchSpec) -> List[str]:
        """The folders to search: the whole computer's roots, or the chosen ones
        without any that sit inside another chosen one."""
        if spec.whole_computer:
            return self.indexes.computer_roots()
        chosen = [loc for loc in dict.fromkeys(spec.locations) if loc]
        return [loc for loc in chosen if not any(o != loc and is_inside(loc, o) for o in chosen)]

    def _run(self, spec: SearchSpec, token: CancelToken, send: Callable[[List[FileEntry]], None],
             progress: Callable[[str], None]) -> SearchStats:
        start = time.monotonic()
        query = compile_query(spec.name, spec.case_sensitive)        # QueryError -> on_error
        if not self.indexes.loaded.is_set():
            progress("Loading the search index…")
            while not self.indexes.loaded.wait(0.1):
                if token.cancelled:
                    return SearchStats(0, 0.0, 0, 0, False, False)
        terms, exact = scan_terms(spec.name, spec.case_sensitive)
        patterns = sorted((pattern_for(*t) for t in terms), key=lambda p: -p.weight)
        matcher = None
        if spec.content:
            matcher = ContentMatcher(spec.content, spec.content_case, spec.content_regex,
                                     spec.content_whole_word, spec.content_max_mb * 1024 * 1024,
                                     spec.content_binary)              # re.error -> on_error
        plan: List[Tuple[FileIndex, str]] = []
        built_now = False
        for loc in self.locations(spec):
            idx = self.indexes.index_for(loc)
            if idx is not None and idx.folders_under(loc) == set():
                idx = None                          # a folder made after the index was built: read it now
            if idx is None:
                built_now = True
                name = os.path.basename(loc.rstrip("/\\")) or loc
                progress(f"Indexing {name}…")
                try:
                    idx = self.indexes.build(loc, token,
                                             lambda n, name=name: progress(f"Indexing {name}… {n:,} items"))
                except Cancelled:
                    return SearchStats(0, time.monotonic() - start, 0, 0, built_now, False)
            elif self.indexes.is_stale(idx):
                self.indexes.refresh_in_background(idx.root, self._runner)
            plan.append((idx, loc))
        if token.cancelled:
            return SearchStats(0, time.monotonic() - start, 0, 0, built_now, False)
        if matcher is not None:
            progress("Looking inside files…")
        found = 0
        checked = [0]
        truncated = False
        verify = not query.is_empty and not exact
        limit = max(1, spec.max_results)
        pool = ThreadPoolExecutor(CONTENT_WORKERS, thread_name_prefix="px-content") if matcher else None
        try:
            for idx, loc in plan:
                for ids in self._candidates(idx, loc, spec, patterns, token):
                    if token.cancelled:
                        break
                    entries = [idx.entry(i) for i in ids]
                    if verify:
                        entries = [e for e in entries if query.match(e)]
                    if matcher is not None and entries:
                        entries = self._with_content(entries, matcher, pool, token, checked)
                    if not entries:
                        continue
                    room = limit - found
                    if len(entries) >= room:
                        entries, truncated = entries[:room], True
                    for k in range(0, len(entries), BATCH):
                        send(entries[k:k + BATCH])
                    found += len(entries)
                    if truncated:
                        break
                if truncated or token.cancelled:
                    break
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        ages = [time.time() - idx.built_at for idx, _ in plan]
        return SearchStats(found, time.monotonic() - start, max(ages) if ages else 0,
                           sum(idx.count for idx, _ in plan), built_now, truncated, checked[0])

    # -- the fast part: name scan + filters over the index -------------------------------
    def _candidates(self, idx: FileIndex, location: str, spec: SearchSpec, patterns: list,
                    token: CancelToken) -> Iterator[List[int]]:
        """Chunks of entry ids that pass the name scan and every filter."""
        allowed = idx.folders_under(location, spec.exclude)
        if patterns:
            source = idx.scan(patterns[0])
        else:
            source = (list(range(k, min(k + 50_000, idx.count))) for k in range(0, idx.count, 50_000))
        parent, flags, size, mtime, dir_ids = idx.parent, idx.flags, idx.size, idx.mtime, idx.dir_id
        hidden_dirs = idx.hidden_dirs() if not spec.include_hidden else None
        want = spec.kind
        lo, hi = spec.size_min, spec.size_max
        after, before = spec.modified_after, spec.modified_before
        exts = tuple("." + e.lower() for e in spec.extensions)
        names, starts = idx.lower, idx.lstarts
        for ids in source:
            if token.cancelled:
                return
            for p in patterns[1:]:
                ids = idx.test(p, ids)
            out = []
            for i in ids:
                p = parent[i]
                if allowed is not None and p not in allowed:
                    continue
                f = flags[i]
                if allowed is not None and f & DIR and dir_ids[i] >= 0 and dir_ids[i] not in allowed:
                    continue                        # an excluded folder itself (e.g. node_modules)
                if want == "files" and f & DIR:
                    continue
                if want == "folders" and not f & DIR:
                    continue
                if hidden_dirs is not None and (f & HIDDEN or hidden_dirs[p]):
                    continue
                if lo is not None and (f & DIR or size[i] < lo):
                    continue
                if hi is not None and (f & DIR or size[i] > hi):
                    continue
                if after is not None and mtime[i] < after:
                    continue
                if before is not None and mtime[i] > before:
                    continue
                if exts and (f & DIR or not names[starts[i]:starts[i + 1] - 1].endswith(exts)):
                    continue
                out.append(i)
            if out:
                yield out

    # -- the slow part: looking inside files -------------------------------------------------
    def _with_content(self, entries: List[FileEntry], matcher: ContentMatcher, pool: ThreadPoolExecutor,
                      token: CancelToken, checked: list) -> List[FileEntry]:
        files = [e for e in entries if not e.is_dir]
        if not files:
            return []
        fs = self._vfs.resolve(files[0].path)
        if fs.is_local:
            check = lambda e: matcher.match_local(e.path)             # noqa: E731
        else:
            check = lambda e: matcher.match_stream(fs, e.path, e.size, token)  # noqa: E731
        out = []
        for e, snippet in zip(files, pool.map(lambda e: None if token.cancelled else check(e), files)):
            checked[0] += 1
            if snippet is not None:
                e.extra = {"snippet": str(snippet), "match": (getattr(snippet, "start", 0), getattr(snippet, "end", 0))}
                out.append(e)
        return out

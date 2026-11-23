"""
FileOperations - copy, move, delete, rename, create. All run in background
threads as "jobs" with progress + cancel in the status bar.

They work between ANY two file systems: local -> local uses fast OS calls;
anything involving a server streams the data in 1 MB chunks. So copying
from an SFTP server to a USB stick is the same code path as a local copy.

The UI is only involved through three small callbacks given to __init__:
    ask_conflict(src_entry, dst_entry) -> (choice, apply_to_all)
    report_errors(title, [messages])
    publish_changed([folders])          # tell open tabs to refresh
They are called from worker threads; the app wraps them so the actual
dialog/refresh happens on the UI thread.
"""
from __future__ import annotations

import os
import shutil
from typing import Callable, List, Optional, Tuple

from ..core.fileinfo import FileEntry
from ..core.formatting import human_size
from ..core.tasks import Cancelled, Job, JobManager, TaskRunner
from .platform_ops import move_to_trash, temp_download_dir
from .search import is_inside

CHUNK = 1024 * 1024
FAST_LOCAL_COPY_LIMIT = 64 * 1024 * 1024     # below this, use shutil.copy2 (kernel-accelerated)


class Conflict:
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"        # keep both
    CANCEL = "cancel"


def unique_name(fs, folder: str, name: str, is_dir: bool = False) -> str:
    """'report.pdf' -> 'report (2).pdf' (first one that doesn't exist)."""
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    if stem.startswith(".") and not ext and not is_dir:       # ".bashrc"
        stem, ext = name, ""
    n = 2
    candidate = name
    while fs.exists(fs.join(folder, candidate)):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return candidate


class FileOperations:
    def __init__(self, vfs, runner: TaskRunner, jobs: JobManager,
                 ask_conflict: Callable[[FileEntry, FileEntry], Tuple[str, bool]],
                 report_errors: Callable[[str, List[str]], None],
                 publish_changed: Callable[[List[str]], None]) -> None:
        self._vfs = vfs
        self._runner = runner
        self._jobs = jobs
        self._ask_conflict = ask_conflict
        self._report_errors = report_errors
        self._publish_changed = publish_changed

    # ==================================================================
    # Copy / move
    # ==================================================================
    def transfer(self, sources: List[str], dest_dir: str, move: bool,
                 on_done: Optional[Callable[[List[str]], None]] = None) -> Job:
        """Copy (or move) ``sources`` into ``dest_dir``. ``on_done(new_paths)``."""
        verb = "Moving" if move else "Copying"
        job = self._jobs.start(f"{verb} {len(sources)} item{'s' if len(sources) != 1 else ''}")
        self._runner.submit(self._transfer_work, job, list(sources), dest_dir, move, pool="ops",
                            on_done=on_done, on_error=lambda exc: None)
        return job

    def _transfer_work(self, job: Job, sources: List[str], dest_dir: str, move: bool) -> List[str]:
        errors: List[str] = []
        created: List[str] = []
        changed = {dest_dir}
        policy = {"choice": None}          # remembered answer when "apply to all" is ticked
        dst_fs = self._vfs.resolve(dest_dir)
        try:
            # ---- phase 1: plan ------------------------------------------
            self._jobs.update(job, None, "Preparing…")
            plan = []                       # (kind, src_fs, src_entry, dst_path)
            total = 0
            for src in sources:
                job.token.check()
                src_fs = self._vfs.resolve(src)
                try:
                    entry = src_fs.stat(src)
                except OSError as exc:
                    errors.append(f"{src}: {exc}")
                    continue
                if entry.is_dir and is_inside(dest_dir, src):
                    errors.append(f"Cannot put '{entry.name}' inside itself")
                    continue
                parent = src_fs.parent(src)
                if parent:
                    changed.add(parent)
                same_folder = parent is not None and src_fs.normalize(parent) == dst_fs.normalize(dest_dir) \
                    and src_fs is dst_fs
                if same_folder and move:
                    continue                                    # moving onto itself: nothing to do
                name = entry.name
                target = dst_fs.join(dest_dir, name)
                if same_folder:                                 # copy into same folder = duplicate
                    target = dst_fs.join(dest_dir, unique_name(dst_fs, dest_dir, name, entry.is_dir))
                else:
                    target = self._resolve_conflict(dst_fs, dest_dir, entry, target, policy)
                    if target is None:
                        continue
                if move and src_fs is dst_fs and src_fs.same_volume(src, dest_dir):
                    plan.append(("rename", src_fs, entry, target))
                    continue
                for kind, s_entry, d_path in self._expand(src_fs, entry, target, dst_fs, job, policy):
                    plan.append((kind, src_fs, s_entry, d_path))
                    if kind == "file":
                        total += s_entry.size
                if move:
                    plan.append(("delete_source", src_fs, entry, None))
                created.append(target)

            # ---- phase 2: do it ------------------------------------------
            done_bytes = 0
            for kind, src_fs, entry, dst in plan:
                job.token.check()
                try:
                    if kind == "rename":
                        if dst_fs.exists(dst):
                            dst_fs.remove_tree(dst)          # user already chose "overwrite"
                        src_fs.rename(entry.path, dst)
                        created.append(dst)
                    elif kind == "mkdir":
                        if not dst_fs.exists(dst):
                            dst_fs.mkdir(dst)
                    elif kind == "file":
                        self._jobs.update(job, done_bytes / total if total else None,
                                          f"{entry.name}  ({human_size(done_bytes)} of {human_size(total)})")
                        done_bytes = self._copy_file(src_fs, entry, dst_fs, dst, job, done_bytes, total)
                    elif kind == "delete_source":
                        src_fs.remove_tree(entry.path, job.token)
                except Cancelled:
                    raise
                except OSError as exc:
                    errors.append(f"{entry.name}: {exc}")
        except Cancelled:
            errors.append("Cancelled by user")
        finally:
            self._jobs.finish(job, errors[0] if errors else None)
            self._publish_changed(sorted(changed))
            real_errors = [e for e in errors if e != "Cancelled by user"]
            if real_errors:
                self._report_errors("Some items could not be " + ("moved" if move else "copied"), real_errors)
        return created

    def _resolve_conflict(self, dst_fs, dest_dir: str, entry: FileEntry, target: str, policy: dict):
        """Returns the target path to use, or None to skip. May raise Cancelled."""
        try:
            existing = dst_fs.stat(target)
        except OSError:
            return target                                 # no conflict
        if existing.is_dir and entry.is_dir:
            return target                                 # folders merge; files inside may conflict
        choice = policy["choice"]
        if choice is None:
            choice, apply_all = self._ask_conflict(entry, existing)
            if apply_all:
                policy["choice"] = choice
        if choice == Conflict.CANCEL:
            raise Cancelled()
        if choice == Conflict.SKIP:
            return None
        if choice == Conflict.RENAME:
            return dst_fs.join(dest_dir, unique_name(dst_fs, dest_dir, entry.name, entry.is_dir))
        return target                                     # overwrite

    def _expand(self, src_fs, entry: FileEntry, target: str, dst_fs, job: Job, policy: dict):
        """Turn one source into a flat list of (kind, entry, dst) steps (recursive)."""
        if not entry.is_dir or entry.is_link:
            yield ("file", entry, target)
            return
        yield ("mkdir", entry, target)
        for child in src_fs.list_dir(entry.path):
            job.token.check()
            child_target = dst_fs.join(target, child.name)
            if not child.is_dir:
                child_target = self._resolve_conflict(dst_fs, target, child, child_target, policy) \
                    if dst_fs.exists(target) else child_target
                if child_target is None:
                    continue
            yield from self._expand(src_fs, child, child_target, dst_fs, job, policy)

    def _copy_file(self, src_fs, entry: FileEntry, dst_fs, dst: str, job: Job, done: int, total: int) -> int:
        if src_fs.is_local and dst_fs.is_local and entry.size < FAST_LOCAL_COPY_LIMIT:
            shutil.copy2(entry.path, dst)
            return done + entry.size
        try:
            with src_fs.open_read(entry.path) as reader, dst_fs.open_write(dst) as writer:
                while True:
                    job.token.check()
                    chunk = reader.read(CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    done += len(chunk)
                    self._jobs.update(job, done / total if total else None,
                                      f"{entry.name}  ({human_size(done)} of {human_size(total)})")
        except BaseException:
            try:
                dst_fs.remove_file(dst)       # don't leave half-copied files around
            except OSError:
                pass
            raise
        if entry.mtime:
            dst_fs.set_mtime(dst, entry.mtime)
        if src_fs.is_local and dst_fs.is_local:
            try:
                shutil.copymode(entry.path, dst)
            except OSError:
                pass
        return done

    # ==================================================================
    # Delete
    # ==================================================================
    def delete(self, paths: List[str], permanent: bool, on_done: Optional[Callable] = None) -> Job:
        what = "Deleting" if permanent else "Moving to trash"
        job = self._jobs.start(f"{what} {len(paths)} item{'s' if len(paths) != 1 else ''}")

        def work() -> int:
            errors: List[str] = []
            changed = set()
            count = 0
            try:
                for i, path in enumerate(paths):
                    job.token.check()
                    fs = self._vfs.resolve(path)
                    self._jobs.update(job, i / max(1, len(paths)), fs.basename(path))
                    parent = fs.parent(path)
                    if parent:
                        changed.add(parent)
                    try:
                        if permanent or not fs.can_trash:
                            fs.remove_tree(path, job.token)
                        else:
                            move_to_trash(path)
                        count += 1
                    except Cancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001  (trash helpers raise various types)
                        errors.append(f"{fs.basename(path)}: {exc}")
            except Cancelled:
                pass
            finally:
                self._jobs.finish(job, errors[0] if errors else None)
                self._publish_changed(sorted(changed))
                if errors:
                    self._report_errors("Some items could not be deleted", errors)
            return count

        self._runner.submit(work, pool="ops", on_done=on_done, on_error=lambda exc: None)
        return job

    # ==================================================================
    # Small, quick operations
    # ==================================================================
    def _quick(self, fn: Callable[[], str], folder: str, on_done, on_error) -> None:
        def work() -> str:
            result = fn()
            self._publish_changed([folder])
            return result
        self._runner.submit(work, pool="ops", on_done=on_done, on_error=on_error)

    def rename(self, path: str, new_name: str, on_done=None, on_error=None) -> None:
        fs = self._vfs.resolve(path)
        parent = fs.parent(path) or path

        def work() -> str:
            if "/" in new_name or (fs.is_local and os.sep in new_name):
                raise OSError("Names cannot contain path separators")
            target = fs.join(parent, new_name)
            if fs.exists(target) and fs.normalize(target).lower() != fs.normalize(path).lower():
                raise FileExistsError(f"'{new_name}' already exists")
            fs.rename(path, target)
            return target
        self._quick(work, parent, on_done, on_error)

    def make_folder(self, folder: str, name: str, on_done=None, on_error=None) -> None:
        fs = self._vfs.resolve(folder)

        def work() -> str:
            target = fs.join(folder, unique_name(fs, folder, name, True))
            fs.mkdir(target)
            return target
        self._quick(work, folder, on_done, on_error)

    def make_file(self, folder: str, name: str, on_done=None, on_error=None) -> None:
        fs = self._vfs.resolve(folder)

        def work() -> str:
            target = fs.join(folder, unique_name(fs, folder, name, False))
            with fs.open_write(target):
                pass
            return target
        self._quick(work, folder, on_done, on_error)

    def download_to_temp(self, uri: str, on_done: Callable[[str], None]) -> Job:
        """Fetch a remote file to a temp folder (so it can be opened in an app)."""
        fs = self._vfs.resolve(uri)
        local_fs = self._vfs.local
        job = self._jobs.start(f"Downloading {fs.basename(uri)}")

        def work() -> str:
            try:
                entry = fs.stat(uri)
                target = os.path.join(temp_download_dir(), entry.name)
                self._copy_file(fs, entry, local_fs, target, job, 0, entry.size)
                return target
            finally:
                self._jobs.finish(job)

        self._runner.submit(work, pool="ops", on_done=on_done,
                            on_error=lambda exc: self._report_errors("Download failed", [str(exc)]))
        return job

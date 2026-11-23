"""
Things that work differently on Windows, macOS and Linux:
opening a file with its default app, opening an external terminal,
and moving files to the recycle bin / trash.

If the optional ``send2trash`` package is installed it is used for the
trash; otherwise we use built-in implementations for each OS.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from ..core.platform_info import IS_MAC, IS_WINDOWS

try:
    from send2trash import send2trash as _send2trash
except ImportError:
    _send2trash = None


def _spawn(args, cwd: Optional[str] = None) -> None:
    """Start a program without waiting for it and without inheriting our console."""
    kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


def open_path(path: str) -> None:
    """Open a file or folder with the operating system's default application."""
    if IS_WINDOWS:
        os.startfile(path)                 # noqa: S606 - this is exactly what we want
    elif IS_MAC:
        _spawn(["open", path])
    else:
        _spawn(["xdg-open", path])


def launch(argv: List[str]) -> None:
    """Start a program with arguments (detached), e.g. ['mpv', '--', 'http://...']."""
    if not argv:
        raise OSError("Nothing to run")
    if not shutil.which(argv[0]) and not os.path.exists(argv[0]):
        raise OSError(f"Program not found: {argv[0]}")
    _spawn(argv)


def open_with(path: str, command: str) -> None:
    """Open ``path`` with a user-typed command, e.g. 'gimp' or 'code -n'."""
    args = shlex.split(command, posix=not IS_WINDOWS)
    if IS_MAC and len(args) == 1 and not shutil.which(args[0]):
        args = ["open", "-a", args[0]]     # "Preview" -> open -a Preview
    _spawn(args + [path])


def reveal_in_system(path: str) -> None:
    """Show the file in the OS's own file manager."""
    if IS_WINDOWS:
        _spawn(["explorer", "/select,", path])
    elif IS_MAC:
        _spawn(["open", "-R", path])
    else:
        _spawn(["xdg-open", os.path.dirname(path) or path])


def _linux_terminals(directory: str) -> List[List[str]]:
    return [
        ["x-terminal-emulator"],                                  # Debian/Ubuntu default
        ["konsole", "--workdir", directory],
        ["gnome-terminal", f"--working-directory={directory}"],
        ["xfce4-terminal", f"--working-directory={directory}"],
        ["kgx", f"--working-directory={directory}"],
        ["tilix", "-w", directory],
        ["alacritty", "--working-directory", directory],
        ["kitty", "--directory", directory],
        ["wezterm", "start", "--cwd", directory],
        ["foot", f"--working-directory={directory}"],
        ["xterm"],
    ]


def open_external_terminal(directory: str, preferred: str = "") -> None:
    """Open the system's terminal window in ``directory``."""
    if preferred:
        _spawn(shlex.split(preferred, posix=not IS_WINDOWS), cwd=directory)
        return
    if IS_WINDOWS:
        if shutil.which("wt"):
            _spawn(["wt", "-d", directory])
        else:
            subprocess.Popen(["cmd.exe", "/K"], cwd=directory,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
        return
    if IS_MAC:
        _spawn(["open", "-a", "Terminal", directory])
        return
    for args in _linux_terminals(directory):
        if shutil.which(args[0]):
            _spawn(args, cwd=directory)
            return
    raise OSError("No terminal emulator found")


# -- trash -----------------------------------------------------------------------

def _trash_windows(path: str) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"            # must be double-NUL terminated
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"Could not move to Recycle Bin (error {result})")


def _trash_freedesktop(path: str) -> None:
    """The freedesktop.org Trash spec: move to ~/.local/share/Trash/files and
    write a .trashinfo file so the desktop's trash can restore it."""
    trash = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash"
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(path.rstrip("/"))
    target, n = name, 1
    while (files / target).exists() or (info / f"{target}.trashinfo").exists():
        n += 1
        target = f"{name}.{n}"
    (info / f"{target}.trashinfo").write_text(
        "[Trash Info]\nPath={}\nDeletionDate={}\n".format(
            quote(os.path.abspath(path)), datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
        encoding="utf-8")
    try:
        os.rename(path, files / target)      # only works on the same disk
    except OSError:
        (info / f"{target}.trashinfo").unlink(missing_ok=True)
        raise


def move_to_trash(path: str) -> None:
    if _send2trash is not None:
        _send2trash(path)
        return
    if IS_WINDOWS:
        _trash_windows(path)
    elif IS_MAC:
        script = f'tell application "Finder" to delete POSIX file "{os.path.abspath(path)}"'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=30)
    else:
        if shutil.which("gio"):
            result = subprocess.run(["gio", "trash", path], capture_output=True, timeout=60)
            if result.returncode == 0:
                return
        _trash_freedesktop(path)


def temp_download_dir() -> str:
    """Where remote files are downloaded to before opening them."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "pyexplorer-remote", str(int(time.time()) // 86400))
    os.makedirs(path, exist_ok=True)
    return path

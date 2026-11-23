"""
Terminal back-ends: something that runs a shell and streams its output.

The F4 pane (ui/terminal_widget.py) draws the screen with the ``pyte``
terminal emulator, so programs that move the cursor or use colours
(top, htop, nano, vim, git log...) display properly. This module only
starts the shell and moves bytes in and out. Three back-ends share one
interface:

* PtyBackend (Linux, macOS, BSD): your real shell (bash, zsh, fish...) in a
  pseudo-terminal - exactly like a normal terminal window.
* WinPtyBackend (Windows, needs ``pip install pywinpty``): cmd.exe or
  PowerShell in a real Windows console (ConPTY), so it behaves like
  Windows Terminal.
* SimpleShellBackend (fallback): each line you type runs as a separate
  command; ``cd`` is handled by us. ``line_mode = True`` tells the pane to
  do its own line editing and echo.

Callbacks ``on_output(text)`` and ``on_exit(code)`` are called from a
background READER THREAD - the pane hands them to the UI thread.
"""
from __future__ import annotations

import codecs
import os
import shlex
import subprocess
import threading
from typing import Callable, Optional

from ..core.platform_info import IS_WINDOWS


class TerminalBackend:
    line_mode = False      # True: the pane collects a whole line before sending
    name = "terminal"

    def __init__(self, on_output: Callable[[str], None], on_exit: Callable[[int], None],
                 shell: str = "") -> None:
        self._on_output = on_output
        self._on_exit = on_exit
        self.shell = shell

    def start(self, cwd: str, cols: int, rows: int) -> None: ...
    def write(self, data: str) -> None: ...
    def change_directory(self, path: str) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def close(self) -> None: ...

    @property
    def alive(self) -> bool:
        return False


# ---------------------------------------------------------------------------
class PtyBackend(TerminalBackend):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._proc: Optional[subprocess.Popen] = None
        self._master: Optional[int] = None

    def start(self, cwd: str, cols: int = 100, rows: int = 24) -> None:
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        shell = self.shell or os.environ.get("SHELL") or "/bin/bash"
        self.name = os.path.basename(shlex.split(shell)[0])
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        env.pop("VTE_VERSION", None)          # we are not a VTE terminal; avoids odd prompt hooks

        def make_controlling_tty() -> None:     # runs in the child just before the shell starts
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self._master = master
        self.resize(cols, rows)
        self._proc = subprocess.Popen(shlex.split(shell), stdin=slave, stdout=slave, stderr=slave,
                                      cwd=cwd, env=env, preexec_fn=make_controlling_tty, close_fds=True)
        os.close(slave)
        threading.Thread(target=self._reader, name="px-pty", daemon=True).start()

    def _reader(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                data = os.read(self._master, 65536)
            except OSError:
                break
            if not data:
                break
            self._on_output(decoder.decode(data))
        code = self._proc.wait() if self._proc else 0
        self._on_exit(code)

    def write(self, data: str) -> None:
        if self._master is not None:
            try:
                os.write(self._master, data.encode("utf-8"))
            except OSError:
                pass

    def change_directory(self, path: str) -> None:
        # Ctrl+E Ctrl+U clears anything half-typed. The leading space keeps the
        # command out of bash/zsh history (with the common default settings).
        self.write("\x05\x15 cd " + shlex.quote(path) + "\r")

    def resize(self, cols: int, rows: int) -> None:
        if self._master is None:
            return
        import fcntl
        import struct
        import termios
        try:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


# ---------------------------------------------------------------------------
class WinPtyBackend(TerminalBackend):
    """Windows console via pywinpty (ConPTY). Untested outside Windows."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._proc = None

    def _is_powershell(self) -> bool:
        return "powershell" in self.name.lower() or "pwsh" in self.name.lower()

    def start(self, cwd: str, cols: int = 100, rows: int = 24) -> None:
        from winpty import PtyProcess   # only exists on Windows (pip install pywinpty)
        shell = self.shell or os.environ.get("COMSPEC", "cmd.exe")
        self.name = os.path.basename(shell.split()[0])
        self._proc = PtyProcess.spawn(shell, cwd=cwd, dimensions=(rows, cols))
        threading.Thread(target=self._reader, name="px-winpty", daemon=True).start()

    def _reader(self) -> None:
        while True:
            try:
                data = self._proc.read(65536)
            except (EOFError, OSError):
                break
            if data:
                self._on_output(data)
            elif not self._proc.isalive():
                break
        self._on_exit(self._proc.exitstatus or 0)

    def write(self, data: str) -> None:
        if self._proc is not None:
            try:
                self._proc.write(data)
            except (EOFError, OSError):
                pass

    def change_directory(self, path: str) -> None:
        if self._is_powershell():
            self.write("\x1b Set-Location -LiteralPath '" + path.replace("'", "''") + "'\r")
        else:
            self.write("\x1b cd /d \"" + path + "\"\r")      # Esc clears the line in cmd.exe

    def resize(self, cols: int, rows: int) -> None:
        if self._proc is not None:
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.terminate(force=True)
            except Exception:  # noqa: BLE001
                pass

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()


# ---------------------------------------------------------------------------
class SimpleShellBackend(TerminalBackend):
    line_mode = True

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.cwd = os.path.expanduser("~")
        self._proc: Optional[subprocess.Popen] = None
        self._started = False
        self._encoding = "oem" if IS_WINDOWS else "utf-8"
        try:
            codecs.lookup(self._encoding)
        except LookupError:
            self._encoding = "utf-8"

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _prompt(self) -> None:
        self._on_output(f"\x1b[1m{self.cwd}>\x1b[0m ")

    def start(self, cwd: str, cols: int = 100, rows: int = 24) -> None:
        self.cwd = cwd
        self._started = True
        self.name = self.shell or ("cmd.exe" if IS_WINDOWS else "sh")
        tip = "  (pip install pywinpty for a full terminal)" if IS_WINDOWS else ""
        self._on_output(f"Simple shell: each line runs as its own command.{tip}\r\n")
        self._prompt()

    def write(self, data: str) -> None:
        """Receives a whole line (the pane does the editing)."""
        line = data.strip()
        if self.busy:
            return
        if not line:
            self._prompt()
            return
        lower = line.lower()
        if lower in ("cls", "clear"):
            self._on_output("\x1b[2J\x1b[H")
            self._prompt()
            return
        if lower == "cd" or lower.startswith(("cd ", "cd\\", "cd/", "chdir ")):
            target = line.split(None, 1)[1] if " " in line else os.path.expanduser("~")
            if target.lower().startswith("/d "):
                target = target[3:]
            target = os.path.normpath(os.path.join(self.cwd, os.path.expanduser(target.strip('"'))))
            if os.path.isdir(target):
                self.cwd = target
            else:
                self._on_output(f"No such folder: {target}\r\n")
            self._prompt()
            return
        threading.Thread(target=self._run, args=(line,), name="px-shell", daemon=True).start()

    def _run(self, line: str) -> None:
        kwargs = {"cwd": self.cwd, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                  "stdin": subprocess.DEVNULL}
        if IS_WINDOWS:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        shell_lower = (self.shell or "").lower()
        if "powershell" in shell_lower or "pwsh" in shell_lower:
            args, use_shell = [self.shell, "-NoProfile", "-Command", line], False
        else:
            args, use_shell = line, True
        try:
            self._proc = subprocess.Popen(args, shell=use_shell, **kwargs)
            decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
            while True:
                chunk = self._proc.stdout.read1(4096)
                if not chunk:
                    break
                self._on_output(decoder.decode(chunk).replace("\r\n", "\n").replace("\n", "\r\n"))
            self._proc.wait()
        except OSError as exc:
            self._on_output(f"{exc}\r\n")
        finally:
            self._proc = None
            self._prompt()

    def interrupt(self) -> None:
        if self.busy:
            self._proc.terminate()
            self._on_output("^C\r\n")

    def change_directory(self, path: str) -> None:
        self.cwd = path
        if self._started and not self.busy:
            self._on_output("\r\x1b[K")
            self._prompt()

    def close(self) -> None:
        self.interrupt()

    @property
    def alive(self) -> bool:
        return self._started


def create_backend(on_output, on_exit, shell: str = "") -> TerminalBackend:
    """The best back-end available on this computer."""
    if IS_WINDOWS:
        try:
            import winpty  # noqa: F401
            return WinPtyBackend(on_output, on_exit, shell)
        except ImportError:
            return SimpleShellBackend(on_output, on_exit, shell)
    try:
        import fcntl  # noqa: F401
        import pty  # noqa: F401
        return PtyBackend(on_output, on_exit, shell)
    except ImportError:
        return SimpleShellBackend(on_output, on_exit, shell)

"""
Taskbar / dock progress - the shading on PyExplorer's taskbar button while
files are being copied.

    bar = create_taskbar_progress(main_window)
    bar.set(0.42)            # 42 %
    bar.set(0.42, paused=True) / bar.set(0.42, error=True)
    bar.set(None)            # hide

Back-ends (each is self-contained; on Linux both run, since desktops differ):
  * Linux: the "Unity LauncherEntry" D-Bus signal, understood by KDE Plasma's
    task manager, GNOME's Dash to Dock / Ubuntu Dock, Plank, Latte... It is
    matched to the app by its .desktop file name ("pyexplorer.desktop"), so
    the app must be installed with one.
  * Linux on X11: the XApp progress hint (_NET_WM_XAPP_PROGRESS window
    property) shown by Cinnamon's window list, and by MATE / Xfce with XApp.
    Set on the windows themselves: no .desktop file needed.
  * Windows: ITaskbarList3 (the green/yellow/red bar on the taskbar button),
    called through ctypes - no extra packages.
  * Anything else (macOS, desktops without support): does nothing.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

log = logging.getLogger(__name__)


class TaskbarProgress:
    """Does nothing: desktops without a progress indicator."""
    name = "none"

    def set(self, progress: Optional[float], paused: bool = False, error: bool = False) -> None:
        pass


class UnityLauncherProgress(TaskbarProgress):
    name = "unity-launcher"
    PATH = "/org/pyexplorer/LauncherEntry"
    IFACE = "com.canonical.Unity.LauncherEntry"

    def __init__(self, desktop_id: str) -> None:
        from PySide6.QtDBus import QDBusConnection
        # (not kept in an attribute: a QDBusConnection outliving the QApplication crashes on exit)
        if not QDBusConnection.sessionBus().isConnected():
            raise RuntimeError("no D-Bus session bus")
        self._app_uri = f"application://{desktop_id}"
        self._last = None

    def set(self, progress: Optional[float], paused: bool = False, error: bool = False) -> None:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage
        state = None if progress is None else (round(progress, 3), error)
        if state == self._last:
            return
        self._last = state
        props = {"progress-visible": progress is not None, "urgent": bool(error)}
        if progress is not None:
            props["progress"] = float(max(0.0, min(1.0, progress)))
        msg = QDBusMessage.createSignal(self.PATH, self.IFACE, "Update")
        msg.setArguments([self._app_uri, props])
        QDBusConnection.sessionBus().send(msg)


class XAppProgress(TaskbarProgress):
    """Cinnamon / MATE / Xfce (XApp): a CARDINAL 0..100 property on each window."""
    name = "xapp"

    def __init__(self, windows) -> None:
        import ctypes
        import ctypes.util
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != "xcb":
            raise RuntimeError("not running on X11")
        lib = ctypes.util.find_library("X11")
        if not lib:
            raise RuntimeError("libX11 not found")
        x = ctypes.CDLL(lib)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        x.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        x.XFlush.argtypes = [ctypes.c_void_p]
        self._x = x
        self._ctypes = ctypes
        self._display = x.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("can't open the X display")
        self._progress = x.XInternAtom(self._display, b"_NET_WM_XAPP_PROGRESS", 0)
        self._pulse = x.XInternAtom(self._display, b"_NET_WM_XAPP_PROGRESS_PULSE", 0)
        self._windows = windows                 # callable -> the QWidgets to mark (main + transfer window)
        self._last = None

    def set(self, progress: Optional[float], paused: bool = False, error: bool = False) -> None:
        value = None if progress is None else int(round(max(0.0, min(1.0, progress)) * 100))
        if value == self._last:
            return
        self._last = value
        x, c = self._x, self._ctypes
        for widget in self._windows():
            if widget is None or not widget.isVisible():
                continue
            xid = int(widget.winId())
            if value is None:
                x.XDeleteProperty(self._display, xid, self._progress)
                x.XDeleteProperty(self._display, xid, self._pulse)
            else:
                data = (c.c_long * 1)(value)
                x.XChangeProperty(self._display, xid, self._progress, 6, 32, 0, data, 1)    # 6 = XA_CARDINAL
        x.XFlush(self._display)


class MultiProgress(TaskbarProgress):
    """Several back-ends at once (Linux: we can't tell which one the panel listens to)."""

    def __init__(self, parts) -> None:
        self.parts = parts
        self.name = "+".join(p.name for p in parts)

    def set(self, progress: Optional[float], paused: bool = False, error: bool = False) -> None:
        for part in self.parts:
            part.set(progress, paused=paused, error=error)


class WindowsTaskbarProgress(TaskbarProgress):
    name = "windows-taskbar"
    TBPF_NOPROGRESS, TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED = 0, 2, 4, 8

    def __init__(self, hwnd: int) -> None:
        import ctypes
        import uuid
        from ctypes import wintypes
        self._hwnd = hwnd

        class GUID(ctypes.Structure):
            _fields_ = [("data", ctypes.c_ubyte * 16)]

        def guid(text: str) -> GUID:
            return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

        ole32 = ctypes.OleDLL("ole32")
        ole32.CoInitialize(None)
        ptr = ctypes.c_void_p()
        clsid, iid = guid("56FDF344-FD6D-11d0-958A-006097C9A090"), guid("ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf")
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr))
        vtable = ctypes.cast(ptr.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def method(index, *args):
            return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *args)(vtable[index])
        # ITaskbarList: 3 HrInit ... ITaskbarList3: 9 SetProgressValue, 10 SetProgressState
        method(3)(ptr)
        self._ptr = ptr
        self._value = method(9, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)
        self._state = method(10, wintypes.HWND, ctypes.c_int)

    def set(self, progress: Optional[float], paused: bool = False, error: bool = False) -> None:
        try:
            if progress is None:
                self._state(self._ptr, self._hwnd, self.TBPF_NOPROGRESS)
                return
            self._state(self._ptr, self._hwnd,
                        self.TBPF_ERROR if error else self.TBPF_PAUSED if paused else self.TBPF_NORMAL)
            self._value(self._ptr, self._hwnd, int(max(0.0, min(1.0, progress)) * 1000), 1000)
        except OSError:
            pass


def create_taskbar_progress(window, desktop_id: str = "pyexplorer.desktop", windows=None) -> TaskbarProgress:
    """``windows()`` -> the top-level widgets to mark where the desktop wants it per window
    (default: just ``window``)."""
    windows = windows or (lambda: [window])
    if sys.platform.startswith("win"):
        try:
            return WindowsTaskbarProgress(int(window.winId()))
        except Exception as exc:  # noqa: BLE001  (no COM: just no taskbar progress)
            log.info("Taskbar progress not available: %s", exc)
        return TaskbarProgress()
    parts = []
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        for make in (lambda: UnityLauncherProgress(desktop_id), lambda: XAppProgress(windows)):
            try:
                parts.append(make())
            except Exception as exc:  # noqa: BLE001
                log.info("Taskbar progress back-end not available: %s", exc)
    if not parts:
        return TaskbarProgress()
    return parts[0] if len(parts) == 1 else MultiProgress(parts)

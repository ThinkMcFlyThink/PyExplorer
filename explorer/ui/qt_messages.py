"""
qt_messages - Qt's own warnings go through PyExplorer's log.

Qt prints its warnings straight to the terminal. We route them into Python's
``logging`` instead, so they also land in pyexplorer.log (useful when
reporting a problem). Each one is logged as "qt.<category>", e.g.
"qt.qpa.services".

A few messages are known to be harmless noise. They are logged at DEBUG level
(not shown in the terminal, still easy to turn on) instead of as warnings:

  * "Failed to register with host portal ... Connection already associated with
    an application ID" - at startup Qt tells the desktop portal (the Flatpak-era
    service desktops use for file pickers, dark-mode settings...) which app this
    is. On Linux Mint the portal has usually already linked the connection to
    the app by then, so the second registration is refused. Nothing depends on
    it: PyExplorer opens files and folders itself.

To add another harmless message, add a piece of its text to HARMLESS.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

HARMLESS = (
    "Failed to register with host portal",
)

_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def handle(kind, context, text: str) -> None:
    """Qt calls this for every message it would have printed."""
    category = getattr(context, "category", None) or "default"
    if isinstance(category, bytes):
        category = category.decode("utf-8", "replace")
    logger = logging.getLogger(f"qt.{category}")
    if any(piece in text for piece in HARMLESS):
        logger.debug("%s (harmless, see explorer/ui/qt_messages.py)", text)
        return
    logger.log(_LEVELS.get(kind, logging.WARNING), "%s", text)


def install() -> None:
    """Call once, before the QApplication is created (Qt may complain while starting)."""
    qInstallMessageHandler(handle)

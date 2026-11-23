"""
The SYSTEM clipboard for files, so copy/cut/paste works between PyExplorer
and other file managers (Explorer, Finder, Dolphin, Nautilus...).

We write the file list in every common format:
  * text/uri-list (file:// URLs)          - understood everywhere
  * x-special/gnome-copied-files          - GNOME/Nautilus/Thunar: "copy" or "cut"
  * application/x-kde-cutselection        - KDE/Dolphin: "1" means cut
  * Preferred DropEffect                  - Windows Explorer: 2 = move, 5 = copy
and read them back the same way.
"""
from __future__ import annotations

import struct
from typing import List, Tuple

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QGuiApplication

GNOME = "x-special/gnome-copied-files"
KDE_CUT = "application/x-kde-cutselection"
WIN_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'


def _url(path: str) -> QUrl:
    return QUrl(path) if "://" in path else QUrl.fromLocalFile(path)


def put_files(paths: List[str], cut: bool) -> None:
    mime = QMimeData()
    urls = [_url(p) for p in paths]
    mime.setUrls(urls)
    mime.setText("\n".join(paths))
    mime.setData(GNOME, ("cut" if cut else "copy").encode() + b"".join(
        b"\n" + bytes(u.toEncoded()) for u in urls))
    mime.setData(KDE_CUT, b"1" if cut else b"0")
    mime.setData(WIN_EFFECT, struct.pack("<I", 2 if cut else 5))
    QGuiApplication.clipboard().setMimeData(mime)


def put_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def get_files() -> Tuple[List[str], bool]:
    """(paths, cut) from the system clipboard; ([], False) if it holds no files."""
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None:
        return [], False
    paths: List[str] = []
    cut = False
    if mime.hasFormat(GNOME):
        lines = bytes(mime.data(GNOME)).decode("utf-8", "replace").splitlines()
        if lines:
            cut = lines[0].strip() == "cut"
            for line in lines[1:]:
                url = QUrl.fromEncoded(line.strip().encode())
                paths.append(url.toLocalFile() if url.isLocalFile() else url.toString())
    if not paths and mime.hasUrls():
        for url in mime.urls():
            paths.append(url.toLocalFile() if url.isLocalFile() else url.toString())
    if not paths:
        return [], False
    if mime.hasFormat(KDE_CUT):
        cut = bytes(mime.data(KDE_CUT)).strip() == b"1"
    elif mime.hasFormat(WIN_EFFECT):
        data = bytes(mime.data(WIN_EFFECT))
        if len(data) >= 4:
            cut = struct.unpack("<I", data[:4])[0] & 2 == 2
    return [p for p in paths if p], cut

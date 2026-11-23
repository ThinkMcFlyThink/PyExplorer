"""
ThumbnailProvider - small previews of images for the Icons view.

Images are decoded and shrunk in background threads with Qt's QImageReader
(which can decode JPEGs directly at a smaller size - very fast). Only the
final, cheap step - turning the QImage into a QIcon - runs on the UI thread.
No extra packages are needed.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap

THUMB_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "ico", "svg", "avif", "heic"}
MAX_CACHE = 800
MAX_FILE_BYTES = 80 * 1024 * 1024        # don't decode giant images

Key = Tuple[str, float, int]


def load_thumbnail(path: str, size: int) -> Optional[QImage]:
    """Worker thread: read and shrink an image. QImage is safe to use off the UI thread."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)                         # respect camera rotation (EXIF)
    original = reader.size()
    if original.isValid() and original.width() > 0:
        reader.setScaledSize(original.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return None
    if image.width() > size or image.height() > size:
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


class ThumbnailProvider:
    def __init__(self, runner) -> None:
        self._runner = runner
        self._cache: "OrderedDict[Key, QIcon]" = OrderedDict()
        self._pending: Dict[Key, List[Callable[[], None]]] = {}
        self._failed = set()
        formats = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
        self.extensions = {e for e in THUMB_EXTS if e in formats or (e == "jpg" and "jpeg" in formats)}

    def get(self, path: str, mtime: float, size: int, file_size: int,
            on_ready: Callable[[], None]) -> Optional[QIcon]:
        """A cached thumbnail, or None while it loads (``on_ready`` is called later)."""
        key = (path, mtime, size)
        icon = self._cache.get(key)
        if icon is not None:
            self._cache.move_to_end(key)
            return icon
        if key in self._failed or file_size > MAX_FILE_BYTES:
            return None
        waiters = self._pending.get(key)
        if waiters is not None:
            waiters.append(on_ready)
            return None
        self._pending[key] = [on_ready]
        self._runner.submit(load_thumbnail, path, size, pool="thumb",
                            on_done=lambda img: self._loaded(key, img),
                            on_error=lambda exc: self._loaded(key, None))
        return None

    def _loaded(self, key: Key, image: Optional[QImage]) -> None:     # UI thread
        callbacks = self._pending.pop(key, [])
        if image is None:
            self._failed.add(key)
            return
        self._cache[key] = QIcon(QPixmap.fromImage(image))
        while len(self._cache) > MAX_CACHE:
            self._cache.popitem(last=False)
        for cb in callbacks:
            cb()

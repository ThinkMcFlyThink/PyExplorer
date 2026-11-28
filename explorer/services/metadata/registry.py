"""
The list of providers, and ``gather()`` - the one function the Info panel
calls (in a worker thread) to learn everything about an item.

Providers are tried from the highest ``priority`` down. The first one that
returns something wins, unless it sets ``final = False`` (then the next
ones may add their sections too - e.g. a .deb's package details, then the
archive listing). Providers that need a missing program are skipped, and
their hint ("Install FFmpeg to...") is shown if nothing else could help.

Register your own with ``register(MyProvider())`` - see base.py.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from ...core.tasks import Cancelled
from .base import Info, InfoProvider, Source
from .general import folder_contents, general

log = logging.getLogger(__name__)

_providers: List[InfoProvider] = []
_lock = threading.Lock()
_builtins_loaded = False


def register(provider: InfoProvider) -> None:
    """Add a provider (replacing one of the same class)."""
    with _lock:
        _providers[:] = [p for p in _providers if type(p) is not type(provider)]
        _providers.append(provider)
        _providers.sort(key=lambda p: -p.priority)


def providers() -> List[InfoProvider]:
    _load_builtins()
    with _lock:
        return list(_providers)


def _load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    with _lock:
        if _builtins_loaded:
            return
        _builtins_loaded = True
    # Imported here, not at the top: nothing is loaded until the panel is first opened.
    from .archives import ArchiveProvider
    from .binary import BinaryProvider
    from .books import BookProvider
    from .documents import OfficeProvider
    from .fonts import FontProvider
    from .media import MediaProvider
    from .text import TextProvider
    from .torrent import TorrentProvider
    for cls in (MediaProvider, BookProvider, OfficeProvider, FontProvider, TorrentProvider, ArchiveProvider,
                BinaryProvider, TextProvider):
        register(cls())


def gather(entry, fs, token=None, options: Optional[Dict[str, Any]] = None) -> Info:
    """Worker thread: the General section, then whatever the providers find.

    ``options``: deep (False = General only, e.g. a cloud placeholder that
    reading would download), video_frames, text_lines."""
    options = options or {}
    src = Source(entry, fs, token, options)
    info = Info(kind=entry.type_label)
    info.sections.append(general(src))
    if entry.is_dir:
        contents = folder_contents(src)
        if contents:
            info.sections.append(contents)
        return info
    if not options.get("deep", True):
        return info
    hints: List[str] = []
    found = False
    for provider in providers():
        if not provider.matches(entry):
            continue
        if provider.local_only and not src.local:
            continue
        if not src.local and entry.size > provider.max_remote_size:
            continue
        if not provider.available():
            hint = provider.missing_hint()
            if hint:
                hints.append(hint)
            continue
        src.check()
        try:
            result = provider.extract(src)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - a damaged file must never break the panel
            log.debug("%s could not read %s", provider.name, entry.path, exc_info=True)
            hints.append(f"Couldn't read the details: {_short(exc)}")
            continue
        if result is None:
            continue
        found = True
        info.kind = result.kind or info.kind
        info.sections.extend(s for s in result.sections if s)
        if result.preview is not None and info.preview is None:
            info.preview = result.preview
        info.note = result.note or info.note
        info.extra.update(result.extra)
        if getattr(provider, "final", True):
            break
    if not found and hints:
        info.note = hints[0]
    if not src.local and not found:
        info.note = info.note or "Details are read for files on this computer (network files show the basics)."
    return info


def _short(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) < 140 else text[:139] + "…"

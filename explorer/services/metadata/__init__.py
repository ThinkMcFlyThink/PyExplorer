"""
File details for the Info panel (Alt+P): one *provider* per family of files.

    base.py        Info / Section (what a provider returns) and InfoProvider
    registry.py    the provider list and gather() - the panel's entry point
    general.py     size, dates, permissions, folder contents (every item)
    media.py       video + audio via ffprobe / mediainfo; poster frames, cover art
    books.py       EPUB, Kindle (MOBI/AZW3), comics (CBZ/CBR/CB7) - with covers
    documents.py   Word / Excel / PowerPoint and OpenDocument
    archives.py    ZIP, TAR, 7z, RAR, ISO... and .deb / .rpm / wheel / JAR packages
    fonts.py       TTF / OTF / WOFF
    torrent.py     .torrent files
    binary.py      ELF / PE / Mach-O programs, AppImages, Java classes
    text.py        text, code, CSV, JSON, subtitles (and sniffing unknown files)

Images and PDFs need Qt, so their providers live in ui/info_providers.py
and register themselves here when the panel opens.

Nothing in this package touches widgets: it all runs in worker threads.
"""
from .base import Info, InfoProvider, Section, Source  # noqa: F401
from .registry import gather, providers, register  # noqa: F401

__all__ = ["Info", "InfoProvider", "Section", "Source", "gather", "providers", "register"]

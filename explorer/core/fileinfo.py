"""
FileEntry - the one object that describes a file or folder everywhere in the
app (views, search results, sidebar, file operations).

It uses ``__slots__`` because a big folder can hold 100,000+ entries and
slots make each object much smaller and faster than a normal class.

Also here: helpers that turn a file extension into a category (image,
video...) and a human "Type" label ("PNG image").
"""
from __future__ import annotations

from typing import Optional

# extension -> category. Categories decide the icon colour.
_CATEGORIES = {
    "image": "png jpg jpeg gif bmp webp tif tiff svg ico heic raw cr2 nef psd xcf kra avif",
    "video": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts",
    "audio": "mp3 flac wav ogg m4a aac wma opus aiff mid midi",
    "archive": "zip rar 7z tar gz bz2 xz zst tgz iso dmg cab deb rpm apk jar",
    "code": "py pyw js ts jsx tsx java c h cpp hpp cs go rs rb php swift kt kts lua sh bash zsh "
            "ps1 bat cmd pl r scala dart html htm css scss sql json yaml yml toml xml ini cfg gradle",
    "document": "pdf doc docx odt rtf xls xlsx ods csv ppt pptx odp epub mobi md tex",
    "text": "txt log nfo",
    "executable": "exe msi appimage bin run com app",
}
EXT_CATEGORY = {ext: cat for cat, exts in _CATEGORIES.items() for ext in exts.split()}

_FRIENDLY_NAMES = {
    "jpg": "JPEG image", "jpeg": "JPEG image", "png": "PNG image", "gif": "GIF image",
    "pdf": "PDF document", "txt": "Text file", "md": "Markdown", "py": "Python script",
    "zip": "ZIP archive", "mp3": "MP3 audio", "mp4": "MP4 video", "mkv": "Matroska video",
    "docx": "Word document", "xlsx": "Excel sheet", "exe": "Application", "epub": "EPUB book",
    "html": "HTML page", "json": "JSON file", "sh": "Shell script",
}


class FileEntry:
    __slots__ = ("name", "path", "parent", "is_dir", "size", "mtime",
                 "is_link", "hidden", "ext", "sk", "nk", "extra", "cloud")

    def __init__(self, name: str, path: str, parent: str, is_dir: bool,
                 size: int = 0, mtime: float = 0.0, is_link: bool = False,
                 hidden: bool = False, extra: Optional[dict] = None) -> None:
        self.name = name            # "photo.jpg"
        self.path = path            # full path or URI: "/home/me/photo.jpg", "sftp://me@host:22/x"
        self.parent = parent        # folder containing it (path/URI)
        self.is_dir = is_dir
        self.size = size            # bytes (0 for folders)
        self.mtime = mtime          # modification time, seconds since epoch
        self.is_link = is_link
        self.hidden = hidden
        # Lower-case extension without the dot. Folders and ".bashrc"-style names have none.
        dot = name.rfind(".")
        self.ext = name[dot + 1:].lower() if (not is_dir and dot > 0) else ""
        self.sk = None              # cached sort key (filled by FolderModel)
        self.nk = None              # cached "natural" name key (see core/sorting.py)
        self.extra = extra          # free-form dict for special entries (drives, placeholders...)
        self.cloud = 0              # cloud state reported by the OS (see core/cloud.py), 0 = none

    @property
    def category(self) -> str:
        if self.is_dir:
            return "folder"
        return EXT_CATEGORY.get(self.ext, "other")

    @property
    def type_label(self) -> str:
        if self.extra and self.extra.get("type_label"):
            return self.extra["type_label"]
        if self.is_dir:
            return "Folder"
        if not self.ext:
            return "File"
        return _FRIENDLY_NAMES.get(self.ext, f"{self.ext.upper()} file")

    def __repr__(self) -> str:
        return f"FileEntry({self.path!r}, dir={self.is_dir})"

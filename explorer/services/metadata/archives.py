"""
Archives and packages: what is inside, how well it is compressed, whether
it is encrypted, and the top-level items.

    ZIP (and JAR, APK, wheels...)  Python's zipfile - no extra programs
    TAR, .tar.gz/.bz2/.xz          Python's tarfile, with a time budget
    .gz                            the original name and size, from the header
    7z, RAR, ISO, DMG, CAB, ...    7-Zip ("7z"), when it is installed
    .deb / .rpm                    dpkg-deb / rpm, when installed (name, version, description...)
"""
from __future__ import annotations

import os
import struct
import tarfile
import time
import zipfile
from typing import Dict, List, Optional, Tuple

from ...core.formatting import human_size
from ...core.sorting import natural_key
from . import tools
from .base import Info, InfoProvider, Source

ZIP_EXTS = {"zip", "jar", "war", "ear", "apk", "aab", "xpi", "whl", "nupkg", "vsix", "ipa", "crx", "kmz", "sketch"}
TAR_EXTS = {"tar", "tgz", "tbz", "tbz2", "txz", "tlz", "taz"}
SEVEN_EXTS = {"7z", "rar", "iso", "dmg", "cab", "msi", "wim", "xar", "arj", "lzh", "lha", "cpio", "rpm", "deb",
              "udf", "vhd", "vhdx", "vmdk", "qcow2", "squashfs", "chm", "zst", "lz4", "lzma", "z"}
STREAM_EXTS = {"gz", "bz2", "xz"}
ZIP_METHODS = {0: "Stored", 8: "Deflate", 9: "Deflate64", 12: "BZip2", 14: "LZMA", 93: "Zstandard", 95: "XZ",
               98: "PPMd", 99: "AES encrypted"}
FORMAT_NAMES = {"7z": "7-Zip", "rar": "RAR", "rar5": "RAR 5", "iso": "ISO disc image", "udf": "UDF disc image",
                "dmg": "Apple disk image", "cab": "Windows cabinet", "wim": "Windows image", "squashfs": "SquashFS",
                "vhd": "Virtual hard disk", "vhdx": "Virtual hard disk", "vmdk": "VMware disk", "qcow": "QEMU disk",
                "msi": "Windows Installer", "chm": "Compiled HTML help", "cpio": "cpio", "xar": "XAR",
                "arj": "ARJ", "lzh": "LZH", "rpm": "RPM", "ar": "ar", "zstd": "Zstandard", "lzma": "LZMA"}
LIST_BUDGET = 2.5         # seconds spent reading a compressed tar before giving a partial answer
TOP_LEVEL_ROWS = 14


def seven_zip() -> Optional[str]:
    return tools.which("7z") or tools.which("7zz") or tools.which("7za")


def is_tar_name(name: str) -> bool:
    low = name.lower()
    return low.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tar.lz", ".tar.z")) or low.rsplit(".", 1)[-1] in TAR_EXTS


class Listing:
    """What an archive holds, whichever tool read it."""

    def __init__(self) -> None:
        self.format = ""
        self.files = 0
        self.folders = 0
        self.size = 0               # uncompressed
        self.packed = 0             # compressed (0 = unknown)
        self.methods: Dict[str, int] = {}
        self.encrypted = 0          # entries that need a password
        self.headers_encrypted = False
        self.complete = True
        self.comment = ""
        self.solid = None
        self.top: Dict[str, List] = {}  # top-level name -> [is_dir, size, item count]
        self.names: List[str] = []      # every file name (comics use it to find pages)
        self._dirs = set()              # folders implied by file paths ("a/b.txt" -> "a")

    def add(self, name: str, is_dir: bool, size: int = 0, packed: int = 0, method: str = "",
            encrypted: bool = False) -> None:
        name = name.replace("\\", "/")
        while name.startswith("./"):
            name = name[2:]
        name = name.lstrip("/")
        if not name or name == ".":
            return
        if is_dir:
            self.folders += 1
            self._dirs.add(name.rstrip("/"))
        else:
            self.files += 1
            self.size += size
            self.packed += packed
            self.names.append(name)
            if method:
                self.methods[method] = self.methods.get(method, 0) + 1
            if encrypted:
                self.encrypted += 1
        parts = name.rstrip("/").split("/")
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))
        top = self.top.setdefault(parts[0], [is_dir or len(parts) > 1, 0, 0])
        if len(parts) > 1:
            top[0] = True
            top[2] += 0 if is_dir else 1
        top[1] += 0 if is_dir else size

    def to_info(self, info: Info, physical_size: int) -> Info:
        s = info.section("Archive")
        s.add("Format", self.format)
        more = "" if self.complete else "+"
        folders = max(self.folders, len(self._dirs))
        s.add("Contents", f"{_plural(self.files, 'file', more)}, {_plural(folders, 'folder', more)}")
        if self.size:
            s.add("Unpacked size", ("at least " if not self.complete else "") + human_size(self.size))
            packed = self.packed or (physical_size if self.complete else 0)
            if packed and self.size:
                saved = 1 - packed / self.size
                s.add("Compression", f"{human_size(packed)} packed" +
                      (f" — saves {saved:.0%}" if saved > 0.005 else " — not compressed"))
        if self.methods:
            s.add("Method", ", ".join(m for m, _ in sorted(self.methods.items(), key=lambda kv: -kv[1])[:3]))
        if self.solid is not None:
            s.add("Solid", "Yes" if self.solid else "No")
        if self.headers_encrypted:
            s.add("Encrypted", "Yes — file names are hidden too")
        elif self.encrypted:
            s.add("Encrypted", "Every file" if self.encrypted == self.files else f"{self.encrypted:,} files")
        s.add("Comment", tools.clip(self.comment, 300))
        if self.top:
            names = sorted(self.top, key=lambda n: (not self.top[n][0], natural_key(n)))
            t = info.section(f"Top level ({len(names):,})", key="Top level")
            for name in names[:TOP_LEVEL_ROWS]:
                is_dir, size, count = self.top[name]
                if is_dir:
                    t.add(name + "/", f"{count:,} files, {human_size(size)}" if count else "folder")
                else:
                    t.add(name, human_size(size))
            if len(names) > TOP_LEVEL_ROWS:
                t.add("…", f"{len(names) - TOP_LEVEL_ROWS:,} more")
        return info


# -- readers ----------------------------------------------------------------------------
def read_zip(src: Source) -> Tuple[Listing, zipfile.ZipFile]:
    fh = src.seekable()
    zf = zipfile.ZipFile(fh)
    out = Listing()
    out.format = "ZIP"
    for n, zi in enumerate(zf.infolist()):
        if n % 2000 == 0:
            src.check()
        method = ZIP_METHODS.get(zi.compress_type, f"method {zi.compress_type}")
        out.add(zi.filename, zi.is_dir(), zi.file_size, zi.compress_size, method,
                bool(zi.flag_bits & 0x1) or zi.compress_type == 99)
    try:
        out.comment = zf.comment.decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        pass
    return out, zf


def read_tar(src: Source) -> Listing:
    out = Listing()
    low = src.entry.name.lower()
    out.format = "TAR" + next((f" + {c}" for e, c in ((".gz", "gzip"), (".tgz", "gzip"), (".bz2", "bzip2"),
                                                       (".tbz", "bzip2"), (".tbz2", "bzip2"), (".xz", "XZ"),
                                                       (".txz", "XZ")) if low.endswith(e)), "")
    start = time.monotonic()
    with src.open() as fh, tarfile.open(fileobj=fh, mode="r|*") as tf:
        for n, member in enumerate(tf):
            if n % 200 == 0:
                src.check()
                if time.monotonic() - start > LIST_BUDGET:
                    out.complete = False
                    break
            out.add(member.name, member.isdir(), member.size if member.isfile() else 0)
    return out


def read_gzip(src: Source) -> Info:
    """A single gzip-compressed file: its original name and size are in the header/trailer."""
    info = Info(kind="gzip-compressed file")
    s = info.section("Archive")
    s.add("Format", "gzip")
    with src.open() as fh:
        head = fh.read(4096)
        if len(head) < 18 or head[:2] != b"\x1f\x8b":
            return info
        flags = head[3]
        mtime = struct.unpack("<I", head[4:8])[0]
        pos = 10
        if flags & 0x04:                                  # FEXTRA
            pos += 2 + struct.unpack("<H", head[pos:pos + 2])[0]
        if flags & 0x08:                                  # FNAME
            end = head.find(b"\0", pos)
            if end > 0:
                s.add("Original name", head[pos:end].decode("latin-1"))
        if src.local and src.entry.size >= 18:
            fh.seek(-4, os.SEEK_END)
            size = struct.unpack("<I", fh.read(4))[0]
            if src.entry.size < 2 ** 32:
                s.add("Unpacked size", human_size(size) + (f" — saves {1 - src.entry.size / size:.0%}"
                                                           if size > src.entry.size else ""))
    if mtime:
        from ...core.formatting import human_time
        s.add("Original date", human_time(mtime))
    return info


def read_7z(path: str, token) -> Listing:
    exe = seven_zip()
    out = Listing()
    try:
        raw = tools.run([exe, "l", "-slt", "-sccUTF-8", "--", path], timeout=20, token=token)
    except OSError as exc:
        if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
            out.headers_encrypted = True
            out.format = "7-Zip" if path.lower().endswith(".7z") else ""
            return out
        raise
    text = raw.decode("utf-8", "replace")
    head, _, body = text.partition("\n----------\n")
    props = dict(_pairs(head.split("\n--\n", 1)[-1]))
    kind = props.get("Type", "")
    out.format = FORMAT_NAMES.get(kind.lower(), kind.upper())
    if props.get("Solid"):
        out.solid = props["Solid"] == "+"
    if "Headers Error" in props or "Encrypted = +" in head:
        out.headers_encrypted = True
    for block in body.split("\n\n"):
        item = dict(_pairs(block))
        if "Path" not in item:
            continue
        is_dir = item.get("Folder") == "+" or "D" in item.get("Attributes", "")[:1]
        out.add(item["Path"], is_dir, _int(item.get("Size")), _int(item.get("Packed Size")),
                item.get("Method", "").split(":")[0].split(" ")[0], item.get("Encrypted") == "+")
    out.comment = props.get("Comment", "")
    return out


def _plural(n: int, word: str, more: str = "") -> str:
    return f"{n:,}{more} {word}" + ("" if n == 1 and not more else "s")


def _pairs(block: str):
    for line in block.splitlines():
        key, sep, value = line.partition(" = ")
        if sep:
            yield key.strip(), value.strip()


def _int(text) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


# -- packages -----------------------------------------------------------------------------
def deb_info(path: str, token) -> Optional[Info]:
    exe = tools.which("dpkg-deb")
    if not exe:
        return None
    raw = tools.run([exe, "--field", path], timeout=10, token=token).decode("utf-8", "replace")
    fields: Dict[str, str] = {}
    key = ""
    for line in raw.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    info = Info(kind="Debian package")
    s = info.section("Package")
    for k in ("Package", "Version", "Architecture", "Maintainer", "Section", "Homepage"):
        s.add(k, fields.get(k))
    if fields.get("Installed-Size", "").isdigit():
        s.add("Installed size", human_size(int(fields["Installed-Size"]) * 1024))
    s.add("Depends", tools.clip(fields.get("Depends", ""), 400))
    desc = fields.get("Description", "")
    if desc:
        s.text = desc.replace("\n.\n", "\n\n").replace("\n.", "\n")
    return info


def rpm_info(path: str, token) -> Optional[Info]:
    exe = tools.which("rpm")
    if not exe:
        return None
    fmt = "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}\\n%{LICENSE}\\n%{URL}\\n%{SIZE}\\n%{PACKAGER}\\n%{SUMMARY}\\n"
    raw = tools.run([exe, "-qp", "--nosignature", "--qf", fmt, path], timeout=10, token=token)
    parts = raw.decode("utf-8", "replace").split("\n")
    if len(parts) < 8:
        return None
    info = Info(kind="RPM package")
    s = info.section("Package")
    for label, value in zip(("Package", "Version", "Architecture", "License", "Homepage"), parts[:5]):
        s.add(label, value if value != "(none)" else "")
    if parts[5].isdigit():
        s.add("Installed size", human_size(int(parts[5])))
    s.add("Packager", parts[6] if parts[6] != "(none)" else "")
    s.add("Summary", parts[7])
    return info


def _zip_package(zf: zipfile.ZipFile, ext: str, info: Info) -> None:
    """Wheels and JARs describe themselves in a text file inside."""
    try:
        if ext == "whl":
            name = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
            if name:
                meta = zf.read(name)[:65536].decode("utf-8", "replace").split("\n\n", 1)[0]
                fields = dict(_header_pairs(meta))
                s = info.section("Package")
                for k in ("Name", "Version", "Summary", "Author", "License", "Requires-Python", "Home-page"):
                    s.add(k.replace("-", " "), fields.get(k))
                info.kind = "Python wheel"
        elif ext in ("jar", "war", "ear"):
            if "META-INF/MANIFEST.MF" in zf.namelist():
                fields = dict(_header_pairs(zf.read("META-INF/MANIFEST.MF")[:65536].decode("utf-8", "replace")))
                s = info.section("Package")
                for k in ("Implementation-Title", "Implementation-Version", "Implementation-Vendor", "Main-Class",
                          "Created-By", "Build-Jdk"):
                    s.add(k.replace("Implementation-", "").replace("-", " "), fields.get(k))
                info.kind = "Java archive"
        elif ext == "apk":
            info.kind = "Android app"
    except (KeyError, OSError, zipfile.BadZipFile):
        pass


def _header_pairs(text: str):
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            yield key.strip(), value.strip()


class ArchiveProvider(InfoProvider):
    name = "Archives"
    exts = frozenset(ZIP_EXTS | TAR_EXTS | SEVEN_EXTS | STREAM_EXTS)
    priority = 40
    local_only = False

    def matches(self, entry) -> bool:
        return not entry.is_dir and (entry.ext in self.exts or is_tar_name(entry.name))

    def missing_hint(self) -> str:
        return "Install 7-Zip to look inside this archive."

    def extract(self, src: Source) -> Optional[Info]:
        ext = src.entry.ext
        if is_tar_name(src.entry.name):
            return read_tar(src).to_info(Info(kind="TAR archive"), src.entry.size)
        if ext in ZIP_EXTS:
            listing, zf = read_zip(src)
            info = Info(kind="ZIP archive")
            _zip_package(zf, ext, info)
            zf.close()
            return listing.to_info(info, src.entry.size)
        if ext == "gz":
            return read_gzip(src)
        package = None
        if src.local and ext == "deb":
            package = deb_info(src.path, src.token)
        elif src.local and ext == "rpm":
            package = rpm_info(src.path, src.token)
        if not src.local or not seven_zip():
            if package is not None:
                return package
            if ext in ("bz2", "xz"):
                info = Info(kind=f"{ext}-compressed file")
                info.section("Archive").add("Format", {"bz2": "bzip2", "xz": "XZ"}[ext])
                return info
            info = Info()
            if src.local:
                info.note = self.missing_hint()
            return info
        listing = read_7z(src.path, src.token)
        if ext in ("deb", "rpm") and package is not None:
            return package                 # the inner data.tar listing says nothing useful
        info = Info(kind=f"{listing.format or ext.upper()} archive")
        return listing.to_info(info, src.entry.size)


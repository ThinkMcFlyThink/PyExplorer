"""
rclone_config - what kind of storage an rclone remote is.

When you run ``rclone mount onedrive: ~/OneDrive``, the system's mount list
only says "fuse.rclone, source onedrive:". To put it in the right place in the
sidebar (Cloud vs Network) and give it the right icon, we look the remote up
in rclone's own config file and read its ``type = ...`` line.

    describe_remote("onedrive:Documents", remotes)  ->  RemoteInfo("Cloud", "OneDrive", "onedrive")

Plain Python, no Qt. Every function takes file paths/data as arguments, so
tests can use made-up config files.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# rclone backend type -> (section, friendly provider name)
# section is "cloud" (internet storage services) or "network" (servers you run / your LAN).
BACKENDS: Dict[str, Tuple[str, str]] = {
    "onedrive": ("cloud", "OneDrive"), "drive": ("cloud", "Google Drive"),
    "gphotos": ("cloud", "Google Photos"), "dropbox": ("cloud", "Dropbox"), "box": ("cloud", "Box"),
    "pcloud": ("cloud", "pCloud"), "mega": ("cloud", "MEGA"), "protondrive": ("cloud", "Proton Drive"),
    "iclouddrive": ("cloud", "iCloud Drive"), "s3": ("cloud", "S3"), "b2": ("cloud", "Backblaze B2"),
    "gcs": ("cloud", "Google Cloud Storage"), "azureblob": ("cloud", "Azure Blob"),
    "azurefiles": ("cloud", "Azure Files"), "jottacloud": ("cloud", "Jottacloud"), "koofr": ("cloud", "Koofr"),
    "yandex": ("cloud", "Yandex Disk"), "mailru": ("cloud", "Mail.ru Cloud"), "zoho": ("cloud", "Zoho WorkDrive"),
    "seafile": ("cloud", "Seafile"), "swift": ("cloud", "Swift"), "storj": ("cloud", "Storj"),
    "hidrive": ("cloud", "HiDrive"), "opendrive": ("cloud", "OpenDrive"), "filen": ("cloud", "Filen"),
    "internxt": ("cloud", "Internxt"), "sharefile": ("cloud", "ShareFile"), "sugarsync": ("cloud", "SugarSync"),
    "pikpak": ("cloud", "PikPak"), "putio": ("cloud", "put.io"), "premiumizeme": ("cloud", "premiumize.me"),
    "sftp": ("network", "SFTP"), "smb": ("network", "SMB"), "ftp": ("network", "FTP"),
    "http": ("network", "HTTP"), "hdfs": ("network", "HDFS"), "webdav": ("network", "WebDAV"),
    "local": ("drives", "Local disk"),
}
# WebDAV servers that are really a cloud service (rclone's "vendor = ..." option).
WEBDAV_VENDORS = {"nextcloud": "Nextcloud", "owncloud": "ownCloud", "sharepoint": "SharePoint",
                  "sharepoint-ntlm": "SharePoint", "fastmail": "Fastmail Files"}
# Remotes that only wrap another remote: follow them to the real storage.
WRAPPERS = {"crypt", "alias", "chunker", "compress", "hasher", "cache", "union", "combine"}
# Last resort for encrypted configs (we can't read the types): guess from the remote's name.
NAME_HINTS = [("onedrive", "onedrive"), ("sharepoint", "onedrive"), ("gdrive", "drive"),
              ("googledrive", "drive"), ("google", "drive"), ("dropbox", "dropbox"), ("pcloud", "pcloud"),
              ("mega", "mega"), ("proton", "protondrive"), ("box", "box"), ("s3", "s3"), ("b2", "b2"),
              ("nextcloud", "webdav:nextcloud"), ("sftp", "sftp"), ("ssh", "sftp"), ("ftp", "ftp"),
              ("smb", "smb"), ("nas", "smb")]


@dataclass
class RemoteInfo:
    section: str          # "cloud" | "network" | "drives"
    provider: str         # "OneDrive", "SFTP"...
    backend: str          # rclone type, "" if unknown


def config_path() -> Optional[str]:
    """Where rclone keeps its config (same search order as rclone itself)."""
    env = os.environ.get("RCLONE_CONFIG")
    if env:
        return env
    candidates = []
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    candidates.append(os.path.join(xdg, "rclone", "rclone.conf"))
    if os.name == "nt":
        candidates.insert(0, os.path.join(os.environ.get("APPDATA", ""), "rclone", "rclone.conf"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".rclone.conf"))
    return next((c for c in candidates if os.path.isfile(c)), candidates[0])


def read_remotes(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """{remote name: its options} from rclone.conf. Empty if missing or encrypted."""
    path = path or config_path()
    if not path:
        return {}
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if text.lstrip().startswith("# Encrypted rclone configuration"):
            return {}
        parser.read_string(text)
    except (OSError, UnicodeDecodeError, configparser.Error):
        return {}
    return {name: dict(parser.items(name)) for name in parser.sections()}


def split_source(source: str) -> Tuple[str, str]:
    """"onedrive:Documents/Work" -> ("onedrive", "Documents/Work").
    On-the-fly remotes like ":sftp,host=x:path" give ("", "sftp")-style backends."""
    if source.startswith(":"):                       # :backend,opt=val:path
        backend = source[1:].split(":", 1)[0].split(",", 1)[0]
        return ":" + backend, source.split(":", 2)[-1] if source.count(":") >= 2 else ""
    name, _, rest = source.partition(":")
    return name, rest


def _backend_of(name: str, remotes: Dict[str, Dict[str, str]], depth: int = 0) -> Tuple[str, Dict[str, str]]:
    """The real backend type of a remote, looking through crypt/alias/... wrappers."""
    if name.startswith(":"):
        return name[1:], {}
    opts = remotes.get(name)
    if opts is None or depth > 8:
        return "", {}
    kind = opts.get("type", "")
    if kind in WRAPPERS:
        inner = opts.get("remote") or (opts.get("upstreams", "").split() or [""])[0]
        inner_name = split_source(inner)[0] if inner else ""      # "gdrive:Photos" -> "gdrive"
        if inner_name:
            return _backend_of(inner_name, remotes, depth + 1)
    return kind, opts


def describe_remote(source: str, remotes: Dict[str, Dict[str, str]]) -> RemoteInfo:
    name, _ = split_source(source)
    backend, opts = _backend_of(name, remotes)
    if not backend:                                  # unknown / encrypted config: guess from the name
        lowered = name.lower().lstrip(":")
        for hint, guess in NAME_HINTS:
            if hint in lowered:
                backend = guess
                break
    if backend.startswith("webdav:"):
        backend, opts = "webdav", {"vendor": backend.split(":", 1)[1]}
    if backend == "webdav" and opts.get("vendor", "").lower() in WEBDAV_VENDORS:
        return RemoteInfo("cloud", WEBDAV_VENDORS[opts["vendor"].lower()], backend)
    if backend in BACKENDS:
        section, provider = BACKENDS[backend]
        return RemoteInfo(section, provider, backend)
    return RemoteInfo("cloud", "rclone", backend)    # most rclone backends are cloud services


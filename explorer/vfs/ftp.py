"""
FTPFileSystem - plain FTP and FTPS (FTP over TLS). Uses Python's built-in
``ftplib`` so it works without installing anything.

One "control" connection is kept for listings; every file transfer opens
its own short-lived connection so a big download never blocks browsing.
"""
from __future__ import annotations

import calendar
import ftplib
import posixpath
import threading
import time
from typing import BinaryIO, Iterator, Optional

from ..core.fileinfo import FileEntry
from .base import RemoteFileSystem
from .sftp import CredentialsProvider


class _TransferStream:
    """File-like wrapper around an FTP data connection."""

    def __init__(self, conn: ftplib.FTP, sock, mode: str) -> None:
        self._conn, self._sock = conn, sock
        self._fh = sock.makefile(mode)

    def read(self, n: int = -1) -> bytes:
        return self._fh.read(n)

    def write(self, data: bytes) -> int:
        self._fh.write(data)
        return len(data)

    def close(self) -> None:
        try:
            self._fh.close()
            self._sock.close()
            self._conn.voidresp()
        finally:
            try:
                self._conn.quit()
            except (OSError, ftplib.Error):
                self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FTPFileSystem(RemoteFileSystem):
    def __init__(self, prefix: str, conn: dict, credentials: CredentialsProvider) -> None:
        super().__init__(prefix, conn)
        self.scheme = conn.get("protocol", "ftp")
        self._credentials = credentials
        self._lock = threading.RLock()
        self._ftp: Optional[ftplib.FTP] = None
        self._mlsd_ok = True

    def _new_connection(self) -> ftplib.FTP:
        tls = self.scheme == "ftps"
        ftp = ftplib.FTP_TLS(timeout=20) if tls else ftplib.FTP(timeout=20)
        ftp.connect(self.conn["host"], int(self.conn.get("port") or 21))
        if not self.conn.get("password"):
            saved = self._credentials.saved_login(self.conn)          # remembered in the keyring?
            if saved:
                self.conn["user"], self.conn["password"] = saved
        user = self.conn.get("user") or "anonymous"
        password = self.conn.get("password") or ""
        try:
            ftp.login(user, password)
        except ftplib.error_perm:
            answer = self._credentials.ask_login(self.conn, "Please log in.")
            if not answer:
                raise OSError("Authentication cancelled")
            user, password = answer[0] or user, answer[1]
            ftp.login(user, password)
            self.conn["user"], self.conn["password"] = user, password
            if len(answer) > 2 and answer[2]:
                self._credentials.save_login(self.conn, user, password)
        if tls:
            ftp.prot_p()        # encrypt file transfers too
        ftp.encoding = "utf-8"
        return ftp

    def _control(self) -> ftplib.FTP:
        if self._ftp is not None:
            try:
                self._ftp.voidcmd("NOOP")
                return self._ftp
            except (OSError, ftplib.Error):
                self._ftp = None
        try:
            self._ftp = self._new_connection()
        except (ftplib.Error, OSError) as exc:
            raise OSError(f"Cannot connect to {self.conn['host']}: {exc}") from exc
        return self._ftp

    # -- listing -----------------------------------------------------------------
    def _list(self, remote: str):
        ftp = self._control()
        results = []
        if self._mlsd_ok:
            try:
                for name, facts in ftp.mlsd(remote, facts=["type", "size", "modify"]):
                    kind = facts.get("type", "file")
                    if kind in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    mtime = 0.0
                    if "modify" in facts:
                        try:
                            mtime = calendar.timegm(time.strptime(facts["modify"][:14], "%Y%m%d%H%M%S"))
                        except ValueError:
                            pass
                    results.append((name, kind == "dir", int(facts.get("size", 0) or 0), float(mtime)))
                return results
            except ftplib.error_perm:
                self._mlsd_ok = False          # old server: fall back to LIST
        lines = []
        ftp.retrlines("LIST " + remote, lines.append)
        for line in lines:                     # unix style: drwxr-xr-x 1 o g 4096 Jan 1 12:00 name
            parts = line.split(None, 8)
            if len(parts) < 9 or parts[8] in (".", ".."):
                continue
            name = parts[8].split(" -> ")[0]
            size = int(parts[4]) if parts[4].isdigit() else 0
            results.append((name, line.startswith("d"), size, 0.0))
        return results

    def list_dir(self, path: str) -> Iterator[FileEntry]:
        remote = self.to_remote(path)
        with self._lock:
            rows = self._list(remote)
        folder_uri = self.to_uri(remote)
        for name, is_dir, size, mtime in rows:
            yield FileEntry(name, self.to_uri(posixpath.join(remote, name)), folder_uri, is_dir,
                            0 if is_dir else size, mtime, False, name.startswith("."))

    def stat(self, path: str) -> FileEntry:
        remote = self.to_remote(path)
        if remote == "/":
            return FileEntry("/", self.to_uri("/"), self.to_uri("/"), True)
        parent = posixpath.dirname(remote)
        name = posixpath.basename(remote)
        for entry in self.list_dir(self.to_uri(parent)):
            if entry.name == name:
                return entry
        raise FileNotFoundError(path)

    # -- changing ----------------------------------------------------------------
    def _cmd(self, method: str, *args):
        with self._lock:
            try:
                return getattr(self._control(), method)(*args)
            except ftplib.Error as exc:
                raise OSError(str(exc)) from exc

    def mkdir(self, path: str) -> None:
        self._cmd("mkd", self.to_remote(path))

    def rename(self, src: str, dst: str) -> None:
        self._cmd("rename", self.to_remote(src), self.to_remote(dst))

    def remove_file(self, path: str) -> None:
        self._cmd("delete", self.to_remote(path))

    def remove_dir(self, path: str) -> None:
        self._cmd("rmd", self.to_remote(path))

    def open_read(self, path: str) -> BinaryIO:
        conn = self._new_connection()
        conn.voidcmd("TYPE I")
        return _TransferStream(conn, conn.transfercmd("RETR " + self.to_remote(path)), "rb")

    def open_write(self, path: str) -> BinaryIO:
        conn = self._new_connection()
        conn.voidcmd("TYPE I")
        return _TransferStream(conn, conn.transfercmd("STOR " + self.to_remote(path)), "wb")

    def close(self) -> None:
        with self._lock:
            if self._ftp is not None:
                try:
                    self._ftp.quit()
                except (OSError, ftplib.Error):
                    pass
            self._ftp = None

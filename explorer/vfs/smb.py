"""
SMBFileSystem - Windows/Samba file shares (TrueNAS, Synology, QNAP, Windows
PCs...) without mounting anything. Needs:  pip install smbprotocol

    smb://truenas.local/            -> the server's shares, shown as folders
    smb://truenas.local/media/Films -> a folder inside the "media" share
    smb://bob@192.168.1.20/backups  -> log in as "bob"

It speaks SMB 2/3 itself (signing and encryption supported), so it works
the same on Linux, Windows and macOS. If the server wants a password and we
don't have one, you are asked - username included.

Internally a URI path "/share/dir/file" becomes the UNC path
\\\\server\\share\\dir\\file that the smbclient library uses.
"""
from __future__ import annotations

import threading
from typing import BinaryIO, Iterator, List, Optional, Tuple

from ..core.fileinfo import FileEntry
from .base import RemoteFileSystem
from .sftp import CredentialsProvider

try:
    import smbclient
    from smbprotocol.exceptions import LogonFailure, SMBAuthenticationError, SMBException
except ImportError:          # optional dependency
    smbclient = None
    LogonFailure = SMBAuthenticationError = SMBException = Exception

FILE_ATTRIBUTE_HIDDEN = 0x2
MISSING = "SMB shares need the 'smbprotocol' package:  pip install smbprotocol"


class SMBFileSystem(RemoteFileSystem):
    scheme = "smb"

    def __init__(self, prefix: str, conn: dict, credentials: CredentialsProvider) -> None:
        super().__init__(prefix, conn)
        self._credentials = credentials
        self.host = conn["host"]
        self.port = int(conn.get("port") or 445)
        self._cache: dict = {}                # this server's own connection pool
        self._login_lock = threading.Lock()
        self._session = None

    # ==================================================================
    # Connecting
    # ==================================================================
    def _kw(self) -> dict:
        return {"username": self.conn.get("user") or None, "password": self.conn.get("password") or None,
                "port": self.port, "connection_cache": self._cache}

    def _login(self):
        """Log in once. Thread-safe. Order: details we already have -> a saved
        login (keyring) -> ask you. A login you ticked "Remember" for is saved
        only after the server accepted it."""
        if smbclient is None:
            raise OSError(MISSING)
        if self._session is not None and self._session.connection.transport.connected:
            return self._session
        with self._login_lock:
            if self._session is not None and self._session.connection.transport.connected:
                return self._session
            from_saved = False
            if not (self.conn.get("user") and self.conn.get("password")):
                saved = self._credentials.saved_login(self.conn)
                if saved:
                    self.conn["user"], self.conn["password"] = saved
                    from_saved = True
            remember = False
            reason = "This server needs a username and password."
            for _attempt in range(4):
                if not self.conn.get("user"):
                    answer = self._ask_login(reason)
                    if answer is None:
                        raise OSError("Login cancelled")
                    remember = answer
                try:
                    self._session = smbclient.register_session(
                        self.host, self.conn.get("user"), self.conn.get("password") or None, port=self.port,
                        connection_cache=self._cache, connection_timeout=15)
                except (LogonFailure, SMBAuthenticationError):
                    self._reset()
                    reason = ("The saved login was rejected - has the password changed?" if from_saved
                              else "Wrong username or password - please try again.")
                    from_saved = False
                    self.conn["password"] = ""
                    answer = self._ask_login(reason)
                    if answer is None:
                        raise OSError("Login cancelled") from None
                    remember = answer
                    continue
                except ValueError as exc:            # smbprotocol reports "can't connect" as ValueError
                    raise OSError(f"Cannot connect to {self.host}: {exc}") from exc
                except SMBException as exc:
                    raise OSError(f"Cannot connect to {self.host}: {exc}") from exc
                if remember:
                    self._credentials.save_login(self.conn, self.conn["user"], self.conn.get("password") or "")
                return self._session
            raise OSError("Too many failed login attempts")

    def _ask_login(self, reason: str) -> Optional[bool]:
        """Ask for a username/password. Returns the "remember" choice, or None if cancelled."""
        answer = self._credentials.ask_login(self.conn, reason)
        if not answer:
            return None
        user, password = answer[0], answer[1]
        remember = bool(answer[2]) if len(answer) > 2 else False
        self.conn["user"], self.conn["password"] = user, password
        if not remember:
            self._credentials.forget_login(self.conn)    # unticked: don't keep an old one around
        return remember

    def _reset(self) -> None:
        self._session = None
        if smbclient is not None:
            try:
                smbclient.reset_connection_cache(connection_cache=self._cache)
            except Exception:  # noqa: BLE001
                pass

    def _call(self, fn, *args, **kwargs):
        """Run an smbclient function, logging in first and turning errors into OSError."""
        self._login()
        try:
            return fn(*args, **kwargs, **self._kw())
        except OSError:
            raise
        except (ValueError, SMBException) as exc:
            raise OSError(str(exc)) from exc

    # ==================================================================
    # Paths
    # ==================================================================
    def _parts(self, path: str) -> List[str]:
        return [p for p in self.to_remote(path).split("/") if p]

    def _unc(self, path: str) -> str:
        parts = self._parts(path)
        return "\\\\" + self.host + ("\\" + "\\".join(parts) if parts else "")

    def same_volume(self, a: str, b: str) -> bool:
        """Renames (fast moves) only work inside one share."""
        if not (a.startswith(self.prefix) and b.startswith(self.prefix)):
            return False
        pa, pb = self._parts(a), self._parts(b)
        return bool(pa and pb and pa[0].lower() == pb[0].lower())

    # ==================================================================
    # Listing
    # ==================================================================
    def list_shares(self) -> List[Tuple[str, str]]:
        from .smb_shares import list_shares
        session = self._login()
        try:
            return list_shares(session, self.host)
        except OSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OSError(f"{self.host} didn't list its shares ({exc}). "
                          f"Type the share name instead, e.g. smb://{self.host}/media") from exc

    def list_dir(self, path: str) -> Iterator[FileEntry]:
        folder_uri = self.to_uri(self.to_remote(path))
        if not self._parts(path):                              # server root: its shares
            for name, comment in self.list_shares():
                label = "Network share" + (f" — {comment}" if comment else "")
                yield FileEntry(name, self.to_uri("/" + name), folder_uri, True,
                                extra={"kind": "share", "type_label": label})
            return
        entries = []
        for de in self._call(smbclient.scandir, self._unc(path)):
            info = de.smb_info
            is_dir = de.is_dir()
            mtime = info.last_write_time.timestamp() if info.last_write_time else 0.0
            hidden = de.name.startswith(".") or bool(info.file_attributes & FILE_ATTRIBUTE_HIDDEN)
            entries.append(FileEntry(de.name, self.join(path, de.name), folder_uri, is_dir,
                                     0 if is_dir else int(info.end_of_file), mtime, False, hidden))
        yield from entries

    def stat(self, path: str) -> FileEntry:
        parts = self._parts(path)
        uri = self.to_uri(self.to_remote(path))
        if not parts:
            return FileEntry(self.host, uri, uri, True, extra={"kind": "network"})
        st = self._call(smbclient.stat, self._unc(path))
        import stat as stat_mod
        is_dir = stat_mod.S_ISDIR(st.st_mode)
        name = parts[-1]
        parent = self.parent(path) or uri
        hidden = name.startswith(".") or bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_HIDDEN)
        return FileEntry(name, uri, parent, is_dir, 0 if is_dir else st.st_size, st.st_mtime, False, hidden)

    # ==================================================================
    # Changing things
    # ==================================================================
    def _need_share(self, path: str) -> None:
        if len(self._parts(path)) < 2:
            raise OSError("Shares themselves can't be changed from here - open a share first")

    def mkdir(self, path: str) -> None:
        self._need_share(path)
        self._call(smbclient.mkdir, self._unc(path))

    def rename(self, src: str, dst: str) -> None:
        self._need_share(src)
        self._need_share(dst)
        self._call(smbclient.rename, self._unc(src), self._unc(dst))

    def remove_file(self, path: str) -> None:
        self._need_share(path)
        self._call(smbclient.remove, self._unc(path))

    def remove_dir(self, path: str) -> None:
        self._need_share(path)
        self._call(smbclient.rmdir, self._unc(path))

    def open_read(self, path: str) -> BinaryIO:
        return self._call(smbclient.open_file, self._unc(path), mode="rb")

    def open_write(self, path: str) -> BinaryIO:
        self._need_share(path)
        return self._call(smbclient.open_file, self._unc(path), mode="wb")

    def set_mtime(self, path: str, mtime: float) -> None:
        try:
            self._call(smbclient.utime, self._unc(path), times=(mtime, mtime))
        except OSError:
            pass

    def disk_usage(self, path: str) -> Optional[Tuple[int, int, int]]:
        parts = self._parts(path)
        if not parts:
            return None
        try:
            v = self._call(smbclient.stat_volume, "\\\\" + self.host + "\\" + parts[0])
        except OSError:
            return None
        return (v.total_size, v.total_size - v.actual_available_size, v.caller_available_size)

    def close(self) -> None:
        self._reset()

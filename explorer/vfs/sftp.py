"""
SFTPFileSystem - browse servers over SSH (needs: pip install paramiko).

Authentication order: ssh-agent / your ~/.ssh keys first, then the saved
password, then we ask you for one. Unknown server keys are shown to you for
confirmation (and remembered in known_hosts inside the config folder) -
we never trust a server silently.
"""
from __future__ import annotations

import posixpath
import stat as stat_mod
import threading
from typing import BinaryIO, Iterator, Optional

from ..core.fileinfo import FileEntry
from ..core.paths import config_file
from .base import RemoteFileSystem

try:
    import paramiko
except ImportError:          # optional dependency
    paramiko = None


class CredentialsProvider:
    """Interface the UI implements so a background connection can ask the
    user something. Methods are called from worker threads."""

    def ask_password(self, conn: dict) -> Optional[str]:
        return None

    def ask_login(self, conn: dict, reason: str = ""):
        """Ask for (username, password, remember); None if cancelled."""
        password = self.ask_password(conn)
        return None if password is None else (conn.get("user") or "", password, False)

    def saved_login(self, conn: dict):
        """(username, password) remembered for this server, or None."""
        return None

    def save_login(self, conn: dict, user: str, password: str) -> None:
        """Remember a login that just worked."""

    def forget_login(self, conn: dict) -> None:
        """Forget the remembered login (e.g. it was rejected and not re-saved)."""

    def confirm_host_key(self, host: str, key_type: str, fingerprint: str) -> bool:
        return False


class SFTPFileSystem(RemoteFileSystem):
    scheme = "sftp"

    def __init__(self, prefix: str, conn: dict, credentials: CredentialsProvider) -> None:
        super().__init__(prefix, conn)
        self._credentials = credentials
        self._lock = threading.RLock()
        self._client = None
        self._sftp = None

    # -- connection ----------------------------------------------------------
    def _connect(self):
        if paramiko is None:     # checked here (worker thread) so the UI can still show the path
            raise OSError("SFTP needs the 'paramiko' package:  pip install paramiko")
        with self._lock:
            if self._sftp is not None and self._client.get_transport() and self._client.get_transport().is_active():
                return self._sftp
            client = paramiko.SSHClient()
            known_hosts = config_file("known_hosts")
            try:
                client.load_system_host_keys()
            except OSError:
                pass
            if known_hosts.exists():
                client.load_host_keys(str(known_hosts))
            credentials = self._credentials

            class AskPolicy(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, cl, hostname, key):
                    fingerprint = key.get_fingerprint().hex(":")
                    if not credentials.confirm_host_key(hostname, key.get_name(), fingerprint):
                        raise paramiko.SSHException("Host key rejected by user")
                    cl.get_host_keys().add(hostname, key.get_name(), key)
                    cl.save_host_keys(str(known_hosts))

            client.set_missing_host_key_policy(AskPolicy())
            host, port = self.conn["host"], int(self.conn.get("port") or 22)
            if not self.conn.get("password"):
                saved = self._credentials.saved_login(self.conn)        # remembered in the keyring?
                if saved and (not self.conn.get("user") or saved[0] == self.conn.get("user")):
                    self.conn["user"], self.conn["password"] = saved
            user, password = self.conn.get("user") or None, self.conn.get("password") or None
            try:
                client.connect(host, port, username=user, password=password,
                               allow_agent=True, look_for_keys=True, timeout=15)
            except paramiko.AuthenticationException:
                for _attempt in range(3):          # keys/saved password didn't work: ask
                    answer = self._credentials.ask_login(self.conn, "Please enter your password.")
                    if not answer:
                        raise OSError("Authentication cancelled") from None
                    user, password, remember = answer[0] or user, answer[1], (answer[2] if len(answer) > 2 else False)
                    try:
                        client.connect(host, port, username=user, password=password,
                                       allow_agent=False, look_for_keys=False, timeout=15)
                    except paramiko.AuthenticationException:
                        continue
                    self.conn["user"], self.conn["password"] = user or "", password
                    if remember:
                        self._credentials.save_login(self.conn, user or "", password)
                    else:
                        self._credentials.forget_login(self.conn)
                    break
                else:
                    raise OSError("Wrong username or password") from None
            except (paramiko.SSHException, OSError) as exc:
                raise OSError(f"Cannot connect to {host}: {exc}") from exc
            self._client = client
            self._sftp = client.open_sftp()
            return self._sftp

    def _entry(self, folder_remote: str, attr) -> FileEntry:
        mode = attr.st_mode or 0
        is_link = stat_mod.S_ISLNK(mode)
        is_dir = stat_mod.S_ISDIR(mode)
        remote = posixpath.join(folder_remote, attr.filename)
        if is_link:   # find out what the link points at
            try:
                is_dir = stat_mod.S_ISDIR(self._sftp.stat(remote).st_mode or 0)
            except OSError:
                pass
        return FileEntry(attr.filename, self.to_uri(remote), self.to_uri(folder_remote), is_dir,
                         0 if is_dir else (attr.st_size or 0), float(attr.st_mtime or 0),
                         is_link, attr.filename.startswith("."))

    # -- FileSystem interface ------------------------------------------------
    def list_dir(self, path: str) -> Iterator[FileEntry]:
        remote = self.to_remote(path)
        with self._lock:
            sftp = self._connect()
            entries = [self._entry(remote, a) for a in sftp.listdir_attr(remote)]
        yield from entries

    def stat(self, path: str) -> FileEntry:
        remote = self.to_remote(path)
        with self._lock:
            attr = self._connect().stat(remote)
        attr.filename = posixpath.basename(remote) or "/"
        entry = self._entry(posixpath.dirname(remote), attr)
        entry.path = self.to_uri(remote)
        return entry

    def mkdir(self, path: str) -> None:
        with self._lock:
            self._connect().mkdir(self.to_remote(path))

    def rename(self, src: str, dst: str) -> None:
        with self._lock:
            self._connect().rename(self.to_remote(src), self.to_remote(dst))

    def remove_file(self, path: str) -> None:
        with self._lock:
            self._connect().remove(self.to_remote(path))

    def remove_dir(self, path: str) -> None:
        with self._lock:
            self._connect().rmdir(self.to_remote(path))

    def open_read(self, path: str) -> BinaryIO:
        with self._lock:
            fh = self._connect().open(self.to_remote(path), "rb")
        fh.prefetch()        # pipeline reads: much faster downloads
        return fh

    def open_write(self, path: str) -> BinaryIO:
        with self._lock:
            fh = self._connect().open(self.to_remote(path), "wb")
        fh.set_pipelined(True)
        return fh

    def set_mtime(self, path: str, mtime: float) -> None:
        try:
            with self._lock:
                self._connect().utime(self.to_remote(path), (mtime, mtime))
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
            self._client = self._sftp = None

"""
CredentialStore - remembers network logins (SMB, SFTP, FTP) safely.

Passwords go into your desktop's KEYRING through the ``keyring`` package:
    Linux:   GNOME Keyring / KWallet (the same store Nemo and Dolphin use)
    Windows: Credential Manager
    macOS:   Keychain
They are never written to PyExplorer's settings files.

Only if no keyring is available (e.g. no desktop session) does it fall back
to a file in the settings folder that only your user account may read
(permissions 600). The dialogs say which one is in use.

One login is kept per server, keyed like "smb://truenas.local:445".
A small index file (saved_logins.json, no passwords in it) lists which
servers have a saved login, because keyrings can't be listed.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional, Tuple

from ..core.connections import DEFAULT_PORTS
from ..core.storage import JsonStore

SERVICE = "PyExplorer"


def login_key(conn: dict) -> str:
    """'smb://truenas.local:445' for a connection dict (host/protocol/port)."""
    protocol = (conn.get("protocol") or "smb").lower()
    port = int(conn.get("port") or DEFAULT_PORTS.get(protocol, 0))
    return f"{protocol}://{(conn.get('host') or '').lower()}:{port}"


class CredentialStore:
    def __init__(self, config_dir: str, backend=None, use_system_keyring: bool = True) -> None:
        self._index = JsonStore(os.path.join(config_dir, "saved_logins.json"))
        self._file_path = os.path.join(config_dir, "credentials.json")
        self._backend = backend
        self._use_system = use_system_keyring
        self._checked = backend is not None
        self._lock = threading.Lock()

    # -- which store -----------------------------------------------------------------
    def _keyring(self):
        """The usable keyring backend, or None (then the protected file is used)."""
        if not self._checked:
            self._checked = True
            if self._use_system:
                try:
                    import keyring
                    from keyring.backends import fail
                    kr = keyring.get_keyring()
                    if not isinstance(kr, fail.Keyring) and getattr(kr, "priority", 1) > 0:
                        self._backend = kr
                except Exception:  # noqa: BLE001 - no keyring package or no backend
                    self._backend = None
        return self._backend

    @property
    def secure(self) -> bool:
        return self._keyring() is not None

    def where(self) -> str:
        """Plain words for the dialogs."""
        kr = self._keyring()
        if kr is None:
            return "a file only your user account can read (no system keyring was found)"
        name = type(kr).__module__.split(".")[-1]
        return {"SecretService": "your desktop keyring (GNOME Keyring)", "kwallet": "KWallet",
                "Windows": "Windows Credential Manager", "macOS": "the macOS Keychain"}.get(name, "your system keyring")

    # -- protected file fallback -------------------------------------------------------
    def _read_file(self) -> Dict[str, dict]:
        try:
            with open(self._file_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _write_file(self, data: Dict[str, dict]) -> None:
        fd = os.open(self._file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        try:
            os.chmod(self._file_path, 0o600)
        except OSError:
            pass

    # -- public ------------------------------------------------------------------------
    def get(self, key: str) -> Optional[Tuple[str, str]]:
        """(user, password) saved for this server, or None."""
        with self._lock:
            kr = self._keyring()
            try:
                raw = kr.get_password(SERVICE, key) if kr is not None else None
            except Exception:  # noqa: BLE001 - locked/unavailable keyring: behave as "not saved"
                raw = None
            if raw:
                try:
                    data = json.loads(raw)
                    return data.get("user", ""), data.get("password", "")
                except ValueError:
                    return None
            data = self._read_file().get(key)
            return (data.get("user", ""), data.get("password", "")) if data else None

    def set(self, key: str, user: str, password: str) -> None:
        with self._lock:
            secret = json.dumps({"user": user, "password": password})
            kr = self._keyring()
            stored_in = "file"
            if kr is not None:
                try:
                    kr.set_password(SERVICE, key, secret)
                    stored_in = "keyring"
                except Exception:  # noqa: BLE001
                    kr = None
            if kr is None:
                data = self._read_file()
                data[key] = {"user": user, "password": password}
                self._write_file(data)
            index = self._index.load(default={}) or {}
            index[key] = {"user": user, "stored_in": stored_in}
            self._index.save(index)

    def delete(self, key: str) -> None:
        with self._lock:
            kr = self._keyring()
            if kr is not None:
                try:
                    kr.delete_password(SERVICE, key)
                except Exception:  # noqa: BLE001 - wasn't there
                    pass
            data = self._read_file()
            if data.pop(key, None) is not None:
                self._write_file(data)
            index = self._index.load(default={}) or {}
            if index.pop(key, None) is not None:
                self._index.save(index)

    def saved(self) -> List[Tuple[str, str]]:
        """[(server key, user)] of every saved login (no passwords)."""
        index = self._index.load(default={}) or {}
        return sorted((key, info.get("user", "")) for key, info in index.items())

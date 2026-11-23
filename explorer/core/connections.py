"""
Connections - saved network locations (SFTP / FTP / FTPS), like WinSCP's
site manager.

Each connection has a URI prefix such as ``sftp://alice@example.com:22``.
Any path starting with that prefix is served by that connection, which is
how a sidebar item like ``sftp://alice@example.com:22/var/www`` works.

Passwords are only saved when the user ticks "remember password", and they
are stored as plain text in connections.json (clearly warned in the UI).
SSH keys / ssh-agent are used automatically for SFTP, which is the better
option.
"""
from __future__ import annotations

import uuid
from typing import List, Optional
from urllib.parse import urlsplit

from .events import EventBus, Topics
from .storage import JsonStore

DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21, "smb": 445}


def make_prefix(protocol: str, user: str, host: str, port: int) -> str:
    user_part = f"{user}@" if user else ""
    return f"{protocol}://{user_part}{host}:{port}"


def split_uri(uri: str):
    """'sftp://bob@host:2222/var/www' -> ('sftp', 'bob', 'host', 2222, '/var/www')"""
    parts = urlsplit(uri)
    protocol = parts.scheme.lower()
    port = parts.port or DEFAULT_PORTS.get(protocol, 0)
    path = parts.path or "/"
    return protocol, parts.username or "", parts.hostname or "", port, path


class Connections:
    def __init__(self, store: JsonStore, bus: EventBus) -> None:
        self._store = store
        self._bus = bus
        self._items: List[dict] = list(store.load(default=[]) or [])
        self._session_passwords: dict = {}     # typed this session, never written to disk

    def remember_session_password(self, conn_id: str, password: str) -> None:
        self._session_passwords[conn_id] = password

    def all(self) -> List[dict]:
        return [dict(c) for c in self._items]

    def get(self, conn_id: str) -> Optional[dict]:
        return next((dict(c) for c in self._items if c["id"] == conn_id), None)

    def prefix_of(self, conn: dict) -> str:
        return make_prefix(conn["protocol"], conn.get("user", ""), conn["host"], int(conn["port"]))

    def root_uri(self, conn: dict) -> str:
        path = conn.get("initial_path") or "/"
        if not path.startswith("/"):
            path = "/" + path
        return self.prefix_of(conn) + path

    def find_for_uri(self, uri: str) -> Optional[dict]:
        protocol, user, host, port, _ = split_uri(uri)
        for c in self._items:
            if (c["protocol"] == protocol and c["host"].lower() == host.lower()
                    and int(c["port"]) == port and (c.get("user") or "") == user):
                found = dict(c)
                if not found.get("password") and c["id"] in self._session_passwords:
                    found["password"] = self._session_passwords[c["id"]]
                return found
        return None

    def save_connection(self, conn: dict) -> dict:
        """Add (no id) or update (has id) a connection. Returns the stored copy."""
        conn = dict(conn)
        conn["password"] = ""       # passwords live in the keyring (services/credential_store.py), never here
        if not conn.get("id"):
            conn["id"] = uuid.uuid4().hex[:10]
            self._items.append(conn)
        else:
            self._items = [conn if c["id"] == conn["id"] else c for c in self._items]
        self._store.save(self._items)
        self._bus.publish(Topics.CONNECTIONS_CHANGED)
        return dict(conn)

    def take_plaintext_passwords(self) -> list:
        """Older versions could store passwords here. Returns [(conn, password)] and
        removes them from the file (the caller moves them into the keyring)."""
        found = [(dict(c), c["password"]) for c in self._items if c.get("password")]
        if found:
            for c in self._items:
                c["password"] = ""
            self._store.save(self._items)
        return found

    def delete(self, conn_id: str) -> None:
        self._items = [c for c in self._items if c["id"] != conn_id]
        self._store.save(self._items)
        self._bus.publish(Topics.CONNECTIONS_CHANGED)

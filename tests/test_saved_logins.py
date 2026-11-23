"""
Tests for remembered network logins.

  * the store: system keyring (a stand-in by default; the REAL desktop keyring
    when one is running), and the protected-file fallback (permissions 600)
  * SMB against a real server: asked once with "Remember" ticked -> next start
    logs in silently; a changed password is asked for again and re-saved;
    unticking "Remember" forgets it
  * old plain-text passwords in connections.json are moved into the store

Needs the SMB test server from test_smb.py (or PYX_SMB_HOST/USER/PASS).
Run:  python tests/test_saved_logins.py
"""
import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from explorer.core.connections import Connections  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.services.credential_store import CredentialStore, login_key  # noqa: E402
from explorer.vfs.registry import VfsRegistry  # noqa: E402
from explorer.vfs.sftp import CredentialsProvider  # noqa: E402

HOST = os.environ.get("PYX_SMB_HOST", "127.0.0.1")
USER = os.environ.get("PYX_SMB_USER", "nasuser")
PASS = os.environ.get("PYX_SMB_PASS", "secret123")


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


class MemoryKeyring:
    """Behaves like a system keyring, kept in memory (so tests never touch yours)."""
    priority = 1

    def __init__(self):
        self.data = {}

    def get_password(self, service, key):
        return self.data.get((service, key))

    def set_password(self, service, key, value):
        self.data[(service, key)] = value

    def delete_password(self, service, key):
        self.data.pop((service, key), None)


print("the store")
cfg = tempfile.mkdtemp()
kr = MemoryKeyring()
store = CredentialStore(cfg, backend=kr)
key = login_key({"protocol": "smb", "host": "TrueNAS.local", "port": 445})
check(key == "smb://truenas.local:445", "one login per server")
store.set(key, "bob", "pw1")
check(store.get(key) == ("bob", "pw1") and store.secure, "saved in the keyring and read back")
check("pw1" not in open(os.path.join(cfg, "saved_logins.json")).read(), "the index file holds no passwords")
check(not os.path.exists(os.path.join(cfg, "credentials.json")), "nothing written to disk when a keyring exists")
check(store.saved() == [(key, "bob")], "list of saved logins")
store.delete(key)
check(store.get(key) is None and store.saved() == [], "forget")

fallback = CredentialStore(tempfile.mkdtemp(), use_system_keyring=False)
fallback.set(key, "bob", "pw2")
path = fallback._file_path
check(fallback.get(key) == ("bob", "pw2") and not fallback.secure, "fallback file works without a keyring")
if os.name == "posix":
    check(stat.S_IMODE(os.stat(path).st_mode) == 0o600, "fallback file is readable only by you (600)")
check("file only your user account can read" in fallback.where(), "dialogs say where it's kept")

real = CredentialStore(tempfile.mkdtemp())
if real.secure:
    real.set("smb://pyexplorer-test:445", "u", "p")
    ok = real.get("smb://pyexplorer-test:445") == ("u", "p")
    real.delete("smb://pyexplorer-test:445")
    check(ok, f"REAL system keyring round trip ({real.where()})")
else:
    print("  (no system keyring running here - real keyring test skipped)")


class Provider(CredentialsProvider):
    """Like the app's UICredentials: a login box (scripted answers) + the store."""

    def __init__(self, store, answers):
        self.store, self.answers, self.asked = store, list(answers), []

    def ask_login(self, conn, reason=""):
        self.asked.append(reason)
        return self.answers.pop(0) if self.answers else None

    def saved_login(self, conn):
        return self.store.get(login_key(conn))

    def save_login(self, conn, user, password):
        self.store.set(login_key(conn), user, password)

    def forget_login(self, conn):
        self.store.delete(login_key(conn))


def browse(provider):
    """A fresh 'start of PyExplorer': new registry, open the server."""
    cfg_dir = tempfile.mkdtemp()
    vfs = VfsRegistry(Connections(JsonStore(cfg_dir + "/c.json"), EventBus()), provider)
    root = f"smb://{HOST}/"
    names = [e.name for e in vfs.resolve(root).list_dir(root)]
    vfs.close_all()
    return names


print("SMB: remembering a login")
store = CredentialStore(tempfile.mkdtemp(), backend=MemoryKeyring())
first = Provider(store, [(USER, PASS, True)])
check(browse(first) and len(first.asked) == 1, "first time: asked once (with Remember ticked)")
second = Provider(store, [])
check(browse(second) and second.asked == [], "next start: logs in silently with the saved login")
store.set(login_key({"protocol": "smb", "host": HOST, "port": 445}), USER, "old-password")
third = Provider(store, [(USER, PASS, True)])
check(browse(third) and len(third.asked) == 1 and "rejected" in third.asked[0],
      "a changed password: explains the saved one was rejected and asks again")
check(store.get(login_key({"protocol": "smb", "host": HOST, "port": 445})) == (USER, PASS), "...and saves the new one")
fourth = Provider(store, [])
check(browse(fourth) and fourth.asked == [], "...which then works silently")
store.delete(login_key({"protocol": "smb", "host": HOST, "port": 445}))
fifth = Provider(store, [(USER, PASS, False)])
browse(fifth)
check(store.saved() == [], "Remember unticked: nothing saved")

print("moving old plain-text passwords into the store")
cfg = tempfile.mkdtemp()
with open(os.path.join(cfg, "connections.json"), "w") as fh:
    json.dump([{"id": "a1", "name": "NAS", "protocol": "smb", "host": "truenas.local", "port": 445,
                "user": "bob", "password": "plain!", "remember_password": True}], fh)
conns = Connections(JsonStore(os.path.join(cfg, "connections.json")), EventBus())
store = CredentialStore(cfg, backend=MemoryKeyring())
for conn, password in conns.take_plaintext_passwords():
    store.set(login_key(conn), conn.get("user") or "", password)
check("plain!" not in open(os.path.join(cfg, "connections.json")).read(), "the password is gone from connections.json")
check(store.get("smb://truenas.local:445") == ("bob", "plain!"), "...and is in the keyring instead")
saved = conns.save_connection({"name": "x", "protocol": "smb", "host": "h", "port": 445, "user": "u",
                               "password": "secret", "remember_password": True})
check("secret" not in open(os.path.join(cfg, "connections.json")).read(), "new connections never write passwords to the file")
print("\nSAVED-LOGIN TESTS PASSED")

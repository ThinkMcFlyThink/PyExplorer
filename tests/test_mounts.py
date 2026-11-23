"""
Tests for Linux network-mount detection, using FAKE /proc/mounts, /etc/fstab
and GVFS folders (so nothing on your system is touched):
  * shares Nemo/Nautilus opened (GVFS) get friendly names,
  * mounted CIFS/NFS shares are listed,
  * /etc/fstab shares that aren't mounted are listed, and opening one mounts
    it (if allowed) or explains how to allow it.

Run:  python tests/test_mounts.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from explorer.core.platform_info import fstab_network_entries, gvfs_mounts, read_mounts  # noqa: E402
from explorer.vfs import automount  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


tmp = tempfile.mkdtemp()
gvfs = os.path.join(tmp, "gvfs")
for name in ("smb-share:server=truenas.local,share=media,user=bob", "sftp:host=pi.local", "smb-share:server=nas,share=backups"):
    os.makedirs(os.path.join(gvfs, name))
proc = os.path.join(tmp, "mounts")
with open(proc, "w") as fh:
    fh.write("sysfs /sys sysfs rw 0 0\n"
             "//truenas.local/photos /mnt/My\\040Photos cifs rw,vers=3.1.1 0 0\n"
             "nas:/export/films /mnt/films nfs4 rw 0 0\n")
fstab = os.path.join(tmp, "fstab")
with open(fstab, "w") as fh:
    fh.write("# comment\nUUID=abcd / ext4 defaults 0 1\n"
             "//truenas.local/photos /mnt/My\\040Photos cifs credentials=/etc/nas,uid=1000 0 0\n"
             "//truenas.local/media  /mnt/media cifs noauto,user,credentials=/home/me/.nas 0 0\n"
             "//truenas.local/admin  /mnt/admin cifs noauto,credentials=/etc/nas 0 0\n")

print("GVFS (shares opened in Nemo)")
labels = dict(gvfs_mounts(gvfs))
check("media on truenas.local" in labels and "backups on nas" in labels, f"friendly share names: {sorted(labels)}")
check("pi.local (SFTP)" in labels, "other GVFS mounts too")
check(gvfs_mounts(os.path.join(tmp, "missing")) == [], "no GVFS folder = nothing")

print("mounted shares")
mounts = read_mounts(proc)
check(("/mnt/My Photos", "cifs") in mounts, "spaces in mount points (\\040) decoded")

print("/etc/fstab")
entries = {e["mountpoint"]: e for e in fstab_network_entries(fstab, proc)}
check(set(entries) == {"/mnt/My Photos", "/mnt/media", "/mnt/admin"}, "only network shares are picked up")
check(entries["/mnt/My Photos"]["mounted"] and not entries["/mnt/media"]["mounted"], "mounted / not mounted")
check(entries["/mnt/media"]["user_mountable"] and not entries["/mnt/admin"]["user_mountable"], "the 'user' option is detected")

print("opening an unmounted share")
automount.FSTAB, automount.PROC_MOUNTS = fstab, proc
automount.IS_LINUX = True
ran = []
real_run = subprocess.run
automount.subprocess.run = lambda args, **kw: (ran.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1]
check(automount.ensure_mounted("/mnt/media") is True and ran == [["mount", "/mnt/media"]], "user-mountable: runs 'mount /mnt/media'")
check(automount.ensure_mounted("/mnt/My Photos") is False, "already mounted: nothing to do")
check(automount.ensure_mounted("/home/me") is False, "ordinary folders are left alone")
try:
    automount.ensure_mounted("/mnt/admin")
    raise AssertionError("should have refused")
except PermissionError as exc:
    check("sudo mount /mnt/admin" in str(exc) and "'user' option" in str(exc), "not user-mountable: explains how to fix it")
automount.subprocess.run = lambda args, **kw: subprocess.CompletedProcess(args, 32, "", "mount error(13): Permission denied")
try:
    automount.ensure_mounted("/mnt/media")
    raise AssertionError("should have failed")
except OSError as exc:
    check("Permission denied" in str(exc), "mount errors are passed on to the user")
automount.subprocess.run = real_run
print("\nMOUNT TESTS PASSED")

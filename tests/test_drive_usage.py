"""Tests for services/drive_usage.py: where the "how full" numbers come from
(disk, rclone mount, rclone about for sync folders) and the cache. No GUI, no
network: rclone is faked. Run:  python tests/test_drive_usage.py"""
import os, sys, time, tempfile, stat
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()
from explorer.services import drive_usage as du
from explorer.services.drive_usage import Usage, measure, parse_about, remote_for, rclone_about, DriveUsage, FAKE_TOTAL
from explorer.services.devices import Device, classify_mount
from explorer.core.tasks import UIDispatcher, TaskRunner

home = tempfile.mkdtemp()

# -- Usage
u = Usage(200, 150, 50)
assert u.fraction == 0.75 and u.text == "50 B free of 200 B" and "75% used" in u.tooltip
assert Usage(0, 0, 0).fraction == 0.0 and Usage(10, 20, 0).fraction == 1.0

# -- rclone about output
about_json = '{"total": 230854492160, "used": 4509715661, "trashed": 0, "free": 226344776499}'
a = parse_about(about_json, "OneDrive:")
assert a.total == 230854492160 and a.free == 226344776499 and a.source == "rclone OneDrive:"
assert parse_about('{"used": 5}', "x:") is None, "no total (e.g. S3): no bar"
assert parse_about("not json", "x:") is None
assert parse_about('{"total": 100, "used": 30}', "x:").free == 70, "free worked out when missing"

# -- which rclone remote belongs to a sync folder
remotes = {"OneDrive": {"type": "onedrive"}, "gd": {"type": "drive"}}
assert remote_for("OneDrive", "OneDrive", remotes) == "OneDrive:"
assert remote_for("Google Drive", "Google Drive", remotes) == "gd:"
assert remote_for("MEGA", "MEGA", remotes) is None
two = {"work": {"type": "onedrive"}, "home": {"type": "onedrive"}}
assert remote_for("OneDrive", "OneDrive - Contoso", two) is None, "two accounts: don't guess"
assert remote_for("OneDrive", "OneDrive (work)", two) == "work:", "...unless the label names one"
assert remote_for("", "x", remotes) is None

# -- measure: drives, mounts, sync folders
calls = []
fake_about = lambda remote: (calls.append(remote), Usage(1000, 250, 750, f"rclone {remote}"))[1]
drive = Device("Data", home, "drive", "drives")
d = measure(home, drive, about=fake_about)
assert d is not None and d.source == "disk" and d.total > 0 and not calls, "a drive: its own numbers"
assert measure(home, None, about=fake_about).source == "disk", "no device info: treated as a drive"
assert measure("sftp://host/x", None, about=fake_about) is None, "connections are not measured"
assert measure(home, Device("NAS (not mounted)", home, "network", "network", mounted=False), about=fake_about) is None, \
    "an unmounted fstab share is never mounted just for a bar"

sync = Device("OneDrive", home, "cloud", "cloud", "OneDrive")
s = measure(home, sync, remotes=remotes, is_mount=lambda p: False, about=fake_about)
assert s.source == "rclone OneDrive:" and calls == ["OneDrive:"], "a sync folder: the account's quota, not the local disk"
assert measure(home, Device("MEGA", home, "cloud", "cloud", "MEGA"), remotes=remotes,
               is_mount=lambda p: False, about=fake_about) is None, "no rclone remote for it: no bar (not a wrong one)"

# rclone mount: statvfs is the quota; a fake 1 PiB disk falls back to rclone about
rc = classify_mount("onedrive:Docs", os.path.join(home, "od"), "fuse.rclone", home, {"onedrive": {"type": "onedrive"}})
assert rc.source == "onedrive:Docs" and rc.section == "cloud"
calls.clear()
real_disk = du.disk_usage
du.disk_usage = lambda p: Usage(5000, 1000, 4000)
assert measure(rc.path, rc, about=fake_about).total == 5000 and not calls, "rclone mount with a quota: statvfs"
du.disk_usage = lambda p: Usage(FAKE_TOTAL, 0, FAKE_TOTAL)
assert measure(rc.path, rc, about=fake_about).source == "rclone onedrive:Docs", "fake 1 PiB -> rclone about"
gv = Device("Google Drive", "/run/user/1000/gvfs/google-drive:host=x", "cloud", "cloud", "Google Drive")
assert measure(gv.path, gv, about=fake_about) is None, "GVFS cloud drive reporting a fake size: no bar"
du.disk_usage = lambda p: Usage(5000, 1000, 4000)
assert measure(gv.path, gv, about=fake_about).source == "disk", "GVFS cloud drive: its own (quota) numbers"
du.disk_usage = real_disk

# rclone_about: runs rclone, parses it, survives failures
fake = os.path.join(home, "rclone")
with open(fake, "w") as fh:
    fh.write("#!/bin/sh\n[ \"$1\" = about ] && [ \"$2\" = \"OneDrive:\" ] && echo '%s' && exit 0\necho nope >&2; exit 1\n" % about_json)
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
assert rclone_about("OneDrive:Some/Folder", rclone=fake).total == 230854492160, "remote:path -> remote:"
assert rclone_about("Other:", rclone=fake) is None
assert rclone_about("x:", rclone=os.path.join(home, "missing")) is None

# -- the cache: get() never blocks, measures once, announces new values
disp = UIDispatcher(lambda ms, fn: None)
runner = TaskRunner(disp)
changed = []
cache = DriveUsage(runner, lambda: [drive], changed.append)
assert cache.get(home) is None, "first ask: nothing yet (measuring in the background)"
assert cache.get(home) is None and len(cache._busy) == 1, "...and only one measurement at a time"
end = time.time() + 5
while time.time() < end and not changed:
    disp.pump_once(); time.sleep(0.005)
assert changed == [home] and cache.get(home).total > 0
cache.get(home)
assert not cache._busy, "fresh value: no new measurement"
cache.invalidate()
cache.get(home)
assert cache._busy, "after a file operation: measured again when shown"
cache.enabled = False
assert cache.get(home) is None, "bars turned off"
assert cache.get("smb://nas/share") is None
runner.shutdown()

if "--real" in sys.argv:                       # optional: your own rclone remotes, read-only
    from explorer.services import rclone_config
    for name in rclone_config.read_remotes():
        print("   ", name, rclone_about(name + ":"))
print("ALL OK")

"""
Tests for live drive / mount / cloud detection.

    python tests/test_devices.py

Part 1 uses made-up mount lists and config files (runs anywhere).
Part 2 does REAL mounts with rclone (and a loop-mounted disk image when run as
root) and times how fast the background monitor notices them.
Part 3 checks the real interface: sidebar, Computer view, status message.
Parts 2 and 3 are skipped when rclone / FUSE aren't available.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pyx-dev-cfg-")

from explorer.core.categories import Categories, migrate_v1  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.services import rclone_config  # noqa: E402
from explorer.services.cloud_providers import detect_linux  # noqa: E402
from explorer.services.device_monitor import DeviceMonitor, describe_changes  # noqa: E402
from explorer.services.devices import scan_linux  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


tmp = tempfile.mkdtemp(prefix="pyx-dev-")

# ===========================================================================
print("rclone remotes")
conf = os.path.join(tmp, "rclone.conf")
write(conf, """
[onedrive]
type = onedrive
drive_type = personal

[gdrive]
type = drive

[secret]
type = crypt
remote = gdrive:Encrypted

[nas]
type = alias
remote = mybox:/srv

[mybox]
type = sftp
host = pi.local

[cloudy]
type = webdav
vendor = nextcloud
url = https://cloud.example.com/remote.php/dav

[pool]
type = union
upstreams = onedrive:Pool gdrive:Pool:ro
""")
remotes = rclone_config.read_remotes(conf)
d = lambda src: rclone_config.describe_remote(src, remotes)        # noqa: E731
check((d("onedrive:").section, d("onedrive:").provider) == ("cloud", "OneDrive"), "onedrive: is OneDrive (cloud)")
check(d("gdrive:Photos/2024").provider == "Google Drive", "gdrive:Photos is Google Drive")
check(d("secret:").provider == "Google Drive", "a crypt remote is looked through to Google Drive")
check((d("nas:").section, d("nas:").provider) == ("network", "SFTP"), "an alias of an SFTP remote is Network")
check(d("cloudy:").provider == "Nextcloud", "WebDAV with vendor=nextcloud is Nextcloud")
check(d("pool:").provider == "OneDrive", "a union remote uses its first upstream")
check(d(":sftp,host=x:/data").section == "network", "on-the-fly :sftp remote is Network")
write(os.path.join(tmp, "enc.conf"), "# Encrypted rclone configuration File\n\nRCLONE_ENCRYPT_V0:\nabc")
check(rclone_config.read_remotes(os.path.join(tmp, "enc.conf")) == {}, "encrypted config: nothing readable")
check(rclone_config.describe_remote("MyOneDrive:", {}).provider == "OneDrive", "encrypted config: guessed from the name")
check(rclone_config.describe_remote("weird:", {}).section == "cloud", "unknown remotes count as cloud")

# ===========================================================================
print("sorting the mount list")
home = os.path.join(tmp, "home", "me")
os.makedirs(home)
mounts = os.path.join(tmp, "mounts")
write(mounts, "\n".join([
    "/dev/sda2 / ext4 rw 0 0",
    "/dev/sda1 /boot/efi vfat rw 0 0",
    "/dev/nvme0n1p3 /home ext4 rw 0 0",
    "/dev/sdb1 /media/me/USB\\040STICK vfat rw 0 0",
    "/dev/sdc1 /mnt/backup ext4 rw 0 0",
    "//truenas.local/media /mnt/nas cifs rw 0 0",
    f"onedrive: {home}/OneDrive fuse.rclone rw 0 0",
    f"gdrive:Photos {home}/Photos fuse.rclone rw 0 0",
    f"nas: {home}/Server fuse.rclone rw 0 0",
    f"onedriver {home}/od fuse.onedriver rw 0 0",
    f"me@pi.local:/srv/files {home}/pi fuse.sshfs rw 0 0",
    f"/dev/sdd1 {home}/ntfs fuseblk rw 0 0",
    "portal /run/user/1000/doc fuse.portal rw 0 0",
    "/dev/loop3 /snap/core/123 squashfs ro 0 0",
    "tmpfs /tmp tmpfs rw 0 0",
    "proc /proc proc rw 0 0",
    "//truenas.local/media /mnt/nas cifs rw 0 0",       # the same mount twice (stacked): shown once
]) + "\n")
fstab = os.path.join(tmp, "fstab")
write(fstab, "//truenas.local/backups /mnt/backups cifs user,noauto 0 0\n"
             "//truenas.local/media /mnt/nas cifs user 0 0\n")
gvfs = os.path.join(tmp, "gvfs")
for name in ("smb-share:server=truenas.local,share=photos", "google-drive:host=gmail.com,user=bob",
             "mtp:host=SAMSUNG_Android_R58M12345"):
    os.makedirs(os.path.join(gvfs, name))
# Linux sync apps: the "onedrive" client, Dropbox (running), Nextcloud
config_home = os.path.join(home, ".config")
write(os.path.join(config_home, "onedrive", "config"), 'sync_dir = "~/OneDriveSync"\n')
os.makedirs(os.path.join(home, "OneDriveSync"))
write(os.path.join(home, ".dropbox", "info.json"), json.dumps({"personal": {"path": os.path.join(home, "Dropbox")}}))
os.makedirs(os.path.join(home, "Dropbox"))
write(os.path.join(config_home, "Nextcloud", "nextcloud.cfg"),
      f"[Accounts]\n0\\Folders\\1\\localPath={home}/Nextcloud/\n")
os.makedirs(os.path.join(home, "Nextcloud"))
write(os.path.join(config_home, "onedrive-work", "config"), 'sync_dir = "/nonexistent/never-synced"\n')


def fake_cloud(home, skip):
    return detect_linux(home=home, config_home=config_home, running={"dropbox", "bash"}, skip=skip)


devices = scan_linux(mounts, fstab, gvfs, home, conf, os.path.join(tmp, "no-sys"), fake_cloud)
by_path = {dev.path: dev for dev in devices}
section = lambda p: by_path[p].section if p in by_path else None   # noqa: E731
print("   ", [(dev.label, dev.section, dev.kind, dev.provider) for dev in devices])
check(devices[0].path == "/" and section("/boot/efi") is None, "system mounts (/boot/efi) are left out")
check(by_path["/home"].label == "/home" and section("/home") == "drives", "a separate /home partition is a drive")
check(by_path["/media/me/USB STICK"].kind == "removable", "a USB stick in /media is removable (name with a space)")
check(by_path["/mnt/backup"].kind == "drive", "a disk in /mnt is a drive")
check(by_path["/mnt/nas"].section == "network" and by_path["/mnt/nas"].provider == "SMB", "cifs mount is Network (SMB)")
check([d.path for d in devices].count("/mnt/nas") == 1, "a mount listed twice shows once")
od = by_path[f"{home}/OneDrive"]
check((od.section, od.kind, od.provider) == ("cloud", "cloud", "OneDrive"), "rclone onedrive: mount is Cloud / OneDrive")
check(by_path[f"{home}/Photos"].provider == "Google Drive", "rclone gdrive: mount is Cloud / Google Drive")
check(section(f"{home}/Server") == "network", "rclone mount of an SFTP alias is Network")
check(by_path[f"{home}/od"].provider == "OneDrive", "onedriver mount is Cloud / OneDrive")
check(by_path[f"{home}/pi"].provider == "SSHFS", "sshfs mount is Network")
check(section(f"{home}/ntfs") == "drives", "an NTFS USB disk (fuseblk) is a drive")
check(section("/run/user/1000/doc") is None and section("/snap/core/123") is None and section("/tmp") is None,
      "portal, snaps and tmpfs are left out")
gv = {dev.label: dev for dev in devices if dev.path.startswith(gvfs)}
check(gv["photos on truenas.local"].section == "network", "GVFS SMB share is Network")
check(gv["Google Drive (bob@gmail.com)"].section == "cloud", "GNOME Online Accounts Google Drive is Cloud")
phone = next(dev for dev in devices if dev.kind == "phone")
check(phone.section == "drives" and phone.label == "SAMSUNG Android R58M12345", f"a phone (MTP) is in Drives: {phone.label}")
check(by_path["/mnt/backups"].mounted is False and "not mounted" in by_path["/mnt/backups"].label,
      "unmounted fstab share is listed as 'not mounted'")
sync = by_path[os.path.join(home, "OneDriveSync")]
check((sync.section, sync.provider, sync.running) == ("cloud", "OneDrive", False),
      "the OneDrive client's folder is found from its config (not running)")
check(by_path[os.path.join(home, "Dropbox")].running is True, "Dropbox folder found, and it is running")
check(section(os.path.join(home, "Nextcloud")) == "cloud", "Nextcloud folder found from nextcloud.cfg")
check(not any("never-synced" in p for p in by_path), "a configured but never-synced folder is skipped")
check(all(dev.cloud_root is not None for dev in devices if dev.section == "cloud" and dev.running is not None),
      "sync-app folders carry a CloudRoot (for icons)")

msg = describe_changes(devices[:3], devices[:2] + [od])
check(msg and "Connected: OneDrive" in msg and "Removed:" in msg, f"status message: {msg!r}")

# ===========================================================================
print("sidebar categories: live sections + upgrade of old files")
v1 = [
    {"id": "a", "name": "Places", "items": [{"id": "1", "label": "Home", "path": home, "kind": "folder"}]},
    {"id": "b", "name": "Drives", "items": [
        {"id": "2", "label": "Computer", "path": "computer://", "kind": "computer"},
        {"id": "3", "label": "Root (/)", "path": "/", "kind": "drive"},
        {"id": "4", "label": "nas", "path": "/mnt/nas", "kind": "network"},
        {"id": "5", "label": "My stuff", "path": "/data/stuff", "kind": "folder"}]},
    {"id": "c", "name": "Network", "items": [
        {"id": "6", "label": "truenas", "path": "smb://truenas.local:445/", "kind": "network"}]},
]
migrated = migrate_v1(json.loads(json.dumps(v1)))
names = [(c["name"], c.get("auto")) for c in migrated]
check(names == [("Places", None), ("Cloud", "cloud"), ("Drives", "drives"), ("Network", "network")],
      f"old categories become live sections, Cloud added after Places: {names}")
drives = migrated[2]
check([i["label"] for i in drives["items"]] == ["Computer", "My stuff"],
      "the old drive snapshot is dropped, the user's own items stay")
check([i["label"] for i in migrated[3]["items"]] == ["truenas"], "saved connections stay in Network")
store = JsonStore(os.path.join(tmp, "categories.json"))
store.save(v1)
cats = Categories(store, EventBus())
check(store.load()["version"] == 2, "categories.json is upgraded once and saved as version 2")
check(set(cats.auto_sections()) == {"drives", "network", "cloud"}, "all three live sections exist")
drives_id = cats.auto_sections()["drives"]
cats.hide_live(drives_id, "/mnt/backup")
check(Categories(store, EventBus()).find_category(drives_id)["hidden"] == ["/mnt/backup"], "hiding a live entry is saved")
cats.unhide_all(drives_id)
check(not cats.find_category(drives_id)["hidden"], "'show hidden entries' brings them back")
cats.delete_category(cats.auto_sections()["cloud"])
check("cloud" not in Categories(store, EventBus()).auto_sections(),
      "a deleted live section stays deleted (no re-upgrade)")

# ===========================================================================
print("REAL mounts: the background monitor")
RCLONE = shutil.which("rclone") or next((p for p in ("/opt/rclone/rclone-filestore",) if os.path.exists(p)), None)
can_fuse = os.path.exists("/dev/fuse") and shutil.which("fusermount3") or shutil.which("fusermount")


class InlineDispatcher:
    """Collects what the monitor posts (the real app hands it to the Qt UI thread)."""
    def __init__(self):
        self.calls = []

    def post(self, fn, *args):
        self.calls.append((time.monotonic(), fn, args))
        fn(*args)


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def unmount(path):
    for cmd in (["fusermount3", "-u", path], ["fusermount", "-u", path], ["umount", "-l", path]):
        if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True).returncode == 0:
            return True
    return False


real_home = os.path.expanduser("~")
mount_point = os.path.join(real_home, "OneDrive-pyx-test")
mount_conf = os.path.join(tmp, "mount.conf")          # rclone mounts a LOCAL folder named "onedrive"...
write(mount_conf, "[onedrive]\ntype = local\n")
os.environ["RCLONE_CONFIG"] = conf                    # ...but the scanner's config says it's OneDrive
source_dir = os.path.join(tmp, "cloud-files")
os.makedirs(source_dir)
write(os.path.join(source_dir, "hello.txt"), "hi")

if RCLONE and can_fuse:
    changes = []
    monitor = DeviceMonitor(InlineDispatcher(), lambda old, new: changes.append((time.monotonic(), old, new)),
                            interval=30.0)          # long interval: only the kernel's mount event can wake it
    monitor.start()
    os.makedirs(mount_point, exist_ok=True)
    t0 = time.monotonic()
    proc = subprocess.Popen([RCLONE, "mount", f"onedrive:{source_dir}", mount_point, "--config", mount_conf],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        found = wait_for(lambda: any(d.path == mount_point for d in monitor.devices), 10)
        if not found and proc.poll() is not None:
            print("    (rclone mount failed:", proc.stderr.read().decode()[-300:], ")")
        check(found, "an rclone mount appears while the app runs")
        seen = next(t for t, _old, new in changes if any(d.path == mount_point for d in new))
        dev = next(d for d in monitor.devices if d.path == mount_point)
        check((dev.section, dev.provider) == ("cloud", "OneDrive"), "it is sorted into Cloud as OneDrive")
        print(f"    noticed {seen - t0:.2f}s after starting 'rclone mount' (rclone itself needs part of that)")
        check(seen - t0 < 5, "noticed from the kernel's mount event, not a 30 s poll")
        t1 = time.monotonic()
        unmount(mount_point)
        proc.wait(timeout=10)
        gone = wait_for(lambda: not any(d.path == mount_point for d in monitor.devices), 10)
        check(gone, f"unmounting removes it ({time.monotonic() - t1:.2f}s)")
    finally:
        if proc.poll() is None:
            unmount(mount_point)
            proc.kill()
        monitor.stop()

    # GVFS-style changes don't touch the mount list: picked up by the short poll.
    gvfs_live = os.path.join(tmp, "gvfs-live")
    os.makedirs(gvfs_live)
    changes.clear()
    monitor = DeviceMonitor(InlineDispatcher(), lambda old, new: changes.append(new),
                            scan=lambda: scan_linux(gvfs=gvfs_live, cloud_detect=lambda **k: []), interval=0.3)
    monitor.start()
    os.makedirs(os.path.join(gvfs_live, "smb-share:server=nas,share=films"))
    check(wait_for(lambda: any(d.label == "films on nas" for d in monitor.devices), 3),
          "a share opened in Nemo (GVFS) appears within the poll interval")
    monitor.stop()
    check(monitor._thread is None, "the monitor thread stops cleanly")
else:
    print("  (skipped: rclone or FUSE not available)")

# A real disk image mounted like a USB stick (needs root + loop devices).
if os.geteuid() == 0 and shutil.which("mkfs.ext4") and shutil.which("losetup"):
    img = os.path.join(tmp, "stick.img")
    usb = "/media/pyxtest/STICK"
    subprocess.run(["truncate", "-s", "8M", img], check=True)
    subprocess.run(["mkfs.ext4", "-q", "-F", img], check=True, capture_output=True)
    os.makedirs(usb, exist_ok=True)
    monitor = DeviceMonitor(InlineDispatcher(), lambda o, n: None, interval=30.0)
    monitor.start()
    res = subprocess.run(["mount", "-o", "loop", img, usb], capture_output=True, text=True)
    if res.returncode == 0:
        try:
            check(wait_for(lambda: any(d.path == usb for d in monitor.devices), 5),
                  "a disk mounted in /media appears (loop-mounted ext4 image)")
            dev = next(d for d in monitor.devices if d.path == usb)
            check(dev.section == "drives" and dev.label == "STICK", f"…in Drives as {dev.label!r} ({dev.kind})")
        finally:
            subprocess.run(["umount", usb])
        check(wait_for(lambda: not any(d.path == usb for d in monitor.devices), 5), "…and goes away when unmounted")
    else:
        print("  (loop mount not possible here:", res.stderr.strip()[:80], ")")
    monitor.stop()
    shutil.rmtree("/media/pyxtest", ignore_errors=True)

# ===========================================================================
print("in the interface")
if RCLONE and can_fuse:
    from PySide6.QtWidgets import QApplication

    from explorer.app import build_context
    from explorer.core.events import Topics

    app = QApplication([])
    ctx = build_context(app)
    ctx.main.restore(None)
    ctx.dispatcher.start()
    ctx.devices.start()
    ctx.main.resize(1100, 700)
    ctx.main.show()
    messages = []
    ctx.bus.subscribe(Topics.STATUS_MESSAGE, lambda text, level="info": messages.append(text))

    def spin(cond, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return cond()

    sidebar = ctx.main.sidebar

    def sidebar_rows():
        out = {}
        for i in range(sidebar.topLevelItemCount()):
            top = sidebar.topLevelItem(i)
            out[top.text(0)] = [top.child(j).text(0) for j in range(top.childCount())]
        return out

    label = os.path.basename(mount_point)
    other_cloud = [d.label for d in ctx.devices.devices if d.section == "cloud"]
    check(("CLOUD" in sidebar_rows()) == bool(other_cloud),
          f"the Cloud header shows only when there is cloud storage (already here: {other_cloud})")
    tab = ctx.commands.active_tab()
    tab.navigate("computer://")
    spin(lambda: not tab._loading)
    os.makedirs(mount_point, exist_ok=True)
    proc = subprocess.Popen([RCLONE, "mount", f"onedrive:{source_dir}", mount_point, "--config", mount_conf],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check(spin(lambda: label in sidebar_rows().get("CLOUD", [])), f"the rclone mount appears under CLOUD: {sidebar_rows().get('CLOUD')}")
        check(any("Connected:" in m and label in m for m in messages), f"status bar says so: {messages[-1:]}")
        check(spin(lambda: any(e.path == mount_point for e in tab.listing.all_entries)),
              "the open Computer view refreshes by itself and lists it")
        entry = next(e for e in tab.listing.all_entries if e.path == mount_point)
        check(entry.extra.get("type_label") == "Cloud storage (OneDrive)", f"type column: {entry.extra.get('type_label')}")
        cloud_id = ctx.categories.auto_sections()["cloud"]
        ctx.categories.hide_live(cloud_id, mount_point)
        check(label not in sidebar_rows().get("CLOUD", []), "Hide from sidebar works")
        ctx.categories.unhide_all(cloud_id)
        places = ctx.categories.all()[0]["id"]
        sidebar._pin_live(mount_point, places)
        check(label in sidebar_rows()["PLACES"], "a live entry can be kept as a permanent shortcut")
        tab.navigate(mount_point)
        spin(lambda: not tab._loading and tab.listing.total >= 1)
        check(any(e.name == "hello.txt" for e in tab.listing.all_entries), "opening it shows the files")
        check(ctx.cloud.root_at(mount_point) is not None, "the folder is known as cloud storage (tab icon)")
        tab.navigate("computer://")
        spin(lambda: not tab._loading)
    finally:
        unmount(mount_point)
        proc.wait(timeout=10)
    check(spin(lambda: label not in sidebar_rows().get("CLOUD", [])), "unmounting removes it from the sidebar")
    check(spin(lambda: not any(e.path == mount_point for e in tab.listing.all_entries)), "…and from the Computer view")
    check(label in sidebar_rows()["PLACES"], "the permanent shortcut stays")
    ctx.devices.stop()
    ctx.runner.shutdown()
    try:
        os.rmdir(mount_point)
    except OSError:
        pass
else:
    print("  (skipped: rclone or FUSE not available)")

shutil.rmtree(tmp, ignore_errors=True)
print("\nDEVICE TESTS PASSED")

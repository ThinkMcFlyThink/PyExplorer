"""Tests for drives that are plugged in but not mounted (services/block_devices.py):
which partitions are shown, their names, how they join the device list, the
status messages, and mounting / unmounting through UDisks2 (udisksctl, faked
here). Run:  python tests/test_block_devices.py"""
import os, sys, tempfile, stat
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from explorer.services import block_devices as bd
from explorer.services.devices import Device, scan_linux, is_unmounted_device, DEVICE_PREFIX
from explorer.services.device_monitor import describe_changes

ESP, MSR, WINRE, LINUX, BASIC = ("c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "e3c9e316-0b5c-4db8-817d-f92df00215ae",
                                 "de94bba4-06d1-4d40-a16a-bfd50179d6ac", "0fc63daf-8483-4772-8e79-3d69d8477de4",
                                 "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")


def part(name, fstype, size, parttype, label=None, partlabel=None, mounts=(None,), rm=False, hot=False, typ="part"):
    return {"name": name, "path": f"/dev/{name}", "type": typ, "fstype": fstype, "label": label, "partlabel": partlabel,
            "size": size, "mountpoints": list(mounts), "rm": rm, "hotplug": hot, "parttype": parttype, "ro": False}


def disk(name, children, rm=False, hot=False):
    d = part(name, None, 0, None, rm=rm, hot=hot, typ="disk")
    d["children"] = children
    return d


LSBLK = {"blockdevices": [
    disk("sda", [part("sda1", "vfat", 536870912, ESP, partlabel="EFI System Partition", mounts=["/boot/efi"]),
                 part("sda2", "ext4", 239526215680, LINUX, mounts=["/"])]),
    disk("sdb", [part("sdb1", "ext4", 1000203091968, LINUX, label="Backup")]),
    disk("sdc", [part("sdc1", "vfat", 104857600, ESP, partlabel="EFI system partition"),
                 part("sdc2", None, 16777216, MSR, partlabel="Microsoft reserved partition"),
                 part("sdc3", "ntfs", 119328730624, BASIC, partlabel="Basic data partition"),
                 part("sdc4", "ntfs", 529530880, WINRE)]),
    disk("sdd", [part("sdd1", "ntfs", 4000768327680, BASIC, label="Films", partlabel="Basic data partition")]),
    disk("sde", [part("sde2", "ext4", 999666221056, LINUX)]),
    disk("sdf", [part("sdf1", "exfat", 64000000000, None, label="STICK")], rm=True, hot=True),
    disk("sdg", [part("sdg1", "swap", 8 * 10 ** 9, "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"),
                 part("sdg2", "crypto_LUKS", 500 * 10 ** 9, LINUX),
                 part("sdg3", "LVM2_member", 100 * 10 ** 9, LINUX)]),
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "fstype": "squashfs", "label": None, "partlabel": None,
     "size": 10 ** 8, "mountpoints": [None], "rm": False, "hotplug": False, "parttype": None, "ro": True},
    {"name": "sr0", "path": "/dev/sr0", "type": "rom", "fstype": "iso9660", "label": "HOLIDAY_DVD", "partlabel": None,
     "size": 4 * 10 ** 9, "mountpoints": [None], "rm": True, "hotplug": False, "parttype": None, "ro": True},
    part("sdh", "ext4", 2 * 10 ** 12, None, label="WholeDisk", typ="disk"),     # a file system on the whole disk
]}

vols = bd.parse_lsblk(LSBLK)
got = {v.device: v for v in vols}
assert sorted(got) == ["/dev/sdb1", "/dev/sdc3", "/dev/sdd1", "/dev/sde2", "/dev/sdf1", "/dev/sdh", "/dev/sr0"], \
    sorted(got)
print("  shown: the data partitions only (no EFI, reserved, recovery, swap, LUKS, LVM, snaps, mounted ones)")
assert got["/dev/sdb1"].label == "Backup" and got["/dev/sdb1"].kind == "drive" and got["/dev/sdb1"].disk == "/dev/sdb"
assert got["/dev/sdc3"].label == "119 GB Volume", got["/dev/sdc3"].label      # generic partition name -> size
assert got["/dev/sdd1"].label == "Films"
assert got["/dev/sde2"].label == "1.0 TB Volume", got["/dev/sde2"].label      # like the desktop names it
assert got["/dev/sdf1"].kind == "removable" and got["/dev/sr0"].kind == "optical" and got["/dev/sr0"].read_only
assert got["/dev/sdh"].disk == "/dev/sdh"
assert bd.parse_lsblk({}) == [] and bd._decimal_size(1500) == "1.5 kB"
print("  names: label, else a meaningful partition name, else '<size> Volume'")

# -- joined into the device list (greyed, mounted when opened)
empty = tempfile.mkdtemp()
gvfs_dir = tempfile.mkdtemp()
mounts = os.path.join(empty, "mounts")
open(mounts, "w").write("/dev/sda2 / ext4 rw 0 0\n")
devs = scan_linux(proc_mounts=mounts, fstab=os.path.join(empty, "none"), gvfs=gvfs_dir, home=empty,
                  rclone_conf=os.path.join(empty, "none"), cloud_detect=lambda **k: [], volumes=lambda: vols)
idle = [d for d in devs if not d.mounted]
assert len(idle) == len(vols) and all(is_unmounted_device(d.path) and d.section == "drives" for d in idle)
backup = next(d for d in idle if d.label == "Backup")
assert backup.path == DEVICE_PREFIX + "/dev/sdb1" and backup.source == "/dev/sdb1"
assert backup.type_label == "Drive (not mounted)" and next(d for d in idle if d.label == "STICK").removable
assert scan_linux(proc_mounts=mounts, fstab=os.path.join(empty, "none"), gvfs=gvfs_dir, home=empty,
                  rclone_conf=os.path.join(empty, "none"), cloud_detect=lambda **k: []) == devs[:1], \
    "no volumes() given: none listed (tests / other callers unchanged)"
print("  listed under Drives as 'not mounted', opened through device://")

# -- status bar messages
mounted = Device("Backup", "/media/me/Backup", "drive", "drives", "", "/dev/sdb1 (ext4)", source="/dev/sdb1")
assert describe_changes([backup], [mounted]) == "Mounted: Backup"
assert describe_changes([mounted], [backup]) == "Unmounted: Backup"
stick = next(d for d in idle if d.label == "STICK")
assert describe_changes([], [stick]) == "Plugged in (not mounted): STICK"
assert describe_changes([mounted], []) == "Removed: Backup"
print("  status bar: Mounted / Unmounted / Plugged in (not mounted) / Removed")

# -- mount / unmount through a fake udisksctl
bindir = tempfile.mkdtemp()
log = os.path.join(bindir, "calls")
script = os.path.join(bindir, "udisksctl")
open(script, "w").write(f"""#!/bin/sh
echo "$@" >> {log}
case "$1 $3" in
  "mount /dev/sdb1") echo "Mounted /dev/sdb1 at /media/me/Backup" ;;
  "mount /dev/sdd1") echo "Error mounting /dev/sdd1: GDBus.Error:org.freedesktop.UDisks2.Error.NotAuthorizedCanObtain: Not authorized to perform operation" >&2; exit 1 ;;
  "unmount /dev/sdb1") echo "Unmounted /dev/sdb1." ;;
  "unmount /dev/sdc3") echo "Error unmounting /dev/sdc3: GDBus.Error:org.freedesktop.UDisks2.Error.DeviceBusy: target is busy" >&2; exit 1 ;;
  *) exit 0 ;;
esac
""")
os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
assert bd.mount("/dev/sdb1") == "/media/me/Backup"
try:
    bd.mount("/dev/sdd1")
    raise AssertionError("should fail")
except bd.MountError as exc:
    assert "Not allowed" in str(exc), exc
bd.unmount("/dev/sdb1")
try:
    bd.unmount("/dev/sdc3")
    raise AssertionError("should fail")
except bd.MountError as exc:
    assert "busy" in str(exc), exc
bd.power_off("/dev/sdf")
calls = open(log).read().splitlines()
assert calls == ["mount --block-device /dev/sdb1", "mount --block-device /dev/sdd1", "unmount --block-device /dev/sdb1",
                 "unmount --block-device /dev/sdc3", "power-off --block-device /dev/sdf"], calls
print("  mount / unmount / eject through udisksctl, with readable errors")

if "--real" in sys.argv:
    for v in bd.unmounted_volumes():
        print("   ", v)
print("ALL OK")

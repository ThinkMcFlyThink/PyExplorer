"""
block_devices - disks and partitions that are plugged in but NOT mounted
(Linux), and mounting / unmounting / ejecting them like Nemo does.

    volumes = unmounted_volumes()          # [Volume(...)], from lsblk
    mount_point = mount("/dev/sdb1")       # -> "/media/me/Data"  (may show a password prompt)
    unmount("/dev/sdb1"); power_off("/dev/sdb")

Listing uses ``lsblk --json`` (part of util-linux: on every Linux). Mounting
goes through UDisks2 (``udisksctl``, or ``gio mount`` as a fallback): the same
service the desktop's file manager uses, so it mounts under /media/<you>/,
honours /etc/fstab, and asks for a password through the desktop's own polkit
dialog when the system requires one (often for internal disks).

What is NOT listed (UDisks/Nemo hide these too): EFI system, Microsoft
reserved and Windows recovery partitions, BIOS boot, swap, RAID/LVM members,
locked encrypted volumes, snap/loop images, and anything without a file system.

No Qt. Blocking - call from worker threads.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

LSBLK_COLUMNS = "NAME,PATH,TYPE,FSTYPE,LABEL,PARTLABEL,SIZE,MOUNTPOINTS,RM,HOTPLUG,PARTTYPE,PKNAME,RO"
TIMEOUT = 8
MOUNT_TIMEOUT = 120                   # (a password prompt may be waiting for the user)

# Partition types that are system plumbing, never something to browse.
HIDDEN_PARTTYPES = {
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",    # EFI system partition
    "e3c9e316-0b5c-4db8-817d-f92df00215ae",    # Microsoft reserved
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac",    # Windows recovery
    "21686148-6449-6e6f-744e-656564454649",    # BIOS boot
    "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",    # Linux swap
    "0xef", "0x27", "0x82", "0x12", "0x84", "0xde",   # MBR: EFI, hidden recovery, swap, OEM diagnostics...
}
HIDDEN_FSTYPES = {"swap", "linux_raid_member", "LVM2_member", "crypto_LUKS", "zfs_member", "bcache",
                  "squashfs", "BitLocker", "ddf_raid_member", "isw_raid_member"}
# Partition names that only say what kind of partition it is - not worth showing as a name.
GENERIC_PARTLABELS = {"basic data partition", "microsoft basic data", "linux filesystem", "linux data",
                      "primary", "logical", "extended", "data", "untitled"}


@dataclass
class Volume:
    device: str           # /dev/sdb1
    label: str            # "TheHardDrive", or "120 GB Volume" like the desktop names it
    fstype: str
    size: int
    kind: str             # drive | removable | optical
    disk: str             # the whole disk (/dev/sdb): what "eject" powers off
    read_only: bool = False


def _decimal_size(n: int) -> str:
    """Disk-maker sizes, as the desktop shows them: 1000204886016 -> '1.0 TB'."""
    size = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB", "PB"):
        if size < 999.5 or unit == "PB":
            return f"{size:.0f} {unit}" if size >= 10 or unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{n} B"


def run_lsblk() -> dict:
    exe = shutil.which("lsblk")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "--json", "--bytes", "--output", LSBLK_COLUMNS], capture_output=True,
                             text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        return json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _mounted(dev: dict) -> bool:
    points = dev.get("mountpoints")
    if points is None and "mountpoint" in dev:              # older lsblk: one MOUNTPOINT column
        points = [dev.get("mountpoint")]
    return any(p for p in (points or []))


def _wanted(dev: dict) -> bool:
    if dev.get("type") not in ("part", "disk", "rom", "crypt", "lvm", "md"):
        return False                                        # loop (snaps), zram...
    fstype = dev.get("fstype") or ""
    if not fstype or fstype in HIDDEN_FSTYPES:
        return False
    if (dev.get("parttype") or "").lower() in HIDDEN_PARTTYPES:
        return False
    return not _mounted(dev)


def _label(dev: dict) -> str:
    label = (dev.get("label") or "").strip()
    if label:
        return label
    partlabel = (dev.get("partlabel") or "").strip()
    if partlabel and partlabel.lower() not in GENERIC_PARTLABELS:
        return partlabel
    return f"{_decimal_size(int(dev.get('size') or 0))} Volume"


def parse_lsblk(data: dict) -> List[Volume]:
    """lsblk's JSON -> the unmounted volumes worth showing (pure: tests feed it made-up JSON)."""
    out: List[Volume] = []

    def walk(devs: list, disk: Optional[dict]) -> None:
        for dev in devs or []:
            top = disk or dev
            if _wanted(dev):
                removable = bool(top.get("rm") or top.get("hotplug") or dev.get("rm") or dev.get("hotplug"))
                kind = "optical" if dev.get("type") == "rom" else "removable" if removable else "drive"
                out.append(Volume(dev.get("path") or f"/dev/{dev['name']}", _label(dev), dev.get("fstype") or "",
                                  int(dev.get("size") or 0), kind, top.get("path") or f"/dev/{top['name']}",
                                  bool(dev.get("ro"))))
            walk(dev.get("children") or [], top)
    walk(data.get("blockdevices") or [], None)
    return out


def unmounted_volumes() -> List[Volume]:
    return parse_lsblk(run_lsblk())


def disk_of(device: str) -> Optional[str]:
    """The whole disk a partition is on (/dev/sdb1 -> /dev/sdb), for "eject"."""
    def find(devs, parent):
        for dev in devs or []:
            if dev.get("path") == device:
                return (parent or dev).get("path")
            hit = find(dev.get("children"), parent or dev)
            if hit:
                return hit
        return None
    return find(run_lsblk().get("blockdevices"), None)


def mount_point_of(device: str) -> Optional[str]:
    """Where ``device`` is mounted now (first mount point), from lsblk."""
    def find(devs):
        for dev in devs or []:
            if dev.get("path") == device:
                points = [p for p in (dev.get("mountpoints") or [dev.get("mountpoint")]) if p]
                return points[0] if points else None
            hit = find(dev.get("children"))
            if hit:
                return hit
        return None
    return find(run_lsblk().get("blockdevices"))


# ---------------------------------------------------------------------------
# Mount / unmount / eject (UDisks2)
# ---------------------------------------------------------------------------
class MountError(OSError):
    pass


def _run(args: List[str], timeout: int = MOUNT_TIMEOUT) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MountError(f"{args[0]} is not installed")
    except subprocess.TimeoutExpired:
        raise MountError("No answer in time (was a password prompt left open?)")
    if out.returncode != 0:
        message = (out.stderr or out.stdout).strip().splitlines()
        text = message[-1] if message else f"{args[0]} failed"
        text = re.sub(r"^Error \w+ [^:]+: ", "", text)          # udisksctl's "Error mounting /dev/sdb1: "
        text = text.replace("GDBus.Error:org.freedesktop.UDisks2.Error.", "")
        if "NotAuthorized" in text or "not authorized" in text.lower():
            text = "Not allowed (the password was cancelled or refused)"
        raise MountError(text)
    return out.stdout


def mount(device: str) -> str:
    """Mount through UDisks2; returns the mount point."""
    if shutil.which("udisksctl"):
        out = _run(["udisksctl", "mount", "--block-device", device])
        found = re.search(r" at (.+?)\.?\s*$", out.strip())
        if found:
            return found.group(1)
    elif shutil.which("gio"):
        _run(["gio", "mount", "--device", device])
    else:
        raise MountError("Can't mount drives: neither udisksctl nor gio is installed")
    point = mount_point_of(device)
    if not point:
        raise MountError("The drive was mounted, but its folder could not be found")
    return point


def unmount(device: str) -> None:
    if shutil.which("udisksctl"):
        _run(["udisksctl", "unmount", "--block-device", device])
    elif shutil.which("gio"):
        point = mount_point_of(device)
        _run(["gio", "mount", "--unmount", point or device])
    else:
        raise MountError("Can't unmount: neither udisksctl nor gio is installed")


def power_off(disk: str) -> None:
    """After unmounting: make a USB disk safe to unplug."""
    if shutil.which("udisksctl"):
        _run(["udisksctl", "power-off", "--block-device", disk])

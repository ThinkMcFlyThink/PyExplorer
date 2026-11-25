"""
devices - everything you can open that isn't an ordinary folder: disks, USB
sticks, phones, network mounts and cloud storage, each sorted into one of the
sidebar's live sections:

    drives   disks and partitions, USB sticks, SD cards, DVDs, phones/cameras
    network  SMB/NFS/SSHFS mounts, shares opened in Nemo (GVFS), rclone SFTP/SMB...
    cloud    rclone OneDrive/Google Drive/..., onedriver, pCloud, Google Drive from
             GNOME Online Accounts, and sync apps' folders (OneDrive client, Dropbox...)

``scan_devices()`` takes a quick snapshot (a few milliseconds). The
DeviceMonitor (services/device_monitor.py) calls it again whenever something
is mounted or unmounted, so the sidebar and the Computer view stay current.

IMPORTANT: nothing here may touch a network mount point itself (no stat,
no listdir). A server that went away can make even "does this exist?" hang
for minutes. We only read the system's mount list and small config files.

Plain Python, no Qt. The Linux functions take file paths as arguments, so the
tests can feed them made-up mount lists, fstab files and config files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..core.platform_info import IS_LINUX, fstab_network_entries, gvfs_root, os_drives, read_mount_specs
from . import block_devices, rclone_config
from .cloud_providers import CloudProviders, CloudRoot, detect_linux, icons_for

SECTIONS = ("drives", "network", "cloud")


@dataclass
class Device:
    label: str
    path: str                       # what opening it navigates to
    kind: str                       # icon hint: drive | removable | optical | phone | network | cloud
    section: str                    # drives | network | cloud
    provider: str = ""              # "OneDrive", "SMB", "rclone"... (cloud + network)
    detail: str = ""                # tooltip: where it comes from, e.g. "rclone mount of onedrive:"
    mounted: bool = True            # False for /etc/fstab shares that aren't mounted yet
    running: Optional[bool] = None  # cloud sync app running? (None = not applicable)
    cloud_root: Optional[CloudRoot] = field(default=None, compare=False, repr=False)
    source: str = ""                # rclone mounts: the remote ("onedrive:Docs"); disks: the device (/dev/sdb1)
    removable: bool = False         # USB sticks / disks: "Eject" powers them off after unmounting

    def key(self) -> Tuple:
        """Everything the UI shows - two snapshots with equal keys look identical."""
        return (self.label, self.path, self.kind, self.section, self.provider,
                self.detail, self.mounted, self.running)

    @property
    def type_label(self) -> str:
        """For the Type column of the Computer view."""
        if self.section == "cloud":
            return f"Cloud storage ({self.provider})" if self.provider else "Cloud storage"
        if self.section == "network":
            return f"Network drive ({self.provider})" if self.provider else "Network drive"
        kind = {"removable": "Removable drive", "optical": "Disc", "phone": "Phone / camera"}.get(self.kind, "Drive")
        return kind if self.mounted else f"{kind} (not mounted)"


DEVICE_PREFIX = "device://"          # path of a disk that isn't mounted yet: opening it mounts it


def is_unmounted_device(path: str) -> bool:
    return path.startswith(DEVICE_PREFIX)


# ---------------------------------------------------------------------------
# Linux: sorting the mount list
# ---------------------------------------------------------------------------
# Kernel/system filesystems that are never interesting to browse.
VIRTUAL_FS = {
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "securityfs", "pstore",
    "bpf", "debugfs", "tracefs", "mqueue", "hugetlbfs", "configfs", "fusectl", "autofs", "overlay",
    "squashfs", "nsfs", "efivarfs", "binfmt_misc", "ramfs", "rpc_pipefs", "nfsd", "selinuxfs",
    "fuse.portal", "fuse.gvfsd-fuse", "fuse.lxcfs", "fuse.snapfuse", "fuse.xdg-document-portal",
}
# Places where the system keeps its own mounts.
SYSTEM_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/boot", "/efi", "/var", "/usr", "/tmp",
                   "/snap", "/recovery", "/etc", "/lib", "/bin", "/sbin")
USER_MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/", "/mnt")
# Filesystem type -> (section, provider). fuse.rclone is looked up in rclone.conf instead.
NETWORK_TYPES: Dict[str, Tuple[str, str]] = {
    "cifs": ("network", "SMB"), "smb3": ("network", "SMB"), "smbfs": ("network", "SMB"),
    "nfs": ("network", "NFS"), "nfs4": ("network", "NFS"), "fuse.sshfs": ("network", "SSHFS"),
    "sshfs": ("network", "SSHFS"), "fuse.curlftpfs": ("network", "FTP"), "davfs": ("network", "WebDAV"),
    "fuse.davfs2": ("network", "WebDAV"), "9p": ("network", "Shared folder"),
    "virtiofs": ("network", "Shared folder"), "vboxsf": ("network", "VirtualBox shared folder"),
    "glusterfs": ("network", "GlusterFS"), "fuse.glusterfs": ("network", "GlusterFS"),
    "ceph": ("network", "Ceph"), "fuse.ceph": ("network", "Ceph"), "afpfs": ("network", "AFP"),
    "fuse.afpfs": ("network", "AFP"),
    "fuse.onedriver": ("cloud", "OneDrive"), "fuse.s3fs": ("cloud", "S3"), "fuse.goofys": ("cloud", "S3"),
    "fuse.gcsfuse": ("cloud", "Google Cloud Storage"),
    "fuse.google-drive-ocamlfuse": ("cloud", "Google Drive"), "fuse.pcloud": ("cloud", "pCloud"),
}
# GVFS folder names start with the kind of location: "smb-share:server=...".
GVFS_KINDS: Dict[str, Tuple[str, str, str]] = {        # kind -> (section, icon kind, provider)
    "smb-share": ("network", "network", "SMB"), "smb-server": ("network", "network", "SMB"),
    "sftp": ("network", "network", "SFTP"), "ftp": ("network", "network", "FTP"),
    "ftps": ("network", "network", "FTPS"), "dav": ("network", "network", "WebDAV"),
    "davs": ("network", "network", "WebDAV"), "nfs": ("network", "network", "NFS"),
    "afp-volume": ("network", "network", "AFP"), "google-drive": ("cloud", "cloud", "Google Drive"),
    "onedrive": ("cloud", "cloud", "OneDrive"), "mtp": ("drives", "phone", "Phone"),
    "gphoto2": ("drives", "phone", "Camera"), "afc": ("drives", "phone", "iPhone"),
}


def _is_system(mount_point: str) -> bool:
    if mount_point.startswith("/run/media/"):
        return False
    return any(mount_point == p or mount_point.startswith(p + "/") for p in SYSTEM_PREFIXES)


def _is_user_place(mount_point: str, home: str) -> bool:
    return (mount_point.startswith(USER_MOUNT_PREFIXES) or mount_point == home
            or mount_point.startswith(home.rstrip("/") + "/"))


def block_kind(device: str, sys_class_block: str = "/sys/class/block") -> Optional[str]:
    """"removable" for USB sticks/SD cards, "optical" for DVD drives, "drive" for
    internal disks, or None if the kernel's /sys can't tell us (e.g. in tests)."""
    name = os.path.basename(os.path.realpath(device)) if device.startswith("/dev/") else ""
    if not name:
        return None
    if name.startswith("sr"):
        return "optical"
    node = os.path.join(sys_class_block, name)
    real = os.path.realpath(node)
    if not os.path.exists(real):
        return None
    disk = os.path.dirname(real) if os.path.exists(os.path.join(real, "partition")) else real
    try:
        with open(os.path.join(disk, "removable"), encoding="ascii") as fh:
            removable = fh.read().strip() == "1"
    except OSError:
        removable = False
    return "removable" if removable or "/usb" in real else "drive"


def _remote_label(source: str) -> str:
    """"//truenas.local/media" -> "media on truenas.local", "me@pi:/srv" -> "srv on pi"."""
    if source.startswith("//"):
        server, _, share = source[2:].partition("/")
        return f"{share} on {server}" if share else server
    host, sep, path = source.rpartition(":")
    if sep and host:
        host = host.rpartition("@")[2]
        name = os.path.basename(path.rstrip("/"))
        return f"{name} on {host}" if name else host
    return source


def classify_mount(source: str, mount_point: str, fstype: str, home: str,
                   remotes: Dict[str, Dict[str, str]],
                   sys_class_block: str = "/sys/class/block") -> Optional[Device]:
    """One line of the mount list -> a Device, or None if it isn't worth showing."""
    if fstype in VIRTUAL_FS or mount_point == "/":
        return None
    if _is_system(mount_point) and not _is_user_place(mount_point, home):   # inside home is always fine
        return None
    name = os.path.basename(mount_point.rstrip("/")) or mount_point

    if fstype == "fuse.rclone" or fstype == "rclone":
        info = rclone_config.describe_remote(source, remotes)
        kind = {"cloud": "cloud", "network": "network"}.get(info.section, "drive")
        provider = info.provider
        return Device(name, mount_point, kind, info.section, provider,
                      f"rclone mount of {source} ({provider})", source=source)
    known = NETWORK_TYPES.get(fstype)
    if known is None and fstype.startswith("fuse.") and "pcloud" in fstype.lower():
        known = ("cloud", "pCloud")
    if known is not None:
        section, provider = known
        detail = f"{source} ({provider})" if section == "network" else f"{provider} ({fstype})"
        return Device(name, mount_point, "cloud" if section == "cloud" else "network", section,
                      provider, detail)

    if source.startswith("/dev/"):                  # a disk partition, USB stick, DVD, disc image...
        kind = block_kind(source, sys_class_block)
        if kind is None:                            # no /sys info: guess from where it is mounted
            kind = "removable" if mount_point.startswith(("/media/", "/run/media/")) else "drive"
        label = mount_point if mount_point == "/home" else name
        return Device(label, mount_point, kind, "drives", "", f"{source} ({fstype})", source=source,
                      removable=kind in ("removable", "optical"))

    if _is_user_place(mount_point, home):           # mergerfs, gocryptfs, ZFS datasets in /mnt...
        return Device(name, mount_point, "drive", "drives", "", f"{source} ({fstype})")
    return None


def gvfs_devices(root: Optional[str]) -> List[Device]:
    """Locations the desktop (Nemo/Nautilus) mounted through GVFS: network
    shares, phones and cameras, Google Drive from GNOME Online Accounts."""
    if not root:
        return []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names:
        kind, _, params = name.partition(":")
        info = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        section, icon_kind, provider = GVFS_KINDS.get(kind, ("network", "network", kind.upper()))
        server = info.get("server") or info.get("host") or kind
        if info.get("share"):
            label = f"{info['share']} on {server}"
        elif kind in ("google-drive", "onedrive"):
            user = info.get("user", "")
            label = f"{provider} ({user}@{info['host']})" if user and info.get("host") else provider
        elif section == "drives":                     # phones: "SAMSUNG_Android_R58M..." -> "SAMSUNG Android R58M"
            label = " ".join(server.replace("_", " ").split()[:3]) or provider
        else:
            label = f"{server} ({provider})"
        out.append(Device(label, os.path.join(root, name), icon_kind, section, provider,
                          f"Opened by the desktop (GVFS): {kind}"))
    return out


def scan_linux(proc_mounts: str = "/proc/self/mounts", fstab: str = "/etc/fstab",
               gvfs: Optional[str] = None, home: Optional[str] = None,
               rclone_conf: Optional[str] = None, sys_class_block: str = "/sys/class/block",
               cloud_detect: Callable[..., List[CloudRoot]] = detect_linux,
               volumes: Optional[Callable[[], list]] = None) -> List[Device]:
    """``volumes()`` -> block_devices.Volume list of disks that are plugged in but not
    mounted (scan_devices passes block_devices.unmounted_volumes; tests pass their own)."""
    home = home or os.path.expanduser("~")
    remotes = rclone_config.read_remotes(rclone_conf)
    devices = [Device("Root (/)", "/", "drive", "drives", "", "The system disk")]
    seen = {"/"}
    specs = read_mount_specs(proc_mounts)
    for source, mount_point, fstype in specs:
        if mount_point in seen:                     # bind mounts / stacked mounts: show once
            continue
        device = classify_mount(source, mount_point, fstype, home, remotes, sys_class_block)
        if device is not None:
            seen.add(mount_point)
            devices.append(device)
    for device in gvfs_devices(gvfs if gvfs is not None else gvfs_root()):
        if device.path not in seen:
            seen.add(device.path)
            devices.append(device)
    for entry in fstab_network_entries(fstab, proc_mounts):
        if not entry["mounted"] and entry["mountpoint"] not in seen:
            seen.add(entry["mountpoint"])
            name = os.path.basename(entry["mountpoint"]) or entry["mountpoint"]
            devices.append(Device(f"{name} (not mounted)", entry["mountpoint"], "network", "network",
                                  NETWORK_TYPES.get(entry["fstype"], ("", ""))[1],
                                  f"{entry['spec']} - in /etc/fstab, opens when you click it", mounted=False))
    # Disks and partitions that are there but not mounted: shown greyed, mounted when opened.
    for vol in (volumes() if volumes is not None else []):
        path = DEVICE_PREFIX + vol.device
        if path not in seen:
            seen.add(path)
            devices.append(Device(vol.label, path, vol.kind, "drives", "",
                                  f"{vol.device} ({vol.fstype}) - not mounted", mounted=False,
                                  source=vol.device, removable=vol.kind in ("removable", "optical")))
    # Sync apps' folders (OneDrive client, Dropbox...). Mount points are passed
    # as "skip" so the detector never touches them.
    for root in cloud_detect(home=home, skip=[mp for _, mp, _ in specs]):
        if root.path in seen:
            continue
        seen.add(root.path)
        devices.append(Device(root.label, root.path, "cloud", "cloud", root.provider, root.detail,
                              running=root.running, cloud_root=root))
    return devices


# ---------------------------------------------------------------------------
# Every OS
# ---------------------------------------------------------------------------
def scan_devices() -> List[Device]:
    """A snapshot of all drives, network mounts and cloud folders right now."""
    if IS_LINUX:
        devices = scan_linux(volumes=block_devices.unmounted_volumes)
    else:
        devices = []
        for label, path, kind in os_drives():
            section = "network" if kind == "network" else "drives"
            devices.append(Device(label, path, kind, section, "SMB" if kind == "network" else ""))
        seen = {os.path.normcase(d.path) for d in devices}
        for root in CloudProviders.detect():           # Windows: SyncRootManager; macOS: CloudStorage
            if os.path.normcase(root.path) not in seen:
                devices.append(Device(root.label, root.path, "cloud", "cloud", root.provider,
                                      root.detail, cloud_root=root))
    for device in devices:                             # give mounted cloud storage a CloudRoot too
        if device.section == "cloud" and device.cloud_root is None:
            device.cloud_root = CloudRoot(device.provider or "Cloud", device.label, device.path, None,
                                          can_pin=False, reports_local=False,
                                          icon_names=icons_for(device.provider), detail=device.detail)
    return devices


def cloud_roots(devices: List[Device]) -> List[CloudRoot]:
    return [d.cloud_root for d in devices if d.cloud_root is not None]


def snapshot_key(devices: List[Device]) -> Tuple:
    return tuple(d.key() for d in devices)


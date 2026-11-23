"""
Cloud file states (OneDrive, Dropbox, Google Drive, iCloud...).

Sync apps keep "placeholder" files on disk: the name and size are there, but
the content stays in the cloud until something reads the file. The OS tells
us which is which through file attributes that come FREE with every folder
listing, so showing the state costs no speed at all.

    Windows: file attributes (Files On-Demand / Cloud Files API)
    macOS:   the "dataless" flag (File Provider)

This module is plain Python: it only turns those numbers into a state.
Finding which folders belong to which cloud app is services/cloud_providers.py.
"""
from __future__ import annotations

# States (stored in FileEntry.cloud)
NONE = 0        # not a cloud file (or we can't tell)
ONLINE = 1      # placeholder: content is only in the cloud, opening it downloads it
LOCAL = 2       # downloaded: available on this device (may be freed up again)
PINNED = 3      # "always keep on this device"

LABELS = {
    ONLINE: "Cloud only (not downloaded)",
    LOCAL: "Available on this device",
    PINNED: "Always kept on this device",
}
SHORT_LABELS = {ONLINE: "Cloud only", LOCAL: "On this device", PINNED: "Always on device"}

# Windows file attribute bits (see Microsoft's "File Attribute Constants").
WIN_OFFLINE = 0x00001000
WIN_RECALL_ON_OPEN = 0x00040000
WIN_PINNED = 0x00080000
WIN_UNPINNED = 0x00100000
WIN_RECALL_ON_DATA_ACCESS = 0x00400000
WIN_IN_CLOUD = WIN_OFFLINE | WIN_RECALL_ON_OPEN | WIN_RECALL_ON_DATA_ACCESS

# macOS: st_flags bit for files whose data lives elsewhere (SF_DATALESS).
MAC_DATALESS = 0x40000000


def state_from_attributes(win_attributes: int = 0, mac_flags: int = 0) -> int:
    """The state the OS reports for one file. LOCAL is decided later
    (it depends on the file being inside a cloud folder at all)."""
    if win_attributes:
        if win_attributes & WIN_IN_CLOUD:
            return ONLINE
        if win_attributes & WIN_PINNED:
            return PINNED
        return NONE
    if mac_flags & MAC_DATALESS:
        return ONLINE
    return NONE


def state_from_stat(st) -> int:
    """Same, straight from an os.stat()/DirEntry.stat() result."""
    return state_from_attributes(getattr(st, "st_file_attributes", 0) or 0, getattr(st, "st_flags", 0) or 0)


def effective_state(raw_state: int, in_cloud_folder: bool, reports_local: bool = True) -> int:
    """What to show: the OS state, or LOCAL for ordinary files inside a cloud
    folder (when that cloud app reports states at all)."""
    if raw_state:
        return raw_state
    return LOCAL if (in_cloud_folder and reports_local) else NONE

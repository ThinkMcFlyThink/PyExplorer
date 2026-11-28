"""
The Settings tab, described as data: pages -> groups -> settings.

Plain Python (no Qt), so it can be tested and so adding a setting is one
line here (plus its default in core/settings.py). The Settings tab
(ui/settings/) draws whatever is listed: a switch, a drop-down, a number
box... chosen by ``kind``:

    bool        a switch                     int       a number box (minimum..maximum)
    choice      a drop-down of ``options``   text      a text field (saved when you leave it)
    font        installed fonts              monofont  installed fixed-width fonts
    theme       the colour themes            paths     a list of folders, one per line
    columns     tick boxes for the Details view's columns
    action      a button that runs a command (``apply``) - no value of its own

``apply`` names a method of ``commands.Commands`` that makes a value take
effect straight away (for example "apply_theme"); settings without one are
read when needed, or by whoever listens to SETTINGS_CHANGED. ``source`` lets
the interface supply a drop-down's choices (the themes, the view modes...).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple

KINDS = ("bool", "int", "choice", "text", "font", "monofont", "theme", "paths", "columns", "action")


@dataclass(frozen=True)
class Setting:
    key: str                                    # dotted settings key ("" for an action)
    label: str
    help: str = ""
    kind: str = "bool"
    options: Tuple[Tuple[Any, str], ...] = ()   # choice: (value, label)
    source: str = ""                            # choice: the interface supplies the options
    minimum: int = 0
    maximum: int = 100
    step: int = 1
    suffix: str = ""
    special: str = ""                           # int: what to show at the minimum ("System default")
    editable: bool = False                      # choice: you may type a value of your own
    apply: str = ""                             # Commands method: take effect now / run (action)
    restart: bool = False                       # takes effect when PyExplorer starts again
    button: str = ""                            # action: the button's text
    danger: bool = False                        # action: destructive
    platforms: Tuple[str, ...] = ()             # only on these: "linux" | "windows" | "mac"
    keywords: str = ""                          # more words the search box should find it by


@dataclass(frozen=True)
class Group:
    title: str
    settings: Tuple[Setting, ...]
    note: str = ""


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    icon: str                                   # a shape from ui/symbols.py
    summary: str
    groups: Tuple[Group, ...] = ()
    custom: str = ""                            # a ready-made editor after the groups (ui/settings/pages.py)
    keywords: str = ""
    extra: dict = field(default_factory=dict)


def _platform() -> str:
    return "windows" if sys.platform.startswith("win") else "mac" if sys.platform == "darwin" else "linux"


S = Setting
G = Group

PAGES: List[Page] = [
    Page("appearance", "Appearance", "appearance", "Colours, fonts and icons.", groups=(
        G("Theme", (
            S("appearance.theme", "Colour theme", "System follows your desktop's own look. The others are modelled "
              "on popular VS Code themes, in light and dark.", kind="theme", apply="apply_theme",
              keywords="dark light mode colours colors"),
            S("appearance.icon_style", "Toolbar and menu icons", "PyExplorer's line icons look the same on every "
              "system and follow the theme. Your desktop's icon theme is used where it has the icon.", kind="choice",
              options=(("line", "PyExplorer line icons"), ("system", "Your desktop's icon theme")),
              apply="apply_theme"),
            S("appearance.system_icons", "File icons from your system", "Off: PyExplorer draws every file icon "
              "itself, coloured by the kind of file."),
        )),
        G("Fonts", (
            S("appearance.font_family", "Interface font", "Menus, sidebar, tabs, file lists and dialogs.",
              kind="font", apply="apply_theme", keywords="typeface"),
            S("appearance.font_size", "Font size", "For the interface and the terminal.", kind="int", minimum=0,
              maximum=28, suffix=" pt", special="System default", apply="apply_theme", keywords="text bigger smaller"),
        )),
        G("Window", (
            S("appearance.show_toolbar", "Toolbar", "The row of buttons at the top. Right-click it to change it.",
              apply="apply_chrome"),
            S("appearance.show_statusbar", "Status bar", "Item counts, free space and the zoom slider at the bottom.",
              apply="apply_chrome"),
        )),
    )),
    Page("toolbar", "Toolbar", "toolbar", "Choose what the toolbar shows, and how.", groups=(
        G("Style", (
            S("toolbar.style", "Labels", "Menu buttons like New and Sort keep their labels unless you choose "
              "icons only.", kind="choice", options=(("auto", "Icons, with labels on menu buttons"),
                                                      ("icons", "Icons only"), ("text_beside", "Icons and labels"),
                                                      ("text_under", "Labels under icons"))),
            S("toolbar.icon_size", "Icon size", "", kind="choice",
              options=((16, "Small"), (18, "Medium"), (22, "Large"))),
        )),
    ), custom="toolbar", keywords="buttons customize customise layout"),
    Page("views", "Views", "view-details", "How folders and files are shown.", groups=(
        G("Layout", (
            S("view.mode", "View", "Used by every tab. Ctrl+1 … Ctrl+4 switch it.", kind="choice", source="view_modes",
              apply="apply_view_mode"),
            S("view.visible_columns", "Columns", "In the Details and Tree views. Right-click a column header to "
              "change them there.", kind="columns"),
            S("view.alternate_rows", "Striped rows", "Every other row in the Details and Tree views is slightly "
              "darker, to help your eye follow a row.", keywords="alternating zebra"),
            S("view.list_cell_width", "Column width in List view", "", kind="int", minimum=120, maximum=600, step=10,
              suffix=" px"),
            S("view.thumbnails", "Image thumbnails", "Pictures show a preview in the Icons view (large sizes)."),
        )),
        G("Sorting", (
            S("view.sort_key", "Sort by", "Or click a column header.", kind="choice",
              options=(("name", "Name"), ("size", "Size"), ("type", "Type"), ("modified", "Date modified")),
              apply="apply_sort"),
            S("view.sort_reverse", "Descending", "Z to A, biggest first, newest first.", apply="apply_sort"),
            S("view.folders_first", "Folders first", "Folders above files, whatever the order.", apply="apply_sort"),
        )),
        G("Files", (
            S("view.show_hidden", "Show hidden files", "Files and folders whose names start with a dot (or are "
              "marked hidden). Ctrl+Shift+H.", apply="apply_show_hidden", keywords="dotfiles"),
        )),
        G("Formats", (
            S("view.date_format", "Dates", "Pick one, or type your own pattern (strftime codes such as %d %m %Y).",
              kind="choice", source="date_formats", editable=True, keywords="time modified"),
            S("view.size_units", "File sizes", "", kind="choice",
              options=(("binary", "1 KB = 1,024 bytes  (like Windows)"),
                       ("decimal", "1 kB = 1,000 bytes  (like macOS, GNOME and KDE)"),
                       ("iec", "1 KiB = 1,024 bytes  (IEC units)")), keywords="kilobytes megabytes units"),
        )),
    )),
    Page("behaviour", "Behaviour", "pointer", "Clicking, typing, tabs and deleting.", groups=(
        G("Opening", (
            S("behaviour.single_click_open", "Open items with a single click", "Like a web page. Off: double-click."),
            S("behaviour.type_to_filter", "Typing filters the folder", "Start typing in a file list to show only "
              "what matches. Off: typing jumps to the first match.", keywords="instant filter"),
        )),
        G("Tabs", (
            S("behaviour.new_tab_position", "New tabs open", "", kind="choice",
              options=(("next", "Next to the current tab"), ("end", "At the end"))),
            S("behaviour.new_tab_folder", "A new tab shows", "", kind="choice",
              options=(("current", "The current folder"), ("home", "Your home folder"))),
            S("behaviour.restore_session", "Reopen your tabs at start-up", "Off: PyExplorer starts in your home "
              "folder (last time's tabs can still be reopened with Ctrl+Shift+T).", keywords="session startup"),
        )),
        G("Deleting", (
            S("behaviour.use_trash", "Delete moves to the trash", "Off: Delete removes files for good. Network files "
              "are always deleted for good.", keywords="recycle bin"),
            S("behaviour.confirm_delete", "Ask before moving to the trash", ""),
        )),
        G("Keeping folders up to date", (
            S("behaviour.watch_folders", "Refresh when files change", "Open folders update by themselves when "
              "another program changes them.", keywords="auto refresh watch"),
            S("behaviour.watch_interval_ms", "Check for changes every", "Shorter is quicker to notice, longer is "
              "lighter on network shares.", kind="int", minimum=250, maximum=10000, step=250, suffix=" ms"),
        )),
    )),
    Page("sidebar", "Sidebar", "sidebar", "Your places, drives, network and cloud storage. Right-click the "
         "sidebar to add, rename and arrange them.", groups=(
            G("Sidebar", (
                S("appearance.show_sidebar", "Show the sidebar", "F9.", apply="apply_chrome"),
                S("appearance.drive_usage", "How full each drive is", "A thin bar under each drive, in the sidebar "
                  "and the Computer view. Point at a drive to see the numbers.", keywords="usage capacity free space"),
            )),
         )),
    Page("terminal", "Terminal", "terminal", "The terminal at the bottom of the window (F4).", groups=(
        G("Terminal", (
            S("terminal.sync_cd", "Follow the current folder", "The terminal changes to each folder you open."),
            S("appearance.terminal_font", "Font", "Only fixed-width fonts are listed.", kind="monofont",
              apply="apply_theme"),
            S("terminal.scrollback", "Scroll-back", "Lines you can scroll back to.", kind="int", minimum=100,
              maximum=100000, step=500, suffix=" lines", restart=True),
            S("terminal.shell", "Shell", "Empty: your login shell (cmd.exe on Windows). Used when the terminal "
              "starts or restarts.", kind="text", keywords="bash zsh fish powershell"),
        )),
        G("Other terminals", (
            S("terminal.external", "External terminal command", "For “Open terminal here” (Ctrl+Alt+T). Empty: found "
              "automatically. The folder is passed as the working directory.", kind="text"),
        )),
    )),
    Page("info", "Info panel", "info", "Details about the selected item, on the right of the window (Alt+P).",
         keywords="properties metadata details preview cover exif mediainfo ffprobe", groups=(
        G("Info panel", (
            S("info_panel.previews", "Pictures", "Book and album covers, image thumbnails, video frames and the first "
              "page of PDFs.", keywords="thumbnail cover"),
            S("info_panel.video_frames", "Frames from videos", "A picture from 10% into each video. Needs FFmpeg.",
              keywords="poster thumbnail ffmpeg"),
            S("info_panel.text_lines", "Text preview", "The first lines of text and code files.", kind="int",
              minimum=0, maximum=200, step=10, suffix=" lines", special="Off"),
         ), note="Free helper programs add more detail when installed: FFmpeg or MediaInfo (video and audio), "
                 "7-Zip (7z, RAR, ISO…), poppler-utils (PDF) and the Python package Pillow (camera details). "
                 "Nothing is read while the panel is closed."),
    )),
    Page("network", "Network", "server", "Opening files on shares and servers, and the logins PyExplorer "
         "remembers.", groups=(
            G("Files on network shares", (
                S("network.open_files", "Opening a file", "Most apps can play or edit a file straight from the share.",
                  kind="choice", options=(("stream", "Open it directly - the app streams it (recommended)"),
                                          ("download", "Download a copy first"))),
                S("network.mount_shares", "Mount SMB shares like the system does", "So every app can open their "
                  "files with a normal path, as with Nemo or Nautilus.", platforms=("linux",)),
            )),
         ), custom="logins", keywords="password keyring smb sftp ftp"),
    Page("search", "Search", "search", "The Search tab (Ctrl+F) and its index.", groups=(
        G("Searching", (
            S("search.live", "Search as you type", "Off: press Enter to search."),
            S("search.max_results", "Show at most", "A search stops listing results after this many.", kind="int",
              minimum=1000, maximum=5_000_000, step=10_000, suffix=" results"),
        )),
        G("Index", (
            S("search.refresh_minutes", "Re-read the disk after", "An older index still answers straight away and is "
              "brought up to date in the background.", kind="int", minimum=0, maximum=1440, step=5, suffix=" min",
              special="Never"),
            S("search.index_network", "Include network and cloud drives in “Whole computer”", "They can be slow "
              "to read."),
            S("search.skip_folders", "Never index", "Folders to leave out, one per line.", kind="paths",
              keywords="exclude ignore"),
            S("search.workers", "Parallel readers for network folders", "", kind="int", minimum=0, maximum=32,
              special="Automatic"),
            S("search.max_index_entries", "Index size limit", "About 75 bytes of memory per item.", kind="int",
              minimum=100_000, maximum=50_000_000, step=100_000, suffix=" items"),
            S("", "Update the index", "Re-read every indexed place now, in the background.", kind="action",
              button="Update now", apply="update_all_search_indexes"),
        )),
    )),
    Page("transfers", "Transfers", "transfer", "Copying and moving files (Ctrl+J).", groups=(
        G("Copying", (
            S("transfers.engine", "Copy and move with", "", kind="choice",
              options=(("pyexplorer", "PyExplorer's transfer window (verified, per-file results)"),
                       ("simple", "The simple copier (status bar only, no verification)"))),
            S("transfers.verify", "Verify every file", "Each copy is read back and compared with a checksum of the "
              "original.", keywords="checksum"),
            S("transfers.algorithm", "Checksum", "", kind="choice",
              options=(("blake2b", "BLAKE2b (fast, built in)"), ("xxh3", "xxHash3 (fastest; needs the xxhash package)"),
                       ("md5", "MD5"), ("sha1", "SHA-1"), ("sha256", "SHA-256")), keywords="hash"),
            S("transfers.verify_from_disk", "Read the copy back from the disk itself", "Not from memory: catches "
              "faulty drives (local drives only)."),
        )),
        G("Transfer window", (
            S("transfers.show_in", "Show transfers in", "", kind="choice",
              options=(("window", "A window of their own"), ("tab", "A tab in the main window"))),
            S("transfers.close_when_done", "Close the window when everything went fine", ""),
            S("transfers.show_files", "List every file", "With a ✓ or ✗ for each."),
            S("transfers.keep_log", "Keep the list of transfers after closing", "Saved in transfers.json."),
        )),
    )),
    Page("history", "History", "history", "What you did to your files, and Undo (Ctrl+H).", groups=(
        G("History", (
            S("history.keep", "Keep the history after closing", "Off: it is forgotten when PyExplorer closes."),
            S("", "Clear the history", "Your files are not touched - only the list is cleared.", kind="action",
              button="Clear history…", apply="clear_history"),
        )),
    )),
    Page("keyboard", "Keyboard", "keyboard", "Every command and its shortcut. Double-click a shortcut to change "
         "it.", custom="keyboard", keywords="shortcuts keys hotkeys bindings"),
    Page("advanced", "Advanced", "sliders", "Your settings file, and starting over.", groups=(
        G("Settings file", (
            S("", "Settings folder", "Every setting is plain JSON in this folder.", kind="action",
              button="Open folder", apply="open_config_folder", keywords="config json"),
            S("", "settings.json", "Edit it by hand. Changes are read when PyExplorer starts.", kind="action",
              button="Open in editor", apply="open_settings_file", keywords="config json"),
            S("", "Export settings", "Save them to a file, e.g. for another computer.", kind="action",
              button="Export…", apply="export_settings", keywords="backup"),
            S("", "Import settings", "Take them from an exported file.", kind="action", button="Import…",
              apply="import_settings", keywords="restore"),
        )),
        G("Start over", (
            S("", "Reset all settings", "Theme, fonts, toolbar, views, shortcuts… back to the defaults. Your "
              "sidebar, connections, history and tabs are kept.", kind="action", button="Reset…",
              apply="reset_settings", danger=True, keywords="defaults factory"),
        )),
    )),
]

# Settings that are deliberately NOT on any page: remembered state, not preferences.
INTERNAL = {
    "view.zoom", "view.columns", "window.geometry", "window.state", "window.split_sizes", "terminal.visible",
    "info_panel.visible", "info_panel.collapsed",
    "behaviour.last_open_with", "transfers.window_geometry", "shortcuts", "toolbar.items",
}


def pages(platform: Optional[str] = None) -> List[Page]:
    """The pages, without the settings that don't apply to this system."""
    platform = platform or _platform()
    out = []
    for page in PAGES:
        groups = tuple(Group(g.title, tuple(s for s in g.settings if not s.platforms or platform in s.platforms),
                             g.note) for g in page.groups)
        out.append(Page(page.id, page.title, page.icon, page.summary,
                        tuple(g for g in groups if g.settings), page.custom, page.keywords, page.extra))
    return out


def page(page_id: str) -> Optional[Page]:
    return next((p for p in PAGES if p.id == page_id), None)


def all_settings() -> Iterator[Tuple[Page, Group, Setting]]:
    for p in PAGES:
        for g in p.groups:
            for s in g.settings:
                yield p, g, s


def find(key: str) -> Optional[Setting]:
    return next((s for _p, _g, s in all_settings() if s.key == key), None)


def words(text: str) -> List[str]:
    return [w for w in text.lower().replace("…", " ").split() if w]


def matches(query: str, *texts: str) -> bool:
    """Every word of ``query`` appears somewhere in ``texts`` (case-insensitive)."""
    haystack = " ".join(t for t in texts if t).lower()
    return all(w in haystack for w in words(query))


def setting_matches(query: str, p: Page, g: Group, s: Setting) -> bool:
    return matches(query, s.label, s.help, s.key.replace(".", " ").replace("_", " "), s.keywords, s.button,
                   g.title, p.title)

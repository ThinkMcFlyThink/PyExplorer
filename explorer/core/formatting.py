"""Small helpers for turning numbers into human-friendly text.

How sizes and dates look is chosen once with ``configure()`` (Settings ->
Views -> Formats; app.py calls it at start-up and whenever those settings
change), so the functions themselves stay as fast as before - they run for
every visible cell of every file list.
"""
from __future__ import annotations

import time
from datetime import datetime

# size units: (divisor, unit names)
SIZE_UNITS = {
    "binary": (1024, ["B", "KB", "MB", "GB", "TB", "PB"]),        # Windows Explorer's way
    "decimal": (1000, ["B", "kB", "MB", "GB", "TB", "PB"]),       # macOS / GNOME / KDE (SI)
    "iec": (1024, ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]),      # exact IEC names
}
RELATIVE = "relative"                     # "Today 14:03" / "Yesterday 09:12" / "2024-05-01 09:12"
DATE_PRESETS = [RELATIVE, "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%d.%m.%Y %H:%M",
                "%d %b %Y %H:%M", "%Y-%m-%d %H:%M:%S"]

_divisor, _units = SIZE_UNITS["binary"]
_date_pattern = RELATIVE


def configure(size_units: str = "binary", date_format: str = RELATIVE) -> None:
    """Choose how human_size / human_time write things (unknown values -> the defaults)."""
    global _divisor, _units, _date_pattern
    _divisor, _units = SIZE_UNITS.get(size_units, SIZE_UNITS["binary"])
    _date_pattern = date_format if (date_format == RELATIVE or "%" in str(date_format or "")) else RELATIVE


def human_size(num_bytes: float) -> str:
    """1536 -> '1.5 KB'"""
    size = float(num_bytes)
    for unit in _units:
        if size < _divisor or unit == _units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
        size /= _divisor
    return f"{size:.1f} {_units[-1]}"


def human_time(timestamp: float) -> str:
    """Seconds-since-epoch -> 'Today 14:03' / '2024-05-01 09:12' (or the chosen pattern)."""
    if not timestamp:
        return ""
    try:
        dt = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return ""
    if _date_pattern != RELATIVE:
        try:
            return dt.strftime(_date_pattern)
        except ValueError:
            return dt.strftime("%Y-%m-%d %H:%M")
    today = datetime.now().date()
    if dt.date() == today:
        return "Today " + dt.strftime("%H:%M")
    if (today - dt.date()).days == 1:
        return "Yesterday " + dt.strftime("%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def example_time(pattern: str) -> str:
    """What ``pattern`` makes of right now - for the Settings tab's choices."""
    if pattern == RELATIVE:
        return "Today " + datetime.now().strftime("%H:%M")
    try:
        return datetime.now().strftime(pattern)
    except ValueError:
        return pattern


def items_label(count: int) -> str:
    return f"{count:,} item" + ("" if count == 1 else "s")


def elapsed(start: float) -> str:
    secs = time.monotonic() - start
    return f"{secs * 1000:.0f} ms" if secs < 1 else f"{secs:.1f} s"

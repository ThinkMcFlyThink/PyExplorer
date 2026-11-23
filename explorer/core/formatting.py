"""Small helpers for turning numbers into human-friendly text."""
from __future__ import annotations

import time
from datetime import datetime

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def human_size(num_bytes: float) -> str:
    """1536 -> '1.5 KB'"""
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def human_time(timestamp: float) -> str:
    """Seconds-since-epoch -> 'Today 14:03' / '2024-05-01 09:12'."""
    if not timestamp:
        return ""
    try:
        dt = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return ""
    today = datetime.now().date()
    if dt.date() == today:
        return "Today " + dt.strftime("%H:%M")
    if (today - dt.date()).days == 1:
        return "Yesterday " + dt.strftime("%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def items_label(count: int) -> str:
    return f"{count:,} item" + ("" if count == 1 else "s")


def elapsed(start: float) -> str:
    secs = time.monotonic() - start
    return f"{secs * 1000:.0f} ms" if secs < 1 else f"{secs:.1f} s"

"""
Small helpers shared by the providers: running external programs safely
(timeouts, cancelling, no console window) and turning raw numbers into the
words people expect ("1:42:07", "5.1", "23.976 fps").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from typing import List, Optional

from ...core.tasks import Cancelled

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


@lru_cache(maxsize=None)
def which(program: str) -> Optional[str]:
    """Full path of a program on PATH, or None. Cached: PATH lookups cost a few ms."""
    return shutil.which(program)


def run(args: List[str], timeout: float = 10.0, token=None, max_output: int = 64 * 1024 * 1024) -> bytes:
    """Run a program and return what it printed. Raises ``Cancelled`` when the
    user moves on to another file (the program is killed straight away),
    ``TimeoutError`` if it takes too long, ``OSError`` if it fails."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                if token is not None and token.cancelled:
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{os.path.basename(args[0])} took longer than {timeout:.0f} s")
    except BaseException:
        proc.kill()
        try:
            proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        raise
    if proc.returncode != 0 and not out:
        message = err.decode("utf-8", "replace").strip().splitlines()
        raise OSError(message[-1] if message else f"{os.path.basename(args[0])} failed ({proc.returncode})")
    return out[:max_output]


# -- formatting ---------------------------------------------------------------------
def duration(seconds: Optional[float]) -> str:
    """3727.5 -> '1:02:07'; 42 -> '0:42'."""
    if seconds is None:
        return ""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def bitrate(bps) -> str:
    """4_500_000 -> '4.5 Mb/s'."""
    try:
        value = float(bps)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} Mb/s"
    return f"{value / 1000:.0f} kb/s"


def sample_rate(hz) -> str:
    try:
        value = float(hz)
    except (TypeError, ValueError):
        return ""
    return f"{value / 1000:g} kHz" if value >= 1000 else f"{value:g} Hz"


def rate(fraction: Optional[str]) -> float:
    """'24000/1001' -> 23.976...; '25' -> 25.0; bad input -> 0."""
    if not fraction:
        return 0.0
    try:
        if "/" in fraction:
            num, den = fraction.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(fraction)
    except (TypeError, ValueError):
        return 0.0


def fps(fraction: Optional[str]) -> str:
    value = rate(fraction)
    if not value or value > 1000:
        return ""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{text} fps"


CHANNEL_NAMES = {1: "Mono", 2: "Stereo", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1", 10: "7.1.2",
                 12: "7.1.4"}


def channels(count, layout: str = "") -> str:
    """(6, '5.1(side)') -> '5.1 (6 channels)'."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return layout or ""
    name = (layout or "").replace("(side)", "").strip() or CHANNEL_NAMES.get(n, "")
    if name.lower() in ("mono", "stereo"):
        name = name.capitalize()
    plural = "channel" if n == 1 else "channels"
    return f"{name} ({n} {plural})" if name else f"{n} {plural}"


def resolution_label(width: int, height: int) -> str:
    """The name people use for a video size: 1920x1080 -> '1080p'."""
    if not width or not height:
        return ""
    w, h = max(width, height), min(width, height)
    for min_w, min_h, label in ((7600, 4000, "8K"), (3800, 1600, "4K UHD"), (2500, 1080, "1440p"),
                                (1900, 800, "1080p"), (1240, 520, "720p"), (700, 400, "SD")):
        if w >= min_w or h >= min_h:
            return label
    return ""


def aspect(width: int, height: int) -> str:
    """1920x1080 -> '16:9'; 3840x1600 -> '2.40:1'."""
    if not width or not height:
        return ""
    ratio = width / height
    for value, label in ((16 / 9, "16:9"), (4 / 3, "4:3"), (21 / 9, "21:9"), (1.0, "1:1"), (3 / 2, "3:2"),
                         (9 / 16, "9:16"), (1.85, "1.85:1"), (2.39, "2.39:1"), (2.4, "2.40:1")):
        if abs(ratio - value) < 0.01:
            return label
    return f"{ratio:.2f}:1"


def language(code: Optional[str]) -> str:
    """ISO 639 code -> a name for the common ones ('eng' -> 'English'); anything else as given."""
    if not code or code.lower() in ("und", "unk", "zxx", "mis"):
        return ""
    return LANGUAGES.get(code.lower(), code)


LANGUAGES = {
    "en": "English", "eng": "English", "fr": "French", "fre": "French", "fra": "French", "de": "German",
    "ger": "German", "deu": "German", "es": "Spanish", "spa": "Spanish", "it": "Italian", "ita": "Italian",
    "pt": "Portuguese", "por": "Portuguese", "nl": "Dutch", "dut": "Dutch", "nld": "Dutch", "ru": "Russian",
    "rus": "Russian", "ja": "Japanese", "jpn": "Japanese", "zh": "Chinese", "chi": "Chinese", "zho": "Chinese",
    "ko": "Korean", "kor": "Korean", "ar": "Arabic", "ara": "Arabic", "hi": "Hindi", "hin": "Hindi",
    "sv": "Swedish", "swe": "Swedish", "no": "Norwegian", "nor": "Norwegian", "nob": "Norwegian",
    "da": "Danish", "dan": "Danish", "fi": "Finnish", "fin": "Finnish", "pl": "Polish", "pol": "Polish",
    "tr": "Turkish", "tur": "Turkish", "cs": "Czech", "cze": "Czech", "ces": "Czech", "el": "Greek",
    "gre": "Greek", "ell": "Greek", "he": "Hebrew", "heb": "Hebrew", "hu": "Hungarian", "hun": "Hungarian",
    "ro": "Romanian", "rum": "Romanian", "ron": "Romanian", "th": "Thai", "tha": "Thai", "uk": "Ukrainian",
    "ukr": "Ukrainian", "vi": "Vietnamese", "vie": "Vietnamese", "id": "Indonesian", "ind": "Indonesian",
    "ms": "Malay", "may": "Malay", "msa": "Malay", "bg": "Bulgarian", "bul": "Bulgarian", "hr": "Croatian",
    "hrv": "Croatian", "sr": "Serbian", "srp": "Serbian", "sk": "Slovak", "slo": "Slovak", "slk": "Slovak",
    "sl": "Slovenian", "slv": "Slovenian", "ca": "Catalan", "cat": "Catalan", "fa": "Persian", "per": "Persian",
    "fas": "Persian", "ta": "Tamil", "tam": "Tamil", "te": "Telugu", "tel": "Telugu", "is": "Icelandic",
    "ice": "Icelandic", "isl": "Icelandic", "et": "Estonian", "est": "Estonian", "lv": "Latvian",
    "lav": "Latvian", "lt": "Lithuanian", "lit": "Lithuanian", "fil": "Filipino", "tl": "Tagalog",
    "tgl": "Tagalog", "la": "Latin", "lat": "Latin",
}


def clip(text: str, limit: int = 600) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

"""
JsonStore - read/write one JSON file safely.

Every persistent thing (settings, sidebar categories, folder colours,
session, network connections) uses this, so they all share the same
"atomic write" behaviour: we write to a temporary file first and then
swap it into place. If the computer crashes mid-save, you keep the old
file instead of a half-written, corrupt one.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JsonStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self, default: Any = None) -> Any:
        """Return the file's data, or ``default`` if missing or unreadable."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            log.exception("Could not read %s - using defaults", self.path)
            # Keep the broken file around for inspection instead of overwriting it.
            try:
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".broken"))
            except OSError:
                pass
            return default

    def save(self, data: Any) -> None:
        """Write ``data`` atomically (temp file + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

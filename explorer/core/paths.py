"""
Where PyExplorer stores its configuration files on each operating system.

    Windows : %APPDATA%\\PyExplorer
    macOS   : ~/Library/Application Support/PyExplorer
    Linux   : $XDG_CONFIG_HOME/pyexplorer  (usually ~/.config/pyexplorer)

Set the environment variable PYEXPLORER_CONFIG_DIR to override (handy for
testing or for a "portable" install on a USB stick).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("PYEXPLORER_CONFIG_DIR")
    if override:
        path = Path(override)
    elif sys.platform.startswith("win"):
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "PyExplorer"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "PyExplorer"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        path = Path(base) / "pyexplorer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file(name: str) -> Path:
    """Full path of one of our JSON files, e.g. ``config_file('settings.json')``."""
    return config_dir() / name

#!/usr/bin/env python3
"""
PyExplorer - entry point.

Run with:
    python main.py              # opens your last session (or your home folder)
    python main.py /some/path   # opens an extra tab at that path

Everything interesting lives in the ``explorer`` package. This file only
starts the application. The ``if __name__ == "__main__"`` guard matters:
on Windows/macOS, worker *processes* re-import this file, and the guard
stops them from opening a second window.
"""
import multiprocessing
import sys


def main() -> int:
    from explorer.app import run
    return run(sys.argv[1:])


if __name__ == "__main__":
    multiprocessing.freeze_support()   # needed if you ever bundle with PyInstaller
    sys.exit(main())

"""
CPU-heavy helpers that run in a separate PROCESS (TaskRunner pool="process").

Functions here must be plain top-level functions with simple arguments,
because they are "pickled" and sent to another Python process.
"""
from __future__ import annotations

import hashlib


def sha256_file(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

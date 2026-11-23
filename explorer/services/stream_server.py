"""
StreamServer - lets other apps (mpv, VLC, browsers...) STREAM a file that
lives on a network location (SMB, SFTP, FTP) instead of downloading it first.

It is a tiny web server inside PyExplorer:
  * it only listens on 127.0.0.1 (your own computer - nothing else can connect),
  * every shared file gets an unguessable address such as
        http://127.0.0.1:41923/kX3...9q/movie.mkv
    so other programs on the computer can't browse your files either,
  * it supports "Range" requests, so players can jump around (seek) and
    only the parts actually watched are fetched from the NAS.

The file is read through PyExplorer's own connection (already logged in),
so no passwords are handed to other apps. Streams stop when PyExplorer closes.
"""
from __future__ import annotations

import mimetypes
import re
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import quote, unquote

CHUNK = 512 * 1024
MAX_SHARED = 500
_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """'bytes=100-199' -> (100, 199). None = whole file. Raises ValueError if unsatisfiable."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None                              # odd/multi-range requests: send everything
    if not m.group(1):                           # "bytes=-500": the last 500 bytes
        length = int(m.group(2))
        if length == 0:
            raise ValueError("empty range")
        return max(0, size - length), size - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else size - 1
    if start >= size or start > end:
        raise ValueError("range outside the file")
    return start, min(end, size - 1)


class StreamServer:
    def __init__(self, vfs) -> None:
        self._vfs = vfs
        self._shared: "OrderedDict[str, Tuple[str, int, str]]" = OrderedDict()   # token -> (path, size, name)
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None

    # -- public --------------------------------------------------------------------
    def url_for(self, path: str) -> str:
        """Share ``path`` and return the address an app can open. Call from a worker
        thread: it asks the server for the file's size."""
        fs = self._vfs.resolve(path)
        entry = fs.stat(path)
        self._start()
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._shared[token] = (path, entry.size, entry.name)
            while len(self._shared) > MAX_SHARED:
                self._shared.popitem(last=False)
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/{token}/{quote(entry.name)}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- internals -------------------------------------------------------------------
    def _lookup(self, token: str):
        with self._lock:
            return self._shared.get(token)

    def _start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            owner = self

            class Handler(_Handler):
                server_owner = owner

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name="px-stream", daemon=True).start()
            self._server = server


class _Handler(BaseHTTPRequestHandler):
    server_owner: StreamServer = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:      # keep the console quiet
        pass

    def do_HEAD(self) -> None:
        self._serve(body=False)

    def do_GET(self) -> None:
        self._serve(body=True)

    def _serve(self, body: bool) -> None:
        parts = self.path.lstrip("/").split("/", 1)
        shared = self.server_owner._lookup(unquote(parts[0])) if parts and parts[0] else None
        if shared is None:
            self.send_error(404, "Not found")
            return
        path, size, name = shared
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        fs = self.server_owner._vfs.resolve(path)
        seekable = getattr(fs, "scheme", "") != "ftp"           # FTP streams can't jump around
        try:
            byte_range = parse_range(self.headers.get("Range"), size) if seekable else None
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = byte_range if byte_range else (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if byte_range else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if seekable:
            self.send_header("Accept-Ranges", "bytes")
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body or length == 0:
            return
        try:
            with fs.open_read(path) as fh:
                if start:
                    fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # the player stopped or jumped elsewhere: normal
        except OSError:
            self.close_connection = True

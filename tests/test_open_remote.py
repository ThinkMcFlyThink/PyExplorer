"""
Tests for opening network files in other apps WITHOUT downloading them first.

  * the streaming server: byte ranges (seeking), HEAD, errors, privacy (unknown tokens)
  * finding real paths: GVFS mounts, /etc/fstab (cifs) mounts, Windows UNC paths
  * mounting a share like Nemo does (gio mount) - simulated, and for real if GVFS runs
  * reading .desktop files: Exec field codes, URL support, default apps
  * choosing path / stream / download
  * if mpv is installed: mpv really plays a video from the SMB share through the
    stream address, including a seek

Needs the same SMB test server as test_smb.py (or set PYX_SMB_* and PYX_SMB_VIDEO).
Run:  python tests/test_open_remote.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("PYEXPLORER_CONFIG_DIR", tempfile.mkdtemp())

from explorer.core.connections import Connections  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.services import network_paths  # noqa: E402
from explorer.services.desktop_apps import DesktopApp, DesktopApps  # noqa: E402
from explorer.services.remote_open import RemoteOpener  # noqa: E402
from explorer.services.stream_server import StreamServer, parse_range  # noqa: E402
from explorer.vfs.registry import VfsRegistry  # noqa: E402
from explorer.vfs.sftp import CredentialsProvider  # noqa: E402

HOST = os.environ.get("PYX_SMB_HOST", "127.0.0.1")
USER = os.environ.get("PYX_SMB_USER", "nasuser")
PASS = os.environ.get("PYX_SMB_PASS", "secret123")
VIDEO = os.environ.get("PYX_SMB_VIDEO", "media/Films/test.mkv")


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


class Login(CredentialsProvider):
    def ask_login(self, conn, reason=""):
        return USER, PASS


print("byte ranges")
check(parse_range("bytes=100-199", 1000) == (100, 199), "start-end")
check(parse_range("bytes=900-", 1000) == (900, 999), "start- (to the end)")
check(parse_range("bytes=-100", 1000) == (900, 999), "-N (the last N bytes)")
check(parse_range("bytes=0-5000", 1000) == (0, 999), "end beyond the file is trimmed")
check(parse_range(None, 1000) is None and parse_range("bytes=0-1,5-9", 1000) is None, "no/multi range = whole file")
try:
    parse_range("bytes=2000-", 1000)
    raise AssertionError("should fail")
except ValueError:
    check(True, "a range past the end is refused")

print("streaming a file from the SMB share")
vfs = VfsRegistry(Connections(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"] + "/c.json"), EventBus()), Login())
video_uri = f"smb://{USER}@{HOST}/{VIDEO}"
fs = vfs.resolve(video_uri)
size = fs.stat(video_uri).size
with fs.open_read(video_uri) as fh:
    original = fh.read()
server = StreamServer(vfs)
url = server.url_for(video_uri)
check(url.startswith("http://127.0.0.1:") and url.endswith("/test.mkv"), f"private address: {url.split('/')[2]}/<token>/test.mkv")
with urllib.request.urlopen(url) as resp:
    check(resp.status == 200 and resp.read() == original, f"whole file ({size:,} bytes) streams correctly")
req = urllib.request.Request(url, headers={"Range": "bytes=1000-1999"})
with urllib.request.urlopen(req) as resp:
    check(resp.status == 206 and resp.headers["Content-Range"] == f"bytes 1000-1999/{size}" and resp.read() == original[1000:2000],
          "a byte range (what players use to seek)")
with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as resp:
    check(resp.headers["Accept-Ranges"] == "bytes" and int(resp.headers["Content-Length"]) == size, "HEAD request")
bad = url.rsplit("/", 2)[0] + "/not-a-token/test.mkv"
try:
    urllib.request.urlopen(bad)
    raise AssertionError("unknown token served")
except urllib.error.HTTPError as exc:
    check(exc.code == 404, "unknown addresses are refused (nothing else is browsable)")

mpv = shutil.which("mpv")
if mpv:
    print("mpv plays the stream")
    out = subprocess.run([mpv, "--no-config", "--vo=null", "--ao=null", "--frames=25", "--start=4", url],
                         capture_output=True, text=True, timeout=60)
    check(out.returncode == 0 and "Video" in out.stdout + out.stderr, "mpv plays the video, starting 4 seconds in (seek)")
else:
    print("  (mpv not installed - skipping the playback test)")

print("real paths for network files")
gv = tempfile.mkdtemp()
os.makedirs(os.path.join(gv, "smb-share:server=truenas.local,share=media,user=bob"))
os.makedirs(os.path.join(gv, "sftp:host=pi.local,port=2222"))
check(network_paths.gvfs_path_for("smb://truenas.local:445/media/Films/a.mkv", gv)
      == os.path.join(gv, "smb-share:server=truenas.local,share=media,user=bob", "Films", "a.mkv"), "GVFS (Nemo) mount of the share")
check(network_paths.gvfs_path_for("smb://TrueNAS.local/Media/x", gv) is not None, "server and share names ignore case")
check(network_paths.gvfs_path_for("smb://truenas.local/other/x", gv) is None, "another share isn't mounted")
check(network_paths.gvfs_path_for("sftp://me@pi.local:2222/home/me/a.txt", gv)
      == os.path.join(gv, "sftp:host=pi.local,port=2222", "home", "me", "a.txt"), "GVFS SFTP mount")
mounts = [("//truenas.local/media", "/mnt/media", "cifs"), ("nas:/x", "/mnt/x", "nfs4")]
check(network_paths.cifs_mount_path_for("smb://truenas.local/media/Films/a.mkv", mounts) == "/mnt/media/Films/a.mkv",
      "/etc/fstab (cifs) mount of the share")
check(network_paths.unc_path_for("smb://truenas.local:445/media/Films/a.mkv") == r"\\truenas.local\media\Films\a.mkv",
      "Windows \\\\server\\share path")

print("mounting like Nemo (gio mount)")
calls = []


def fake_run(args, **kw):
    calls.append((args, kw.get("input")))
    return SimpleNamespace(returncode=0, stderr="")


check(network_paths.gio_mount_smb("smb://truenas.local:445/media/Films/a.mkv", r"HOME\bob", "pw", run=fake_run), "gio mount succeeds")
check(calls[0][0] == ["gio", "mount", "smb://truenas.local/media"] and calls[0][1] == "bob\nHOME\npw\n",
      "only the share is mounted; user/domain/password go through a pipe, not the command line")
if network_paths.gvfs_available():
    path = network_paths.local_path_for(video_uri, USER, PASS)
    check(path is not None and os.path.exists(path) and os.path.getsize(path) == size,
          f"real GVFS mount: {path}")
    if mpv:
        out = subprocess.run([mpv, "--no-config", "--vo=null", "--ao=null", "--frames=10", path],
                             capture_output=True, text=True, timeout=60)
        check(out.returncode == 0, "mpv plays the file from the mounted share")
else:
    print("  (GVFS not running here - skipping the real mount test)")

print("desktop apps")
apps_dir = tempfile.mkdtemp()
with open(os.path.join(apps_dir, "player.desktop"), "w") as fh:
    fh.write("[Desktop Entry]\nType=Application\nName=Player\nExec=player --fs %U\nMimeType=video/x-matroska;\nIcon=player\n")
with open(os.path.join(apps_dir, "viewer.desktop"), "w") as fh:
    fh.write("[Desktop Entry]\nType=Application\nName=Viewer\nExec=viewer %f\nMimeType=video/x-matroska;image/png;\n")
with open(os.path.join(apps_dir, "gone.desktop"), "w") as fh:
    fh.write("[Desktop Entry]\nType=Application\nName=Gone\nExec=gone %f\nTryExec=definitely-not-installed\n"
             "MimeType=video/x-matroska;\n")
apps = DesktopApps([apps_dir], query_default=lambda mime: "viewer.desktop" if mime == "video/x-matroska" else None)
found = [a.id for a in apps.apps_for(["video/x-matroska"])]
check(found == ["viewer.desktop", "player.desktop"], "apps for a type, default first, uninstalled ones hidden")
player, viewer = apps.get("player.desktop"), apps.get("viewer.desktop")
check(player.accepts_urls and not viewer.accepts_urls, "%U = accepts URLs, %f = files only")
check(player.command(["http://x/a.mkv"]) == ["player", "--fs", "http://x/a.mkv"], "command line built from Exec")
check(DesktopApp("x", "X", "x --name %c --file=%f").command(["/a"]) == ["x", "--name", "X", "--file=/a"], "%c and embedded %f")
check(DesktopApp("x", "X", "x").command(["/a"]) == ["x", "/a"], "no field code: file appended")

print("choosing how to open")
settings = SimpleNamespace(values={"network.open_files": "stream", "network.mount_shares": False})
settings.get = lambda key, default=None: settings.values.get(key, default)
opener = RemoteOpener(vfs, server, settings)
check(opener.resolve("/etc/hostname", False).kind == "path", "local files: their own path")
check(opener.resolve(video_uri, True).kind in ("url", "path"), "URL-capable app (mpv): stream (or a mount if one exists)")
if not network_paths.gvfs_path_for(video_uri):
    check(opener.resolve(video_uri, True).kind == "url", "no mount allowed + URL app: stream")
    check(opener.resolve(video_uri, False).kind == "download", "no mount allowed + file-only app: download")
settings.values["network.open_files"] = "download"
check(opener.resolve(video_uri, True).kind == "download", "the 'download first' setting is respected")
server.stop()
vfs.close_all()
print("\nOPEN-REMOTE TESTS PASSED")

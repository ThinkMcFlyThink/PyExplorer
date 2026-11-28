"""
Video and audio files: every stream, as much detail as ffprobe gives.

    ffprobe (part of FFmpeg)   containers, every video / audio / subtitle stream,
                               HDR10 / HLG / Dolby Vision, chapters, tags
    ffmpeg                     a poster frame for videos, the cover of music files
    mediainfo                  used instead of ffprobe when FFmpeg isn't installed

All three are free and in every Linux distribution, Homebrew, winget and
Chocolatey. Without them the panel still shows the General section.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ...core.fileinfo import EXT_CATEGORY
from . import tools
from .base import Info, InfoProvider, Section, Source

VIDEO_EXTS = {e for e, c in EXT_CATEGORY.items() if c == "video"} | {
    "mts", "m2ts", "m2t", "vob", "ogv", "rm", "rmvb", "asf", "divx", "f4v", "mxf", "y4m", "h264", "h265", "hevc"}
AUDIO_EXTS = {e for e, c in EXT_CATEGORY.items() if c == "audio" and e not in ("mid", "midi")} | {
    "mka", "ape", "wv", "dsf", "dff", "tta", "ac3", "dts", "eac3", "mpc", "spx", "caf", "amr", "au", "w64", "alac"}

FRIENDLY_CODECS = {
    "h264": "H.264 / AVC", "hevc": "H.265 / HEVC", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "mpeg4": "MPEG-4 Part 2", "mpeg2video": "MPEG-2", "mpeg1video": "MPEG-1", "prores": "Apple ProRes",
    "dnxhd": "Avid DNxHD", "mjpeg": "Motion JPEG", "theora": "Theora", "wmv3": "WMV 9", "vc1": "VC-1",
    "aac": "AAC", "mp3": "MP3", "mp2": "MPEG Layer II", "ac3": "Dolby Digital (AC-3)",
    "eac3": "Dolby Digital Plus (E-AC-3)", "truehd": "Dolby TrueHD", "dts": "DTS", "flac": "FLAC",
    "alac": "Apple Lossless", "opus": "Opus", "vorbis": "Vorbis", "wmav2": "WMA", "pcm_s16le": "PCM 16-bit",
    "pcm_s24le": "PCM 24-bit", "pcm_s32le": "PCM 32-bit", "pcm_f32le": "PCM 32-bit float", "pcm_s16be": "PCM 16-bit",
    "pcm_s24be": "PCM 24-bit", "ape": "Monkey's Audio", "wavpack": "WavPack",
    "subrip": "SubRip (SRT)", "ass": "ASS / SSA", "ssa": "SSA", "webvtt": "WebVTT", "mov_text": "MP4 timed text",
    "hdmv_pgs_subtitle": "PGS (Blu-ray)", "dvd_subtitle": "VobSub (DVD)", "dvb_subtitle": "DVB",
}
CONTAINERS = {"matroska,webm": "Matroska / WebM", "mov,mp4,m4a,3gp,3g2,mj2": "MP4 / QuickTime",
              "mpegts": "MPEG transport stream", "avi": "AVI", "flac": "FLAC", "ogg": "Ogg", "wav": "WAV",
              "mp3": "MP3", "asf": "ASF / Windows Media", "flv": "Flash video", "mpeg": "MPEG program stream"}
TAG_ORDER = [("title", "Title"), ("artist", "Artist"), ("album_artist", "Album artist"), ("album", "Album"),
             ("track", "Track"), ("disc", "Disc"), ("date", "Date"), ("year", "Year"), ("genre", "Genre"),
             ("composer", "Composer"), ("performer", "Performer"), ("publisher", "Publisher"),
             ("label", "Label"), ("copyright", "Copyright"), ("comment", "Comment"), ("show", "Show"),
             ("season_number", "Season"), ("episode_sort", "Episode"), ("description", "Description"),
             ("bpm", "BPM"), ("isrc", "ISRC"), ("encoded_by", "Encoded by"), ("encoder", "Encoder"),
             ("creation_time", "Created"), ("location", "Location")]


def _tags(d: Dict[str, Any]) -> Dict[str, str]:
    """ffprobe tags with lower-case keys (MKV writes 'TITLE', MP4 'title')."""
    out = {}
    for k, v in (d.get("tags") or {}).items():
        key = k.lower()
        out.setdefault(key, str(v))
        base = key.split("-")[0]               # 'bps-eng' -> 'bps'
        out.setdefault(base, str(v))
    return out


def _codec(stream: Dict[str, Any]) -> str:
    name = stream.get("codec_name", "")
    label = FRIENDLY_CODECS.get(name) or (stream.get("codec_long_name") or name or "Unknown").split(" / ")[0]
    profile = stream.get("profile")
    if profile and profile.lower() not in ("unknown", "main") and profile.lower() not in label.lower():
        if name == "dts" and profile in ("DTS-HD MA", "DTS-HD HRA", "DTS:X", "DTS-ES"):
            return profile
        label += f" ({profile})"
    elif profile and profile.lower() == "main" and name in ("hevc", "h264"):
        label += " (Main)"
    return label


def _bit_depth(stream: Dict[str, Any]) -> str:
    raw = stream.get("bits_per_raw_sample")
    if raw and str(raw).isdigit() and int(raw):
        return f"{raw}-bit"
    pix = stream.get("pix_fmt") or ""
    for depth in ("16", "14", "12", "10"):
        if depth in pix:
            return f"{depth}-bit"
    return "8-bit" if pix else ""


def _hdr(stream: Dict[str, Any]) -> str:
    kinds = []
    for side in stream.get("side_data_list") or []:
        t = side.get("side_data_type", "")
        if "DOVI" in t or "Dolby Vision" in t:
            profile = side.get("dv_profile")
            kinds.append(f"Dolby Vision{f' profile {profile}' if profile is not None else ''}")
        elif "HDR10+" in t or "HDR Dynamic Metadata SMPTE2094-40" in t:
            kinds.append("HDR10+")
    transfer = stream.get("color_transfer", "")
    if transfer == "smpte2084":
        if not any(k == "HDR10+" for k in kinds):
            kinds.append("HDR10")
    elif transfer == "arib-std-b67":
        kinds.append("HLG")
    return ", ".join(dict.fromkeys(kinds))


def _flags(stream: Dict[str, Any]) -> List[str]:
    disp = stream.get("disposition") or {}
    out = []
    for key, label in (("default", "default"), ("forced", "forced"), ("hearing_impaired", "SDH"),
                       ("visual_impaired", "audio description"), ("comment", "commentary")):
        if disp.get(key):
            out.append(label)
    return out


def _stream_bitrate(stream: Dict[str, Any], tags: Dict[str, str]) -> str:
    return tools.bitrate(stream.get("bit_rate") or tags.get("bps"))


def _video_section(info: Info, stream: Dict[str, Any], n: int, total: int) -> None:
    tags = _tags(stream)
    s = info.section("Video" if total == 1 else f"Video #{n}")
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    s.add("Codec", _codec(stream))
    label = tools.resolution_label(w, h)
    s.add("Resolution", f"{w} × {h}" + (f"  ({label})" if label else "") if w and h else "")
    dar = stream.get("display_aspect_ratio")
    if dar and dar not in ("0:1", "N/A"):
        a, b = (int(x) for x in dar.split(":"))
        s.add("Aspect ratio", tools.aspect(a, b) if b else dar)
    else:
        s.add("Aspect ratio", tools.aspect(w, h))
    frame_rate = stream.get("avg_frame_rate") if tools.rate(stream.get("avg_frame_rate")) else stream.get("r_frame_rate")
    s.add("Frame rate", tools.fps(frame_rate))
    s.add("Bit rate", _stream_bitrate(stream, tags))
    s.add("Bit depth", _bit_depth(stream))
    s.add("HDR", _hdr(stream))
    colour = [stream.get("color_primaries"), stream.get("color_space")]
    colour = [c for c in colour if c and c != "unknown"]
    if colour:
        rng = stream.get("color_range")
        s.add("Colour", " / ".join(dict.fromkeys(colour)) + (f" ({'limited' if rng == 'tv' else 'full'} range)"
                                                             if rng in ("tv", "pc") else ""))
    s.add("Pixel format", stream.get("pix_fmt"))
    order = stream.get("field_order")
    if order and order not in ("unknown",):
        s.add("Scan", "Progressive" if order == "progressive" else "Interlaced")
    s.add("Frames", f"{int(stream['nb_frames']):,}" if str(stream.get("nb_frames", "")).isdigit() else "")
    s.add("Language", tools.language(tags.get("language")))
    s.add("Title", tags.get("title"))
    s.add("Flags", _flags(stream))


def _audio_section(info: Info, stream: Dict[str, Any], n: int, total: int) -> None:
    tags = _tags(stream)
    lang = tools.language(tags.get("language"))
    s = info.section("Audio" if total == 1 else f"Audio #{n}" + (f" · {lang}" if lang else ""), key="Audio")
    s.add("Codec", _codec(stream))
    s.add("Channels", tools.channels(stream.get("channels"), stream.get("channel_layout", "")))
    s.add("Sample rate", tools.sample_rate(stream.get("sample_rate")))
    raw = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    s.add("Bit depth", f"{raw}-bit" if raw and str(raw).isdigit() and int(raw) else "")
    s.add("Bit rate", _stream_bitrate(stream, tags))
    s.add("Language", lang if total == 1 else "")
    s.add("Title", tags.get("title"))
    s.add("Flags", _flags(stream))


def _subtitle_section(info: Info, streams: List[Dict[str, Any]]) -> None:
    s = info.section(f"Subtitles ({len(streams)})", key="Subtitles")
    for n, stream in enumerate(streams, start=1):
        tags = _tags(stream)
        lang = tools.language(tags.get("language")) or f"Track {n}"
        extra = [_codec(stream)] + _flags(stream)
        if tags.get("title"):
            extra.insert(0, tags["title"])
        s.add(lang, ", ".join(extra))


def _chapters_section(info: Info, chapters: List[Dict[str, Any]]) -> None:
    s = info.section(f"Chapters ({len(chapters)})", key="Chapters")
    for n, ch in enumerate(chapters[:60], start=1):
        title = _tags(ch).get("title") or f"Chapter {n}"
        s.add(tools.duration(float(ch.get("start_time") or 0)), title)
    if len(chapters) > 60:
        s.add("…", f"{len(chapters) - 60} more")


def from_ffprobe(data: Dict[str, Any], is_audio_file: bool) -> Info:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    tags = _tags(fmt)
    covers = [st for st in streams if st.get("codec_type") == "video" and (st.get("disposition") or {}).get("attached_pic")]
    videos = [st for st in streams if st.get("codec_type") == "video" and st not in covers]
    audios = [st for st in streams if st.get("codec_type") == "audio"]
    subs = [st for st in streams if st.get("codec_type") == "subtitle"]
    container = CONTAINERS.get(fmt.get("format_name", ""), (fmt.get("format_long_name") or "").split(" / ")[0])

    audio_only = not videos
    info = Info(kind=("Audio" if audio_only else "Video") + (f" · {container}" if container else ""))
    if audio_only and tags:
        t = info.section("Tags")
        for key, label in TAG_ORDER:
            if key in ("creation_time", "encoder"):
                continue
            value = tags.get(key)
            if value:
                t.add(label, tools.clip(value, 300))
    m = info.section("Media")
    m.add("Container", container)
    try:
        secs = float(fmt.get("duration")) if fmt.get("duration") not in (None, "N/A") else None
    except ValueError:
        secs = None
    m.add("Duration", tools.duration(secs))
    m.add("Overall bit rate", tools.bitrate(fmt.get("bit_rate")))
    if not audio_only:
        m.add("Title", tags.get("title"))
    counts = []
    for items, word in ((videos, "video"), (audios, "audio"), (subs, "subtitle")):
        if items:
            counts.append(f"{len(items)} {word}")
    if len(counts) > 1 or len(audios) > 1:
        m.add("Streams", ", ".join(counts))
    m.add("Chapters", len(data.get("chapters") or []) or "")
    m.add("Encoder", tags.get("encoder") or tags.get("encoded_by") or tags.get("writing_application"))
    m.add("Created", tags.get("creation_time", "").replace("T", " ").split(".")[0])
    for n, st in enumerate(videos, start=1):
        _video_section(info, st, n, len(videos))
    for n, st in enumerate(audios, start=1):
        _audio_section(info, st, n, len(audios))
    if subs:
        _subtitle_section(info, subs)
    if data.get("chapters"):
        _chapters_section(info, data["chapters"])
    info.extra["duration"] = secs or 0.0
    info.extra["has_cover"] = bool(covers)
    info.extra["has_video"] = bool(videos)
    return info


# -- mediainfo (when FFmpeg isn't installed) -------------------------------------------------
def from_mediainfo(data: Dict[str, Any]) -> Info:
    tracks = (data.get("media") or {}).get("track") or []
    general = next((t for t in tracks if t.get("@type") == "General"), {})
    videos = [t for t in tracks if t.get("@type") == "Video"]
    audios = [t for t in tracks if t.get("@type") == "Audio"]
    texts = [t for t in tracks if t.get("@type") == "Text"]
    container = general.get("Format", "")
    info = Info(kind=("Video" if videos else "Audio") + (f" · {container}" if container else ""))
    if not videos:
        t = info.section("Tags")
        for key, label in (("Title", "Title"), ("Performer", "Artist"), ("Album_Performer", "Album artist"),
                           ("Album", "Album"), ("Track_Position", "Track"), ("Recorded_Date", "Date"),
                           ("Genre", "Genre"), ("Composer", "Composer"), ("Comment", "Comment")):
            t.add(label, general.get(key))
    m = info.section("Media")
    m.add("Container", container)
    m.add("Duration", tools.duration(general.get("Duration")))
    m.add("Overall bit rate", tools.bitrate(general.get("OverallBitRate")))
    m.add("Encoder", general.get("Encoded_Application") or general.get("Encoded_Library"))
    for n, v in enumerate(videos, start=1):
        s = info.section("Video" if len(videos) == 1 else f"Video #{n}")
        w, h = int(v.get("Width") or 0), int(v.get("Height") or 0)
        s.add("Codec", " ".join(x for x in (v.get("Format"), f"({v['Format_Profile']})" if v.get("Format_Profile") else "") if x))
        label = tools.resolution_label(w, h)
        s.add("Resolution", f"{w} × {h}" + (f"  ({label})" if label else "") if w and h else "")
        s.add("Aspect ratio", v.get("DisplayAspectRatio_String") or tools.aspect(w, h))
        s.add("Frame rate", tools.fps(v.get("FrameRate")))
        s.add("Bit rate", tools.bitrate(v.get("BitRate")))
        s.add("Bit depth", f"{v['BitDepth']}-bit" if v.get("BitDepth") else "")
        s.add("HDR", v.get("HDR_Format"))
        s.add("Colour", v.get("colour_primaries"))
        s.add("Scan", v.get("ScanType"))
        s.add("Language", tools.language(v.get("Language")))
    for n, a in enumerate(audios, start=1):
        lang = tools.language(a.get("Language"))
        s = info.section("Audio" if len(audios) == 1 else f"Audio #{n}" + (f" · {lang}" if lang else ""), key="Audio")
        s.add("Codec", a.get("Format_Commercial_IfAny") or a.get("Format"))
        s.add("Channels", tools.channels(a.get("Channels")))
        s.add("Sample rate", tools.sample_rate(a.get("SamplingRate")))
        s.add("Bit depth", f"{a['BitDepth']}-bit" if a.get("BitDepth") else "")
        s.add("Bit rate", tools.bitrate(a.get("BitRate")))
        s.add("Title", a.get("Title"))
    if texts:
        s = info.section(f"Subtitles ({len(texts)})", key="Subtitles")
        for n, t in enumerate(texts, start=1):
            s.add(tools.language(t.get("Language")) or f"Track {n}", ", ".join(
                x for x in (t.get("Title"), t.get("Format"), "default" if t.get("Default") == "Yes" else "",
                            "forced" if t.get("Forced") == "Yes" else "") if x))
    try:
        info.extra["duration"] = float(general.get("Duration") or 0)
    except ValueError:
        info.extra["duration"] = 0.0
    info.extra["has_video"] = bool(videos)
    info.extra["has_cover"] = general.get("Cover") == "Yes"
    return info


# -- images out of media files (run later, in a worker) ------------------------------------
def video_frame(path: str, at_seconds: float, token=None) -> Optional[bytes]:
    ffmpeg = tools.which("ffmpeg")
    if not ffmpeg:
        return None
    args = [ffmpeg, "-nostdin", "-v", "error", "-ss", f"{max(at_seconds, 0):.2f}", "-i", path,
            "-frames:v", "1", "-an", "-sn", "-vf", "scale='min(800,iw)':-2", "-f", "image2pipe", "-c:v", "mjpeg",
            "-q:v", "4", "-"]
    data = tools.run(args, timeout=12, token=token)
    if not data and at_seconds > 0:                     # very short clip: take the first frame
        data = tools.run(args[:5] + ["0"] + args[6:], timeout=12, token=token)
    return data or None


def cover_art(path: str, token=None) -> Optional[bytes]:
    ffmpeg = tools.which("ffmpeg")
    if not ffmpeg:
        return None
    return tools.run([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-an", "-map", "0:v:0", "-frames:v", "1",
                      "-vf", "scale='min(800,iw)':-2", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "-"],
                     timeout=10, token=token) or None


class MediaProvider(InfoProvider):
    name = "Video and audio"
    exts = frozenset(VIDEO_EXTS | AUDIO_EXTS)
    priority = 80

    def available(self) -> bool:
        return bool(tools.which("ffprobe") or tools.which("mediainfo"))

    def missing_hint(self) -> str:
        return "Install FFmpeg (or MediaInfo) to see codecs, resolution, HDR, audio tracks and more."

    def extract(self, src: Source) -> Optional[Info]:
        ffprobe = tools.which("ffprobe")
        if ffprobe:
            out = tools.run([ffprobe, "-v", "error", "-hide_banner", "-print_format", "json", "-show_format",
                             "-show_streams", "-show_chapters", src.path], timeout=15, token=src.token)
            info = from_ffprobe(json.loads(out.decode("utf-8", "replace") or "{}"), src.entry.ext in AUDIO_EXTS)
        else:
            out = tools.run([tools.which("mediainfo"), "--Output=JSON", src.path], timeout=15, token=src.token)
            info = from_mediainfo(json.loads(out.decode("utf-8", "replace") or "{}"))
        path = src.path
        if info.extra.get("has_video") and src.option("video_frames", True) and tools.which("ffmpeg"):
            at = info.extra.get("duration", 0) * 0.1
            at = min(at, 120.0) if at else 0.0
            info.preview = lambda token=None: video_frame(path, at, token)
        elif info.extra.get("has_cover") and tools.which("ffmpeg"):
            info.preview = lambda token=None: cover_art(path, token)
        return info


# Section and the helpers above are public for tests.
__all__ = ["MediaProvider", "from_ffprobe", "from_mediainfo", "Section"]

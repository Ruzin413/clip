import functools
import html
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

from models import CaptionSegment, Platform, VideoInfo
from sources import SourceRef

CHAT_TRACKS = {"live_chat", "rechat"}
SUBTITLE_EXTS = ("json3", "vtt")
BROWSER_VIDEO_CODECS = {"h264", "vp9", "av1"}

_VTT_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})"
_VTT_CUE_TIMING = re.compile(rf"{_VTT_TIME}\s+-->\s+{_VTT_TIME}")
_VTT_TAG = re.compile(r"<[^>]*>")


def _ydl(**opts: Any) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, **opts})


def get_video_info(source: SourceRef) -> VideoInfo:
    with _ydl(skip_download=True) as ydl:
        meta = ydl.extract_info(source.url, download=False)
    if not meta:
        raise ValueError(f"Could not extract metadata from {source.url}")

    stream_url = None
    formats = meta.get("formats") or []
    progressive_formats = [
        f for f in formats
        if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("url")
    ]
    if progressive_formats:
        progressive_formats.sort(key=lambda f: f.get("height") or 0, reverse=True)
        stream_url = progressive_formats[0].get("url")
    elif formats:
        stream_url = formats[-1].get("url")

    return VideoInfo(
        video_id=source.media_key,
        platform=source.platform,
        title=meta.get("title") or "Unknown Title",
        duration=float(meta.get("duration") or 0.0),
        thumbnail=meta.get("thumbnail") or "",
        stream_url=stream_url,
    )


def download_video(source: SourceRef, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source.media_key}.mp4"
    ydl_opts = {
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "outtmpl": str(output_dir / f"{source.media_key}.%(ext)s"),
        "merge_output_format": "mp4",
        # Twitch and Kick serve single-stream HLS, which arrives as MPEG-TS and needs a remux to mp4.
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        "concurrent_fragment_downloads": 8,
        "overwrites": True,
    }
    with _ydl(**ydl_opts) as ydl:
        ydl.download([source.url])
    if not target.exists():
        raise FileNotFoundError(f"Download finished but {target} is missing")
    ensure_browser_playable(target)
    return target


@functools.cache
def _h264_args() -> tuple[str, ...]:
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if " libx264 " in encoders:
        return ("-c:v", "libx264", "-preset", "fast", "-crf", "22")
    # Builds without libx264, such as Fedora's ffmpeg-free, ship OpenH264, which takes a bitrate instead of a CRF.
    return ("-c:v", "libopenh264", "-b:v", "4M")


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}: {result.stderr}")


def _video_codec(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def ensure_browser_playable(path: Path) -> None:
    """Re-encode to H.264 in place when the browser preview cannot play the codec (Twitch clips arrive as HEVC)."""
    if _video_codec(path) in BROWSER_VIDEO_CODECS:
        return
    converted = path.with_name(f"{path.stem}.h264{path.suffix}")
    _run_ffmpeg(["-i", str(path), *_h264_args(), "-c:a", "copy", "-movflags", "+faststart", str(converted)])
    converted.replace(path)


def fetch_captions(source: SourceRef) -> list[CaptionSegment]:
    if source.platform is Platform.YOUTUBE:
        try:
            segments = _transcript_api_captions(source.id)
        except (YouTubeTranscriptApiException, requests.RequestException):
            segments = []
        if segments:
            return segments
    return _ytdlp_captions(source)


def _language_rank(code: str) -> int:
    if code == "en":
        return 0
    if code.startswith("en-"):
        return 1
    return 2


def _transcript_api_captions(video_id: str) -> list[CaptionSegment]:
    transcripts = YouTubeTranscriptApi().list(video_id)
    best = min(transcripts, key=lambda t: (_language_rank(t.language_code) == 2, t.is_generated))
    return _segments((s.start, s.start + s.duration, s.text) for s in best.fetch())


def _pick_subtitle_track(info: dict[str, Any]) -> dict[str, Any] | None:
    for field in ("subtitles", "automatic_captions"):
        tracks = {lang: fmts for lang, fmts in (info.get(field) or {}).items() if lang not in CHAT_TRACKS}
        if not tracks:
            continue
        lang = min(tracks, key=_language_rank)
        for ext in SUBTITLE_EXTS:
            track = next((f for f in tracks[lang] if f.get("ext") == ext and f.get("url")), None)
            if track:
                return track
    return None


def _ytdlp_captions(source: SourceRef) -> list[CaptionSegment]:
    with _ydl(skip_download=True) as ydl:
        info = ydl.extract_info(source.url, download=False)
        track = _pick_subtitle_track(info or {})
        if track is None:
            return []
        with ydl.urlopen(track["url"]) as resp:
            body = resp.read().decode("utf-8")
    if track["ext"] == "json3":
        return parse_json3(json.loads(body))
    return parse_vtt(body)


def _segments(rows: Iterable[tuple[float, float, str]]) -> list[CaptionSegment]:
    segments: list[CaptionSegment] = []
    for start, end, raw_text in rows:
        text = " ".join(raw_text.split())
        if not text or (segments and segments[-1].text == text):
            continue
        segments.append(CaptionSegment(start=round(start, 3), end=round(max(end, start), 3), text=text))
    return segments


def parse_json3(raw: dict[str, Any]) -> list[CaptionSegment]:
    rows = []
    for event in raw.get("events") or []:
        if "segs" not in event or "tStartMs" not in event:
            continue
        start = event["tStartMs"] / 1000.0
        end = start + event.get("dDurationMs", 0) / 1000.0
        rows.append((start, end, "".join(seg.get("utf8", "") for seg in event["segs"])))
    return _segments(rows)


def _vtt_seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def parse_vtt(text: str) -> list[CaptionSegment]:
    rows = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = block.strip().splitlines()
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = _VTT_CUE_TIMING.search(lines[timing_index])
        if not timing:
            continue
        start = _vtt_seconds(*timing.groups()[:4])
        end = _vtt_seconds(*timing.groups()[4:])
        for line in lines[timing_index + 1:]:
            rows.append((start, end, html.unescape(_VTT_TAG.sub("", line))))
    return _segments(rows)


def cut_clip(source_video_path: str, start_time: float, end_time: float, output_path: str) -> str:
    source = Path(source_video_path)
    if not source.exists():
        raise FileNotFoundError(f"Source video not found: {source_video_path}")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    duration = max(end_time - start_time, 0.1)
    _run_ffmpeg([
        "-ss", str(round(start_time, 3)),
        "-i", str(source),
        "-t", str(round(duration, 3)),
        *_h264_args(),
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_file)
    ])

    return str(out_file.resolve())

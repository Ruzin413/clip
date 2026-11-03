import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from models import CaptionSegment, HighlightClip, VideoInfo

EXCITEMENT_KEYWORDS = {
    "key", "secret", "crucial", "important", "remember", "insane", "crazy",
    "incredible", "amazing", "tip", "trick", "first", "finally", "reveal",
    "hack", "mistake", "truth", "best", "worst", "lesson", "how", "why",
    "rule", "strategy", "breakthrough", "warning", "never", "always"
}


def extract_video_id(url_or_id: str) -> str:
    parsed = urllib.parse.urlparse(url_or_id)
    if not parsed.netloc:
        return url_or_id
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        return parsed.path.lstrip("/").split("?")[0]
    if "youtube.com" in parsed.netloc:
        if parsed.path == "/watch":
            queries = urllib.parse.parse_qs(parsed.query)
            if "v" in queries:
                return queries["v"][0]
        if parsed.path.startswith(("/embed/", "/v/", "/shorts/")):
            return parsed.path.split("/")[2]
    match = re.search(r"([a-zA-Z0-9_-]{11})", url_or_id)
    if match:
        return match.group(1)
    return url_or_id


def get_video_info(url: str) -> VideoInfo:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=False)
        if not meta:
            raise ValueError(f"Could not extract metadata from {url}")

        video_id = meta.get("id") or extract_video_id(url)
        title = meta.get("title", "Unknown Title")
        duration = float(meta.get("duration") or 0.0)
        thumbnail = meta.get("thumbnail") or ""

        stream_url = None
        formats = meta.get("formats", [])
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
            video_id=video_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            stream_url=stream_url,
        )


def download_video(url: str, output_dir: str | Path) -> str:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(target_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=True)
        if not meta:
            raise RuntimeError(f"Failed to download video from {url}")
        video_id = meta.get("id") or extract_video_id(url)
        downloaded_path = target_dir / f"{video_id}.mp4"
        if downloaded_path.exists():
            return str(downloaded_path.resolve())

        expected = ydl.prepare_filename(meta)
        stem = Path(expected).stem
        for candidate in target_dir.glob(f"{stem}.*"):
            return str(candidate.resolve())

        raise FileNotFoundError(f"Downloaded video file not found in {target_dir}")


def fetch_captions(video_id_or_url: str) -> list[CaptionSegment]:
    vid = extract_video_id(video_id_or_url)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(vid)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en", "en-US"])
        except Exception:
            try:
                transcript = transcript_list.find_generated_transcript(["en", "en-US"])
            except Exception:
                transcript = next(iter(transcript_list))

        data = transcript.fetch()
        segments = []
        for item in data:
            start = float(item["start"])
            duration = float(item.get("duration", 0.0))
            text = item.get("text", "").strip()
            if text:
                segments.append(CaptionSegment(
                    start=round(start, 2),
                    end=round(start + max(duration, 0.5), 2),
                    text=text
                ))
        if segments:
            return segments
    except Exception:
        pass

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        url = f"https://www.youtube.com/watch?v={vid}"
        info = ydl.extract_info(url, download=False)
        subtitles = (info.get("subtitles") or {}).get("en") or (info.get("automatic_captions") or {}).get("en")
        if not subtitles:
            return []

        json3_url = next((s["url"] for s in subtitles if s.get("ext") == "json3"), None)
        if not json3_url:
            return []

        req = urllib.request.Request(json3_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            segments = []
            events = raw.get("events", [])
            for ev in events:
                if "segs" not in ev or "tStartMs" not in ev:
                    continue
                start_sec = ev["tStartMs"] / 1000.0
                duration_sec = ev.get("dDurationMs", 1000) / 1000.0
                text = "".join(s.get("utf8", "") for s in ev["segs"]).strip()
                if text and text != "\n":
                    segments.append(CaptionSegment(
                        start=round(start_sec, 2),
                        end=round(start_sec + duration_sec, 2),
                        text=text
                    ))
            return segments


def cut_clip(source_video_path: str, start_time: float, end_time: float, output_path: str) -> str:
    source = Path(source_video_path)
    if not source.exists():
        raise FileNotFoundError(f"Source video not found: {source_video_path}")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    duration = max(end_time - start_time, 0.1)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(round(start_time, 3)),
        "-i", str(source),
        "-t", str(round(duration, 3)),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_file)
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}: {result.stderr}")

    return str(out_file.resolve())


def _build_candidate_windows(
    segments: list[CaptionSegment],
    target_duration: float = 30.0,
    min_duration: float = 12.0
) -> list[dict[str, Any]]:
    if not segments:
        return []

    windows = []
    n = len(segments)
    for i in range(n):
        start = segments[i].start
        collected_text = []
        end = segments[i].end
        for j in range(i, n):
            collected_text.append(segments[j].text)
            end = segments[j].end
            cur_duration = end - start
            if cur_duration >= target_duration or j == n - 1:
                if cur_duration >= min_duration:
                    combined = " ".join(collected_text)
                    words = combined.split()
                    wps = len(words) / max(cur_duration, 1.0)
                    kw_matches = sum(1 for w in words if w.lower().strip(".,!?:;\"'") in EXCITEMENT_KEYWORDS)
                    score = min(0.3 + (wps / 6.0) * 0.35 + min(kw_matches * 0.08, 0.35), 1.0)
                    title_candidate = " ".join(words[:6]).capitalize()
                    if len(title_candidate) > 40:
                        title_candidate = title_candidate[:37] + "..."
                    windows.append({
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "text": combined,
                        "title": title_candidate or "Key Insight",
                        "score": round(score, 2),
                        "kw_count": kw_matches
                    })
                break
    return windows


def _heuristic_highlights(segments: list[CaptionSegment], count: int = 4) -> list[HighlightClip]:
    candidates = _build_candidate_windows(segments)
    if not candidates:
        if segments:
            first = segments[0]
            last = segments[-1]
            return [HighlightClip(
                start=first.start,
                end=min(last.end, first.start + 30.0),
                title="Overview Clip",
                reason="Generated from primary video content segment.",
                score=0.85
            )]
        return []

    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected: list[dict[str, Any]] = []

    for cand in candidates:
        overlaps = any(
            not (cand["end"] <= s["start"] or cand["start"] >= s["end"])
            for s in selected
        )
        if not overlaps:
            selected.append(cand)
            if len(selected) >= count:
                break

    selected.sort(key=lambda s: s["start"])
    return [
        HighlightClip(
            start=s["start"],
            end=s["end"],
            title=s["title"],
            reason=f"High semantic density with {s['kw_count']} topical cues.",
            score=s["score"]
        )
        for s in selected
    ]


def _gemini_highlights(segments: list[CaptionSegment], api_key: str) -> list[HighlightClip]:
    transcript_sample = "\n".join(
        f"[{seg.start:.1f}s - {seg.end:.1f}s]: {seg.text}"
        for seg in segments
    )
    prompt = (
        "You are an expert video editor. Analyze this timestamped transcript and identify between 3 and 6 "
        "compelling, high-value clip highlights (15 to 60 seconds each). For each highlight, return exact start and end "
        "timestamps, a concise punchy title, a brief reason why it is interesting, and a quality score between 0.0 and 1.0.\n\n"
        "Return ONLY a JSON array of objects with the keys: 'start', 'end', 'title', 'reason', 'score'.\n\n"
        f"Transcript:\n{transcript_sample}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        text_content = result["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text_content)
        highlights = []
        for item in parsed:
            highlights.append(HighlightClip(
                start=float(item["start"]),
                end=float(item["end"]),
                title=str(item["title"]),
                reason=str(item["reason"]),
                score=float(item.get("score", 0.9))
            ))
        if highlights:
            return highlights
        raise ValueError("Empty highlight output from Gemini API")


def analyze_highlights(
    transcript_segments: list[CaptionSegment],
    api_key: str | None = None
) -> list[HighlightClip]:
    if not transcript_segments:
        return []

    active_key = api_key or os.environ.get("GEMINI_API_KEY")
    if active_key:
        try:
            return _gemini_highlights(transcript_segments, active_key)
        except Exception:
            pass

    return _heuristic_highlights(transcript_segments)

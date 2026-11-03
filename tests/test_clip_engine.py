import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipstudio.clip_engine import cut_clip, ensure_browser_playable, parse_json3, parse_vtt
from clipstudio.models import CaptionSegment, ClipRequest, Platform, VideoInfo


def test_models_validation():
    v = VideoInfo(
        video_id="youtube_dQw4w9WgXcQ",
        platform=Platform.YOUTUBE,
        title="Sample Video",
        duration=212.5,
        thumbnail="https://example.com/thumb.jpg",
        stream_url="https://example.com/stream.mp4"
    )
    assert v.platform is Platform.YOUTUBE

    with pytest.raises(ValidationError):
        VideoInfo(video_id="youtube_x", title="t", duration=1.0, thumbnail="")

    c = CaptionSegment(start=10.0, end=15.5, text="Hello world")
    assert c.end > c.start

    req = ClipRequest(video_id="kick_clip_01M64B2EM73ZDKW12FKGZNXER8", start_time=5.0, end_time=12.0)
    assert req.end_time > req.start_time

    for bad_key in ("abc", "vimeo_123", "youtube_../../etc"):
        with pytest.raises(ValidationError):
            ClipRequest(video_id=bad_key, start_time=5.0, end_time=12.0)


def _pairs(segments: list[CaptionSegment]) -> list[tuple[float, float, str]]:
    return [(s.start, s.end, s.text) for s in segments]


def test_parse_vtt_strips_markup_and_rolling_repeats():
    vtt = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.500 align:start position:0%
hello<00:00:01.500><c> there</c>

00:00:03.500 --> 00:00:03.510 align:start position:0%
hello there


00:00:03.510 --> 00:00:06.000 align:start position:0%
hello there
general<00:00:04.000><c> kenobi</c> &amp; co

01:02:03.250 --> 01:02:05.000
<v Speaker>Later line</v>
"""
    assert _pairs(parse_vtt(vtt)) == [
        (1.0, 3.5, "hello there"),
        (3.51, 6.0, "general kenobi & co"),
        (3723.25, 3725.0, "Later line"),
    ]


def test_parse_vtt_without_cues_is_empty():
    assert parse_vtt("WEBVTT\n\nNOTE nothing here\n") == []


def test_parse_json3_joins_segments_and_drops_blanks():
    raw = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "id": 1},
            {"tStartMs": 1000, "dDurationMs": 2500, "segs": [{"utf8": "All"}, {"utf8": " right,"}, {"utf8": " so"}]},
            {"tStartMs": 3500, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 3500, "dDurationMs": 1500, "segs": [{"utf8": "All right, so"}]},
            {"tStartMs": 5000, "dDurationMs": 1250, "segs": [{"utf8": "here we are\nin front"}]},
            {"tStartMs": 7000, "segs": [{"utf8": "of the elephants"}]},
        ]
    }
    assert _pairs(parse_json3(raw)) == [
        (1.0, 3.5, "All right, so"),
        (5.0, 6.25, "here we are in front"),
        (7.0, 7.0, "of the elephants"),
    ]


def test_cut_clip_ffmpeg_execution():
    with tempfile.TemporaryDirectory() as tmp_dir:
        src = Path(tmp_dir) / "synth_test.mp4"
        out = Path(tmp_dir) / "clipped.mp4"

        gen_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "mpeg4", "-c:a", "aac",
            str(src)
        ]
        p = subprocess.run(gen_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.returncode == 0, f"ffmpeg synth failed: {p.stderr.decode()}"
        result_path = cut_clip(str(src), 1.0, 3.5, str(out))
        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0


def _codec(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_cut_clip_output_is_h264():
    with tempfile.TemporaryDirectory() as tmp_dir:
        src = Path(tmp_dir) / "synth.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30", "-c:v", "mpeg4", str(src)],
            check=True, capture_output=True,
        )
        out = Path(cut_clip(str(src), 0.5, 2.0, str(Path(tmp_dir) / "out.mp4")))
        assert _codec(out) == "h264"


def test_unplayable_download_is_reencoded_to_h264_in_place():
    with tempfile.TemporaryDirectory() as tmp_dir:
        media = Path(tmp_dir) / "twitch_SomeClip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
             "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "mpeg4", "-c:a", "aac", str(media)],
            check=True, capture_output=True,
        )
        assert _codec(media) == "mpeg4"
        ensure_browser_playable(media)
        assert _codec(media) == "h264"
        assert sorted(p.name for p in Path(tmp_dir).iterdir()) == ["twitch_SomeClip.mp4"]

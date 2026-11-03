import os
import tempfile
import subprocess
from pathlib import Path
from models import CaptionSegment, ClipRequest, ClipResponse, HighlightClip, VideoInfo
from clip_engine import (
    analyze_highlights,
    cut_clip,
    extract_video_id,
    _build_candidate_windows,
    _heuristic_highlights,
)


def test_models_validation():
    v = VideoInfo(
        video_id="dQw4w9WgXcQ",
        title="Sample Video",
        duration=212.5,
        thumbnail="https://example.com/thumb.jpg",
        stream_url="https://example.com/stream.mp4"
    )
    assert v.video_id == "dQw4w9WgXcQ"

    c = CaptionSegment(start=10.0, end=15.5, text="Hello world")
    assert c.end > c.start

    req = ClipRequest(video_id="abc", start_time=5.0, end_time=12.0)
    assert req.end_time > req.start_time

    res = ClipResponse(
        clip_id="c1",
        file_path="/tmp/c1.mp4",
        download_url="/clips/c1.mp4",
        duration=7.0
    )
    assert res.duration == 7.0


def test_extract_video_id():
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_analyze_highlights_heuristic():
    sample_captions = [
        CaptionSegment(start=0.0, end=4.0, text="Welcome everyone to today's tutorial."),
        CaptionSegment(start=4.1, end=8.0, text="In this video we cover basic setup."),
        CaptionSegment(start=10.0, end=14.0, text="Here is the secret trick you must remember."),
        CaptionSegment(start=14.1, end=18.0, text="This strategy is an incredible breakthrough hack."),
        CaptionSegment(start=18.1, end=25.0, text="Never make this mistake in your deployment."),
        CaptionSegment(start=30.0, end=35.0, text="Moving on to the next section of the codebase."),
        CaptionSegment(start=35.1, end=40.0, text="Let's summarize the key rule."),
        CaptionSegment(start=40.1, end=48.0, text="Finally this reveals how you optimize performance.")
    ]

    highlights = analyze_highlights(sample_captions)
    assert len(highlights) >= 1
    for h in highlights:
        assert isinstance(h, HighlightClip)
        assert h.end > h.start
        assert 0.0 <= h.score <= 1.0
        assert len(h.title) > 0
        assert len(h.reason) > 0


def test_cut_clip_ffmpeg_execution():
    with tempfile.TemporaryDirectory() as tmp_dir:
        src = Path(tmp_dir) / "synth_test.mp4"
        out = Path(tmp_dir) / "clipped.mp4"

        gen_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "libx264", "-c:a", "aac",
            str(src)
        ]
        p = subprocess.run(gen_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.returncode == 0, f"ffmpeg synth failed: {p.stderr.decode()}"
        result_path = cut_clip(str(src), 1.0, 3.5, str(out))
        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0


if __name__ == "__main__":
    test_models_validation()
    test_extract_video_id()
    test_analyze_highlights_heuristic()
    test_cut_clip_ffmpeg_execution()
    print("ALL TESTS PASSED")

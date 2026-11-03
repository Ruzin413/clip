import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import highlights
from app import MEDIA_DIR, app
from hooks import HookDraft, build_prompt, check_grounded, hook_window, write_hook
from models import AIProvider, CaptionSegment

client = TestClient(app)

CAPTIONS = [
    CaptionSegment(start=0.0, end=4.0, text="welcome back everyone"),
    CaptionSegment(start=4.0, end=8.0, text="we lost the last two games"),
    CaptionSegment(start=8.0, end=12.0, text="so Marcus says we go all in"),
    CaptionSegment(start=12.0, end=16.0, text="this is round 13 and we have 800 credits"),
    CaptionSegment(start=16.0, end=20.0, text="okay everyone push B now"),
    CaptionSegment(start=20.0, end=24.0, text="no way that worked"),
]
LINES = [c.text for c in CAPTIONS]


def _fake_provider(monkeypatch, draft: HookDraft, seen: dict | None = None):
    def fake_call(request, api_key, model, base_url):
        if seen is not None:
            seen.update(system=request.system, prompt=request.prompt, output=request.output)
        return draft

    spec = highlights.PROVIDERS[AIProvider.OPENAI]
    monkeypatch.setitem(highlights.PROVIDERS, AIProvider.OPENAI, highlights.ProviderSpec(**{**spec.__dict__, "call": fake_call}))
    monkeypatch.setenv("OPENAI_MODEL", "fake-model")


def test_window_is_the_clip_plus_three_lines_before():
    before, inside = hook_window(CAPTIONS, 16.0, 24.0)
    assert [c.text for c in before] == LINES[1:4]
    assert [c.text for c in inside] == LINES[4:6]


def test_prompt_marks_lines_before_the_clip():
    before, inside = hook_window(CAPTIONS, 16.0, 24.0)
    prompt = build_prompt(before, inside)
    assert "[0] (before) we lost the last two games" in prompt
    assert "[3] okay everyone push B now" in prompt


def test_grounded_hook_is_cleaned_and_kept():
    draft = HookDraft(text='  "Round 13, 800 credits, and Marcus wants to go all in"  ', source_lines=[2, 3])
    assert check_grounded(draft, LINES) == "Round 13, 800 credits, and Marcus wants to go all in"


def test_sentence_starters_are_not_treated_as_facts():
    assert check_grounded(HookDraft(text="They go all in on round 13", source_lines=[3]), LINES)


@pytest.mark.parametrize("text, invented", [
    ("Shroud goes all in on round 13", "Shroud"),
    ("Then Valorant pros push B", "Valorant"),
    ("Round 14 and Marcus goes all in", "14"),
    ("They have 900 credits left", "900"),
])
def test_invented_names_and_numbers_are_rejected(text, invented):
    with pytest.raises(highlights.ProviderCallError, match=invented):
        check_grounded(HookDraft(text=text, source_lines=[3]), LINES)


def test_long_hooks_and_missing_citations_are_rejected():
    with pytest.raises(highlights.ProviderCallError, match="12 words"):
        check_grounded(HookDraft(text=" ".join(["go"] * 13), source_lines=[0]), LINES)
    with pytest.raises(highlights.ProviderCallError, match="do not exist"):
        check_grounded(HookDraft(text="they go all in", source_lines=[99]), LINES)
    with pytest.raises(highlights.ProviderCallError, match="do not exist"):
        check_grounded(HookDraft(text="they go all in", source_lines=[]), LINES)


def test_local_provider_cannot_write_hooks():
    with pytest.raises(highlights.ProviderConfigError, match="needs an AI provider"):
        write_hook(CAPTIONS, 16.0, 24.0, AIProvider.LOCAL)


def test_range_without_subtitles_cannot_get_a_hook():
    with pytest.raises(highlights.ProviderConfigError, match="no subtitles"):
        write_hook(CAPTIONS, 30.0, 40.0, AIProvider.LOCAL)


def test_write_hook_sends_the_rules_and_window(monkeypatch):
    seen = {}
    _fake_provider(monkeypatch, HookDraft(text="Round 13 and the team goes all in", source_lines=[3]), seen)
    provider, text = write_hook(CAPTIONS, 16.0, 24.0, AIProvider.OPENAI)
    assert provider is AIProvider.OPENAI and text == "Round 13 and the team goes all in"
    assert "Use only facts stated in these lines" in seen["system"]
    assert "welcome back everyone" not in seen["prompt"]
    assert seen["output"] is HookDraft


def _hook_band(path: Path, at: float) -> bytes:
    """Grayscale pixels of the top quarter of the frame, where the hook is drawn."""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
         "-vf", "crop=iw:ih/4:0:0,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout


def _band_difference(a: Path, b: Path, at: float) -> float:
    x, y = _hook_band(a, at), _hook_band(b, at)
    return sum(abs(p - q) for p, q in zip(x, y)) / len(x)


def test_export_burns_the_hook_into_the_first_two_seconds(monkeypatch):
    _fake_provider(monkeypatch, HookDraft(text="They lost the last two games", source_lines=[1]))
    key = "youtube_hooksynth"
    src = MEDIA_DIR / f"{key}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=25",
         "-f", "lavfi", "-i", "sine=duration=8", "-c:v", "mpeg4", "-q:v", "3", "-c:a", "aac", str(src)],
        check=True, capture_output=True,
    )
    try:
        captions = [c.model_dump() for c in CAPTIONS]
        plain = client.post("/api/clip", json={"video_id": key, "start_time": 0, "end_time": 5})
        hooked = client.post("/api/clip", json={
            "video_id": key, "start_time": 0, "end_time": 5,
            "hook": {"captions": captions, "provider": "openai"},
        })
        assert plain.status_code == 200 and plain.json()["hook"] is None
        assert hooked.status_code == 200, hooked.text
        assert hooked.json()["hook"] == "They lost the last two games"
        plain_path, hooked_path = Path(plain.json()["file_path"]), Path(hooked.json()["file_path"])
        shown, gone = _band_difference(plain_path, hooked_path, 0.5), _band_difference(plain_path, hooked_path, 3.0)
        assert shown > 10, f"hook visible at 0.5 s (mean difference {shown:.1f})"
        assert gone < 2, f"hook gone by 3 s (mean difference {gone:.1f})"
        plain_path.unlink()
        hooked_path.unlink()
    finally:
        src.unlink(missing_ok=True)


def test_rejected_hook_fails_the_export_with_the_reason(monkeypatch):
    _fake_provider(monkeypatch, HookDraft(text="Shroud says welcome back", source_lines=[0]))
    key = "youtube_hookreject"
    src = MEDIA_DIR / f"{key}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=25", "-c:v", "mpeg4", str(src)],
        check=True, capture_output=True,
    )
    try:
        res = client.post("/api/clip", json={
            "video_id": key, "start_time": 0, "end_time": 2,
            "hook": {"captions": [c.model_dump() for c in CAPTIONS], "provider": "openai"},
        })
        assert res.status_code == 502
        assert "Shroud" in res.json()["detail"]
    finally:
        src.unlink(missing_ok=True)

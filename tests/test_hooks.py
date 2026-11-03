import pytest

from clipstudio import highlights
from clipstudio.hooks import HookDraft, build_prompt, check_grounded, hook_window, write_hook
from clipstudio.models import AIProvider, CaptionSegment

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

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clipstudio import highlights
from clipstudio.highlights import ProviderCallError, ProviderConfigError, find_highlights, list_providers, resolve_provider
from clipstudio.models import AIProvider, CaptionSegment

PROVIDER_ENV = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
]

# Every caption runs at least 5 seconds, so each one is its own numbered line.
LONG_CAPTIONS = [
    CaptionSegment(start=1.37, end=7.12, text="okay chat we are loading into the final round"),
    CaptionSegment(start=7.12, end=13.4, text="he has no idea I am behind him"),
    CaptionSegment(start=13.58, end=19.93, text="one tap no way that actually happened"),
    CaptionSegment(start=20.05, end=26.71, text="clip that clip that right now"),
    CaptionSegment(start=27.3, end=33.06, text="alright back to the lobby"),
    CaptionSegment(start=33.5, end=40.25, text="thanks for the sub appreciate it"),
]

OLD_SAMPLE_CAPTIONS = [
    CaptionSegment(start=0.0, end=4.0, text="Welcome everyone to today's tutorial."),
    CaptionSegment(start=4.1, end=8.0, text="In this video we cover basic setup."),
    CaptionSegment(start=10.0, end=14.0, text="Here is the secret trick you must remember."),
    CaptionSegment(start=14.1, end=18.0, text="This strategy is an incredible breakthrough hack."),
    CaptionSegment(start=18.1, end=25.0, text="Never make this mistake in your deployment."),
    CaptionSegment(start=30.0, end=35.0, text="Moving on to the next section of the codebase."),
    CaptionSegment(start=35.1, end=40.0, text="Let's summarize the key rule."),
    CaptionSegment(start=40.1, end=48.0, text="Finally this reveals how you optimize performance."),
]


@dataclass
class Captured:
    method: str
    path: str
    headers: dict[str, str]
    body: dict


@dataclass
class FakeServer:
    url: str
    requests: list[Captured] = field(default_factory=list)
    reply: Callable[[Captured], tuple[int, dict]] = lambda req: (500, {"error": "no reply configured"})

    @property
    def last(self) -> Captured:
        assert self.requests, "the provider never reached the fake server"
        return self.requests[-1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server():
    fake = FakeServer(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            captured = Captured(
                method="POST",
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=json.loads(raw or b"{}"),
            )
            fake.requests.append(captured)
            status, payload = fake.reply(captured)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()


def line_of(prompt: str, phrase: str) -> int:
    match = re.search(rf"^\[(\d+)\] [^\n]*{re.escape(phrase)}(?!\w)", prompt, re.MULTILINE)
    assert match, f"{phrase!r} is not on a numbered transcript line"
    return int(match.group(1))


def plan_reply(prompt: str) -> dict:
    return {"highlights": [
        {
            "start_line": line_of(prompt, "thanks for the sub"),
            "end_line": line_of(prompt, "thanks for the sub"),
            "title": "Sub thanks",
            "reason": "Viewer shoutout",
            "score": 0.4,
        },
        {
            "start_line": line_of(prompt, "he has no idea"),
            "end_line": line_of(prompt, "clip that clip that"),
            "title": "The one tap",
            "reason": "Peak reaction",
            "score": 0.95,
        },
    ]}


def claude_message(text: str | None, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [] if text is None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def claude_prompt(req: Captured) -> str:
    content = req.body["messages"][0]["content"]
    return content if isinstance(content, str) else content[0]["text"]


def assert_long_caption_plan(clips):
    assert [(c.start, c.end, c.title, c.score) for c in clips] == [
        (7.12, 26.71, "The one tap", 0.95),
        (33.5, 40.25, "Sub thanks", 0.4),
    ]


def test_claude_sends_structured_request_and_maps_spans_to_caption_times(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    server.reply = lambda req: (200, claude_message(json.dumps(plan_reply(claude_prompt(req)))))

    provider, clips = find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, api_key="user-key")

    assert provider is AIProvider.CLAUDE
    assert_long_caption_plan(clips)
    req = server.last
    assert req.path.startswith("/v1/messages")
    assert req.headers["authorization"] == "Bearer user-key"
    assert "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    assert req.body["model"] == "claude-opus-5-5"
    assert req.body["max_tokens"] == 16000
    assert req.body["fallbacks"] == "default"
    assert req.body["output_config"]["effort"] == "medium"
    schema = req.body["output_config"]["format"]["schema"]
    assert req.body["output_config"]["format"]["type"] == "json_schema"
    assert "highlights" in schema["properties"]
    prompt = claude_prompt(req)
    assert line_of(prompt, "okay chat we are loading") == 0
    assert line_of(prompt, "thanks for the sub") == 5


def test_claude_uses_server_key_and_custom_model(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    server.reply = lambda req: (200, claude_message(json.dumps(plan_reply(claude_prompt(req)))))

    find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, model="claude-sonnet-5-5")

    assert server.last.headers["authorization"] == "Bearer env-key"
    assert server.last.body["model"] == "claude-sonnet-5-5"


def test_claude_query_reaches_the_prompt(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    server.reply = lambda req: (200, claude_message(json.dumps(plan_reply(claude_prompt(req)))))

    find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, api_key="k", query="  the one tap  ")

    assert "the one tap" in claude_prompt(server.last)


@pytest.mark.parametrize("status, error", [
    (401, ProviderConfigError),
    (403, ProviderConfigError),
    (400, ProviderCallError),
])
def test_claude_http_errors_map_to_provider_errors(server, monkeypatch, status, error):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    server.reply = lambda req: (status, {"type": "error", "error": {"type": "x", "message": "nope"}})

    with pytest.raises(error):
        find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, api_key="k")


@pytest.mark.parametrize("text", [
    None,
    '{"highlights": [{"start_line": 0',
    '{"highlights": [{"start_line": 0, "end_line": 1, "title": "t", "reason": "r", "score": 1}]}',
])
def test_claude_refusal_raises_call_error(server, monkeypatch, text):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    server.reply = lambda req: (200, claude_message(text, stop_reason="refusal"))

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, api_key="k")


def test_claude_unreachable_raises_call_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.CLAUDE, api_key="k")


def gemini_prompt(req: Captured) -> str:
    return req.body["contents"][0]["parts"][0]["text"]


def gemini_reply(req: Captured) -> tuple[int, dict]:
    text = json.dumps(plan_reply(gemini_prompt(req)))
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_sends_key_header_and_schema(server, monkeypatch):
    monkeypatch.setattr(highlights, "GEMINI_API_ROOT", f"{server.url}/v1beta")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    server.reply = gemini_reply

    provider, clips = find_highlights(LONG_CAPTIONS, AIProvider.GEMINI)

    assert provider is AIProvider.GEMINI
    assert_long_caption_plan(clips)
    req = server.last
    assert req.path == "/v1beta/models/gemini-2.5-flash:generateContent"
    assert "key=" not in req.path
    assert req.headers["x-goog-api-key"] == "gem-key"
    config = req.body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "highlights" in config["responseSchema"]["properties"]
    assert line_of(gemini_prompt(req), "okay chat we are loading") == 0


def test_gemini_blocked_prompt_raises_call_error(server, monkeypatch):
    monkeypatch.setattr(highlights, "GEMINI_API_ROOT", server.url)
    server.reply = lambda req: (200, {"promptFeedback": {"blockReason": "SAFETY"}})

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.GEMINI, api_key="k")


def test_gemini_rejected_key_raises_config_error(server, monkeypatch):
    monkeypatch.setattr(highlights, "GEMINI_API_ROOT", server.url)
    server.reply = lambda req: (403, {"error": {"message": "permission denied"}})

    with pytest.raises(ProviderConfigError):
        find_highlights(LONG_CAPTIONS, AIProvider.GEMINI, api_key="bad")


def openai_prompt(req: Captured) -> str:
    return req.body["messages"][-1]["content"]


def openai_reply_with(plan: Callable[[str], dict]):
    def reply(req: Captured) -> tuple[int, dict]:
        content = json.dumps(plan(openai_prompt(req)))
        return 200, {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return reply


def test_openai_compatible_posts_chat_completion(server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    server.reply = openai_reply_with(plan_reply)

    provider, clips = find_highlights(
        LONG_CAPTIONS, AIProvider.OPENAI, model="gpt-test", base_url=f"{server.url}/v1/",
    )

    assert provider is AIProvider.OPENAI
    assert_long_caption_plan(clips)
    req = server.last
    assert req.path == "/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer oa-key"
    assert req.body["model"] == "gpt-test"
    assert req.body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in req.body["messages"]] == ["system", "user"]
    assert line_of(openai_prompt(req), "okay chat we are loading") == 0


def test_openai_compatible_reads_env_and_works_without_key(server, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", f"{server.url}/api")
    monkeypatch.setenv("OPENAI_MODEL", "llama3.1")
    server.reply = openai_reply_with(plan_reply)

    _, clips = find_highlights(LONG_CAPTIONS, AIProvider.OPENAI)

    assert_long_caption_plan(clips)
    assert server.last.path == "/api/chat/completions"
    assert server.last.body["model"] == "llama3.1"
    assert "authorization" not in server.last.headers


def test_openai_server_error_raises_call_error(server):
    server.reply = lambda req: (500, {"error": "boom"})

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.OPENAI, model="m", base_url=server.url)


def test_openai_non_plan_json_raises_call_error(server):
    server.reply = openai_reply_with(lambda prompt: {"clips": []})

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.OPENAI, model="m", base_url=server.url)


def test_invalid_spans_are_dropped_scores_clamped_and_clips_sorted(server):
    server.reply = openai_reply_with(lambda prompt: {"highlights": [
        {"start_line": 4, "end_line": 2, "title": "reversed", "reason": "r", "score": 0.5},
        {"start_line": 0, "end_line": 99, "title": "past the end", "reason": "r", "score": 0.5},
        {"start_line": -1, "end_line": 0, "title": "negative", "reason": "r", "score": 0.5},
        {"start_line": 3, "end_line": 4, "title": "late", "reason": "r", "score": 1.7},
        {"start_line": 1, "end_line": 1, "title": "early", "reason": "r", "score": -0.2},
    ]})

    _, clips = find_highlights(LONG_CAPTIONS, AIProvider.OPENAI, model="m", base_url=server.url)

    assert [(c.title, c.start, c.end, c.score) for c in clips] == [
        ("early", 7.12, 13.4, 0.0),
        ("late", 20.05, 33.06, 1.0),
    ]


def test_only_invalid_spans_raises_call_error(server):
    server.reply = openai_reply_with(lambda prompt: {"highlights": [
        {"start_line": 5, "end_line": 1, "title": "t", "reason": "r", "score": 0.5},
        {"start_line": 6, "end_line": 6, "title": "t", "reason": "r", "score": 0.5},
    ]})

    with pytest.raises(ProviderCallError):
        find_highlights(LONG_CAPTIONS, AIProvider.OPENAI, model="m", base_url=server.url)


def test_short_captions_merge_into_lines_that_keep_real_boundaries(server):
    captions = [
        CaptionSegment(start=round(0.4 + 2.1 * i, 2), end=round(2.3 + 2.1 * i, 2), text=f"caption number {i}")
        for i in range(60)
    ]
    seen = {}

    def plan(prompt):
        seen["lines"] = len(re.findall(r"^\[\d+\]", prompt, re.MULTILINE))
        return {"highlights": [
            {"start_line": 0, "end_line": seen["lines"] - 1, "title": "all", "reason": "r", "score": 1},
            {
                "start_line": line_of(prompt, "caption number 20"),
                "end_line": line_of(prompt, "caption number 29"),
                "title": "middle", "reason": "r", "score": 1,
            },
        ]}
    server.reply = openai_reply_with(plan)

    _, clips = find_highlights(captions, AIProvider.OPENAI, model="m", base_url=server.url)

    assert seen["lines"] < len(captions) / 2
    whole, middle = clips
    assert (whole.start, whole.end) == (captions[0].start, captions[-1].end)
    assert middle.start in {c.start for c in captions}
    assert middle.end in {c.end for c in captions}
    assert middle.start <= captions[20].start and middle.end >= captions[29].end


@pytest.mark.parametrize("env, expected", [
    ({}, AIProvider.LOCAL),
    ({"GEMINI_API_KEY": "g"}, AIProvider.GEMINI),
    ({"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}, AIProvider.CLAUDE),
    ({"OPENAI_API_KEY": "o"}, AIProvider.LOCAL),
    ({"OPENAI_API_KEY": "o", "OPENAI_MODEL": "gpt-test"}, AIProvider.OPENAI),
    ({"OPENAI_API_KEY": "o", "OPENAI_MODEL": "gpt-test", "GEMINI_API_KEY": "g"}, AIProvider.GEMINI),
    ({"ANTHROPIC_API_KEY": ""}, AIProvider.LOCAL),
])
def test_auto_picks_first_provider_with_a_server_key(monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert resolve_provider(AIProvider.AUTO) is expected
    assert resolve_provider(AIProvider.GEMINI) is AIProvider.GEMINI


def test_auto_runs_the_resolved_provider(server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("OPENAI_BASE_URL", server.url)
    server.reply = openai_reply_with(plan_reply)

    provider, clips = find_highlights(LONG_CAPTIONS, AIProvider.AUTO)

    assert provider is AIProvider.OPENAI
    assert_long_caption_plan(clips)


def test_auto_without_keys_runs_local(monkeypatch):
    provider, clips = find_highlights(OLD_SAMPLE_CAPTIONS, AIProvider.AUTO)

    assert provider is AIProvider.LOCAL
    assert clips


@pytest.mark.parametrize("provider, env_var", [
    (AIProvider.CLAUDE, "ANTHROPIC_API_KEY"),
    (AIProvider.GEMINI, "GEMINI_API_KEY"),
])
def test_missing_required_key_names_the_env_var(provider, env_var):
    with pytest.raises(ProviderConfigError, match=env_var):
        find_highlights(LONG_CAPTIONS, provider)


def test_openai_without_model_raises_config_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")

    with pytest.raises(ProviderConfigError, match="OPENAI_MODEL"):
        find_highlights(LONG_CAPTIONS, AIProvider.OPENAI)


def test_list_providers_reports_server_configuration(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_MODEL", "llama3.1")

    infos = {info.id: info for info in list_providers()}

    assert set(infos) == {AIProvider.CLAUDE, AIProvider.GEMINI, AIProvider.OPENAI, AIProvider.LOCAL}
    assert infos[AIProvider.GEMINI].has_server_key and not infos[AIProvider.CLAUDE].has_server_key
    assert infos[AIProvider.CLAUDE].needs_key and infos[AIProvider.CLAUDE].default_model == "claude-opus-5-5"
    assert infos[AIProvider.GEMINI].default_model == "gemini-2.5-flash"
    openai = infos[AIProvider.OPENAI]
    assert (openai.needs_key, openai.uses_base_url, openai.default_model, openai.default_base_url) == (
        False, True, "llama3.1", "https://api.openai.com/v1",
    )
    assert not infos[AIProvider.LOCAL].needs_key


def test_local_heuristic_on_sample_captions_returns_caption_aligned_clips():
    provider, clips = find_highlights(OLD_SAMPLE_CAPTIONS, AIProvider.LOCAL)

    assert provider is AIProvider.LOCAL
    assert clips
    starts = {c.start for c in OLD_SAMPLE_CAPTIONS}
    ends = {c.end for c in OLD_SAMPLE_CAPTIONS}
    for clip in clips:
        assert clip.start in starts and clip.end in ends
        assert clip.end - clip.start >= 12.0
        assert 0.0 <= clip.score <= 1.0
        assert clip.title and clip.reason
    for earlier, later in zip(clips, clips[1:]):
        assert earlier.end <= later.start


def test_local_query_steers_toward_matching_words():
    captions = [
        CaptionSegment(start=4.0 * i, end=4.0 * i + 3.9, text=f"just some ordinary chatter line {i}")
        for i in range(80)
    ]
    captions[1] = CaptionSegment(start=4.0, end=7.9, text="the secret trick is an incredible breakthrough hack")
    captions[62] = CaptionSegment(start=248.0, end=251.9, text="we ordered the pizza")
    pizza = captions[62]

    def covers_pizza(clips):
        return any(c.start <= pizza.start and c.end >= pizza.end for c in clips)

    _, plain = find_highlights(captions, AIProvider.LOCAL)
    _, steered = find_highlights(captions, AIProvider.LOCAL, query="the pizza moment")

    assert not covers_pizza(plain)
    assert covers_pizza(steered)


def test_local_short_transcript_returns_overview_clip():
    captions = [
        CaptionSegment(start=2.0, end=5.5, text="hi"),
        CaptionSegment(start=5.6, end=9.0, text="bye"),
    ]

    _, clips = find_highlights(captions, AIProvider.LOCAL)

    assert [(c.start, c.end) for c in clips] == [(2.0, 9.0)]


def test_empty_captions_return_no_clips_without_calling_out():
    assert find_highlights([], AIProvider.LOCAL) == (AIProvider.LOCAL, [])
    assert find_highlights([], AIProvider.OPENAI, model="m", base_url="http://127.0.0.1:9") == (
        AIProvider.OPENAI, [],
    )

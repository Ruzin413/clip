import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import anthropic
from pydantic import ValidationError

from models import AIProvider, CaptionSegment, HighlightClip, HighlightPlan, LineSpan, ProviderInfo


class ProviderConfigError(ValueError):
    """The chosen provider cannot run as configured: a missing or rejected key, or no model."""


class ProviderCallError(RuntimeError):
    """The provider failed upstream or returned nothing usable."""


@dataclass(frozen=True)
class TranscriptLine:
    start: float
    end: float
    text: str


LINE_MIN_SECONDS = 5.0
LINE_MAX_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 180.0
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

EXCITEMENT_KEYWORDS = {
    "key", "secret", "crucial", "important", "remember", "insane", "crazy",
    "incredible", "amazing", "tip", "trick", "first", "finally", "reveal",
    "hack", "mistake", "truth", "best", "worst", "lesson", "how", "why",
    "rule", "strategy", "breakthrough", "warning", "never", "always"
}
QUERY_STOPWORDS = {
    "a", "an", "and", "are", "about", "for", "from", "in", "is", "it", "of", "on",
    "or", "the", "that", "this", "to", "was", "where", "when", "with", "moment", "moments",
    "clip", "clips", "part", "parts", "find", "show", "me",
}

SYSTEM_PROMPT = """You are a video editor who finds the moments in a video worth clipping.

You receive the video's transcript as numbered lines shaped like `[N] start-end text`. The times are there only so you can judge how long a range runs.

Report each clip as an inclusive range of line numbers: start_line is the first line of the clip and end_line is the last. Use only line numbers that appear in the transcript, and never write times yourself.

A good clip stands on its own. It starts where a thought or a moment starts, ends once it lands, and makes sense to a viewer who has not seen the rest of the video.

For each clip give a short, punchy title, one sentence on why it is worth clipping, and a score from 0 to 1 for how strong it is.

Reply with JSON only, shaped as {"highlights": [{"start_line": <int>, "end_line": <int>, "title": <string>, "reason": <string>, "score": <number>}]}."""


def build_transcript(captions: Sequence[CaptionSegment]) -> list[TranscriptLine]:
    lines: list[TranscriptLine] = []
    group: list[CaptionSegment] = []
    for caption in sorted(captions, key=lambda c: c.start):
        if group and (
            group[-1].end - group[0].start >= LINE_MIN_SECONDS
            or caption.end - group[0].start > LINE_MAX_SECONDS
        ):
            lines.append(_merge(group))
            group = []
        group.append(caption)
    if group:
        lines.append(_merge(group))
    return lines


def _merge(group: list[CaptionSegment]) -> TranscriptLine:
    text = " ".join(" ".join(c.text.split()) for c in group)
    return TranscriptLine(start=group[0].start, end=group[-1].end, text=text)


def _clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def numbered_transcript(lines: Sequence[TranscriptLine]) -> str:
    return "\n".join(
        f"[{i}] {_clock(line.start)}-{_clock(line.end)} {line.text}" for i, line in enumerate(lines)
    )


def build_user_prompt(lines: Sequence[TranscriptLine], query: str | None) -> str:
    if query:
        task = (
            f"Find the moments that match this request: {query}\n"
            "Return the clips that best match it, strongest first, each about 15 to 90 seconds long."
        )
    else:
        task = "Pick the best 3 to 8 standalone clips, each about 15 to 90 seconds long."
    return f"{task}\n\nTranscript:\n{numbered_transcript(lines)}"


def plan_to_clips(plan: HighlightPlan, lines: Sequence[TranscriptLine]) -> list[HighlightClip]:
    clips = []
    for span in plan.highlights:
        if not 0 <= span.start_line <= span.end_line < len(lines):
            continue
        start, end = lines[span.start_line].start, lines[span.end_line].end
        if end <= start:
            continue
        clips.append(HighlightClip(
            start=start,
            end=end,
            title=span.title,
            reason=span.reason,
            score=min(max(span.score, 0.0), 1.0),
        ))
    if not clips:
        raise ProviderCallError("The AI returned no highlight that points at real transcript lines.")
    return sorted(clips, key=lambda c: c.start)


def _words(text: str) -> list[str]:
    return [w.lower().strip(".,!?:;\"'()") for w in text.split()]


@dataclass(frozen=True)
class _Window:
    first: int
    last: int
    title: str
    score: float
    keyword_hits: int
    query_hits: int


def _candidate_windows(
    lines: Sequence[TranscriptLine],
    query_words: set[str],
    target_duration: float = 30.0,
    min_duration: float = 12.0,
) -> list[_Window]:
    windows = []
    n = len(lines)
    for i in range(n):
        start = lines[i].start
        for j in range(i, n):
            duration = lines[j].end - start
            if duration >= target_duration or j == n - 1:
                if duration >= min_duration:
                    words = _words(" ".join(line.text for line in lines[i:j + 1]))
                    wps = len(words) / max(duration, 1.0)
                    keyword_hits = sum(1 for w in words if w in EXCITEMENT_KEYWORDS)
                    score = min(0.3 + (wps / 6.0) * 0.35 + min(keyword_hits * 0.08, 0.35), 1.0)
                    title = " ".join(lines[i].text.split()[:6]).capitalize()
                    if len(title) > 40:
                        title = title[:37] + "..."
                    windows.append(_Window(
                        first=i,
                        last=j,
                        title=title or "Key Insight",
                        score=round(score, 2),
                        keyword_hits=keyword_hits,
                        query_hits=len(query_words.intersection(words)),
                    ))
                break
    return windows


def _local_plan(
    lines: Sequence[TranscriptLine],
    query: str | None,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
    count: int = 4,
) -> HighlightPlan:
    query_words = {w for w in _words(query or "") if w and w not in QUERY_STOPWORDS}
    candidates = _candidate_windows(lines, query_words)
    if not candidates:
        last = max(
            (i for i, line in enumerate(lines) if line.end <= lines[0].start + 30.0),
            default=0,
        )
        return HighlightPlan(highlights=[LineSpan(
            start_line=0,
            end_line=last,
            title="Overview Clip",
            reason="Generated from primary video content segment.",
            score=0.85,
        )])

    candidates.sort(key=lambda w: (w.query_hits, w.score), reverse=True)
    selected: list[_Window] = []
    for cand in candidates:
        if all(cand.last < s.first or cand.first > s.last for s in selected):
            selected.append(cand)
            if len(selected) >= count:
                break

    return HighlightPlan(highlights=[
        LineSpan(
            start_line=w.first,
            end_line=w.last,
            title=w.title,
            reason=(
                f"Mentions {w.query_hits} of your search words with {w.keyword_hits} topical cues."
                if w.query_hits
                else f"High semantic density with {w.keyword_hits} topical cues."
            ),
            score=w.score,
        )
        for w in selected
    ])


def _call_claude(
    lines: Sequence[TranscriptLine],
    query: str | None,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> HighlightPlan:
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(lines, query)}],
            output_format=HighlightPlan,
            output_config={"effort": "medium"},
            betas=[CLAUDE_FALLBACK_BETA],
            fallbacks="default",
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ProviderConfigError(f"Claude rejected the API key: {exc.message}") from exc
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise ProviderCallError(f"Claude request failed: {exc}") from exc
    except ValidationError as exc:
        # parse() validates text eagerly, so a refusal or truncation that left partial text lands here.
        raise ProviderCallError("Claude stopped before returning a complete highlight plan.") from exc
    if response.stop_reason == "refusal":
        raise ProviderCallError("Claude declined to analyze this transcript.")
    if response.parsed_output is None:
        raise ProviderCallError(f"Claude returned no highlight plan (stop reason {response.stop_reason}).")
    return response.parsed_output


def _post_json(url: str, payload: dict, headers: dict[str, str], provider_label: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        error = ProviderConfigError if exc.code in (401, 403) else ProviderCallError
        raise error(f"{provider_label} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderCallError(f"Could not reach {provider_label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderCallError(f"{provider_label} sent a response that is not JSON.") from exc


def _parse_plan(text: str, provider_label: str) -> HighlightPlan:
    try:
        return HighlightPlan.model_validate_json(text)
    except ValidationError as exc:
        raise ProviderCallError(f"{provider_label} returned JSON that is not a highlight plan.") from exc


GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "highlights": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_line": {"type": "INTEGER"},
                    "end_line": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                    "score": {"type": "NUMBER"},
                },
                "required": ["start_line", "end_line", "title", "reason", "score"],
            },
        },
    },
    "required": ["highlights"],
}


def _call_gemini(
    lines: Sequence[TranscriptLine],
    query: str | None,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> HighlightPlan:
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(lines, query)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA,
        },
    }
    result = _post_json(
        f"{GEMINI_API_ROOT}/models/{model}:generateContent",
        payload,
        {"x-goog-api-key": api_key or ""},
        "Gemini",
    )
    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        reason = (result.get("promptFeedback") or {}).get("blockReason") or "no candidates"
        raise ProviderCallError(f"Gemini returned no highlight plan ({reason}).") from exc
    return _parse_plan(text, "Gemini")


def _call_openai(
    lines: Sequence[TranscriptLine],
    query: str | None,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> HighlightPlan:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(lines, query)},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    result = _post_json(f"{base_url}/chat/completions", payload, headers, "The OpenAI-compatible server")
    try:
        text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderCallError("The OpenAI-compatible server returned no message.") from exc
    if not isinstance(text, str):
        raise ProviderCallError("The OpenAI-compatible server returned an empty message.")
    return _parse_plan(text, "The OpenAI-compatible server")


ProviderCall = Callable[
    [Sequence[TranscriptLine], str | None, str | None, str | None, str | None], HighlightPlan
]


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    call: ProviderCall
    key_env: str | None = None
    needs_key: bool = False
    default_model: str | None = None
    model_env: str | None = None
    base_url_env: str | None = None
    default_base_url: str | None = None

    @property
    def uses_base_url(self) -> bool:
        return self.default_base_url is not None


PROVIDERS: dict[AIProvider, ProviderSpec] = {
    AIProvider.CLAUDE: ProviderSpec(
        label="Claude",
        call=_call_claude,
        key_env="ANTHROPIC_API_KEY",
        needs_key=True,
        default_model="claude-opus-5-5",
    ),
    AIProvider.GEMINI: ProviderSpec(
        label="Gemini",
        call=_call_gemini,
        key_env="GEMINI_API_KEY",
        needs_key=True,
        default_model="gemini-2.5-flash",
    ),
    AIProvider.OPENAI: ProviderSpec(
        label="OpenAI-compatible",
        call=_call_openai,
        key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
        base_url_env="OPENAI_BASE_URL",
        default_base_url=OPENAI_DEFAULT_BASE_URL,
    ),
    AIProvider.LOCAL: ProviderSpec(label="Local keyword heuristic", call=_local_plan),
}


def _env(name: str | None) -> str | None:
    return (os.environ.get(name) or None) if name else None


def _server_model(spec: ProviderSpec) -> str | None:
    return _env(spec.model_env) or spec.default_model


def _server_base_url(spec: ProviderSpec) -> str | None:
    return _env(spec.base_url_env) or spec.default_base_url


def resolve_provider(provider: AIProvider) -> AIProvider:
    """AUTO becomes the first AI provider the server can run on its own, else LOCAL."""
    if provider is not AIProvider.AUTO:
        return provider
    return next(
        (
            p for p, spec in PROVIDERS.items()
            if _env(spec.key_env) and _server_model(spec)
        ),
        AIProvider.LOCAL,
    )


def list_providers() -> list[ProviderInfo]:
    return [
        ProviderInfo(
            id=provider,
            label=spec.label,
            has_server_key=_env(spec.key_env) is not None,
            needs_key=spec.needs_key,
            default_model=_server_model(spec),
            uses_base_url=spec.uses_base_url,
            default_base_url=_server_base_url(spec),
        )
        for provider, spec in PROVIDERS.items()
    ]


def find_highlights(
    captions: Sequence[CaptionSegment],
    provider: AIProvider,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    query: str | None = None,
) -> tuple[AIProvider, list[HighlightClip]]:
    resolved = resolve_provider(provider)
    spec = PROVIDERS[resolved]

    key = api_key or _env(spec.key_env)
    if spec.needs_key and not key:
        raise ProviderConfigError(
            f"{spec.label} needs an API key. Enter one or set {spec.key_env} on the server."
        )
    chosen_model = model or _server_model(spec)
    if spec.model_env and not chosen_model:
        raise ProviderConfigError(
            f"{spec.label} needs a model name. Enter one or set {spec.model_env} on the server."
        )
    chosen_base_url = (base_url or _server_base_url(spec) or "").rstrip("/") or None

    lines = build_transcript(captions)
    if not lines:
        return resolved, []
    plan = spec.call(lines, (query or "").strip() or None, key, chosen_model, chosen_base_url)
    return resolved, plan_to_clips(plan, lines)

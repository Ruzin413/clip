import re
from collections.abc import Sequence

from pydantic import BaseModel

from highlights import NO_SAVED, LLMRequest, ProviderCallError, ProviderConfigError, SavedSettings, resolve_call
from models import AIProvider, CaptionSegment

LINES_BEFORE = 3
MAX_WORDS = 12

SYSTEM_PROMPT = """You write a context hook: one line shown on screen during the first two seconds of a short clip, so a viewer who never saw the full video understands what they are about to watch.

You get numbered transcript lines. Lines marked (before) come just before the clip starts. The other lines are the clip itself.

Rules:
1. Use only facts stated in these lines. Do not add names, numbers, places, events, motives or claims that the lines do not state.
2. Name a person, game, product or place only if that exact name appears in the lines. Otherwise describe them plainly, such as "the streamer".
3. Copy any number exactly as it appears in the lines.
4. Set up the moment. Do not reveal how it ends.
5. One line of at most 12 words. No hashtags, emoji or quotation marks.
6. In source_lines, list the number of every line the hook relies on.

Reply with JSON only, shaped as {"text": <string>, "source_lines": [<int>, ...]}."""


class HookDraft(BaseModel):
    text: str
    source_lines: list[int]


GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "text": {"type": "STRING"},
        "source_lines": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
    "required": ["text", "source_lines"],
}

# Numbers and capitalized words are the facts a hook could invent. Sentence starters carry none.
_FACT = re.compile(r"\d[\d,.:%]*|\b[A-Z][\w'’-]*")
_STARTERS = {
    "a", "an", "the", "this", "that", "these", "those", "and", "but", "so", "when", "after", "before",
    "while", "as", "if", "then", "now", "here", "what", "why", "how", "who", "i", "he", "she", "they",
    "it", "we", "you", "his", "her", "their", "its", "our", "your", "one", "everyone", "nobody",
}


def hook_window(
    captions: Sequence[CaptionSegment], start: float, end: float
) -> tuple[list[CaptionSegment], list[CaptionSegment]]:
    ordered = sorted(captions, key=lambda c: c.start)
    before = [c for c in ordered if c.end <= start][-LINES_BEFORE:]
    inside = [c for c in ordered if c.end > start and c.start < end]
    return before, inside


def build_prompt(before: Sequence[CaptionSegment], inside: Sequence[CaptionSegment]) -> str:
    numbered = [f"[{i}] (before) {c.text}" for i, c in enumerate(before)]
    numbered += [f"[{i}] {c.text}" for i, c in enumerate(inside, start=len(before))]
    return "Write the hook for this clip.\n\nTranscript:\n" + "\n".join(numbered)


def check_grounded(draft: HookDraft, line_texts: Sequence[str]) -> str:
    """Return the cleaned hook, or raise when it breaks a rule the code can check."""
    text = " ".join(draft.text.split()).strip(" \"'“”")
    if not text:
        raise ProviderCallError("The AI returned an empty hook.")
    if len(text.split()) > MAX_WORDS:
        raise ProviderCallError(f"The AI hook is longer than {MAX_WORDS} words, so it was not added.")
    if not draft.source_lines or any(not 0 <= n < len(line_texts) for n in draft.source_lines):
        raise ProviderCallError("The AI hook cites transcript lines that do not exist, so it was not added.")
    source = " ".join(line_texts).lower()
    unsupported = [
        w for w in _FACT.findall(text)
        if w.lower() not in _STARTERS and w.lower().rstrip(".,:%") not in source
    ]
    if unsupported:
        raise ProviderCallError(
            f"The AI hook used words that are not in the transcript ({', '.join(unsupported)}), so it was not added."
        )
    return text


def write_hook(
    captions: Sequence[CaptionSegment],
    start: float,
    end: float,
    provider: AIProvider,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    saved: SavedSettings = NO_SAVED,
) -> tuple[AIProvider, str]:
    before, inside = hook_window(captions, start, end)
    if not inside:
        raise ProviderConfigError("This range has no subtitles, so a context hook cannot be written for it.")
    resolved, call = resolve_call(provider, api_key=api_key, model=model, base_url=base_url, saved=saved)
    draft = call(LLMRequest(
        system=SYSTEM_PROMPT,
        prompt=build_prompt(before, inside),
        output=HookDraft,
        gemini_schema=GEMINI_SCHEMA,
    ))
    return resolved, check_grounded(draft, [c.text for c in [*before, *inside]])

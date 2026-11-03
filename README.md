# Clip Studio

Cut short clips from YouTube, Twitch and Kick videos. Pick the range by hand, or let an AI read the subtitles and suggest moments. A clip can open with a one-line context hook written from its own subtitles.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- ffmpeg with `drawtext`

## Run

```bash
./start.sh
```

This creates `.venv`, installs the dependencies from `pyproject.toml`, and serves the app at http://localhost:8000.

## AI providers

Choose Claude, Gemini, any OpenAI-compatible server, or the local keyword method in the Highlights panel. Type a key in the page with "Remember on this computer" ticked, and it is saved to `storage/clip_studio.db` for next time. You can instead set `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `OPENAI_API_KEY` with `OPENAI_MODEL` before you run `./start.sh`.

Twitch and Kick do not publish subtitles, so AI features work only on videos that have them.

## Test

```bash
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest -q
```

## Layout

- `clipstudio/` is the app: routes in `app.py`, the page in `static/index.html`.
- `tests/` holds the pytest suite.
- `storage/` holds downloaded videos, exported clips and saved AI settings. Git ignores its contents.

## Working with an AI agent

Read `AGENTS.md` first. It requires the pstack plugin.

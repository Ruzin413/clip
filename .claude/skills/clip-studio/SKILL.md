---
name: clip-studio
description: Run, test and change Clip Studio, the FastAPI clipper for YouTube, Twitch and Kick links with AI-picked highlights. Use for any code change, bug fix or verification in this repo.
---

# Clip Studio

## Map

- `sources.py` turns a link into a `SourceRef`. Its pattern table is the only place that knows URL shapes. The media key `<platform>_<id>` names the cached file and is the `video_id` the page sees.
- `clip_engine.py` handles media only: video info, download, subtitles and the ffmpeg cut.
- `highlights.py` picks moments. The `PROVIDERS` table drives listing, Automatic and every provider call. Models return transcript line numbers, and `plan_to_clips` maps them to real caption times.
- `hooks.py` writes the one-line context hook from the clip's subtitles plus the 3 lines before it. `check_grounded` rejects any name or number missing from those lines, and `cut_clip` draws the line over the first 2 seconds.
- `settings_store.py` keeps remembered keys, models and base URLs in `storage/clip_studio.db`.
- `app.py` holds the HTTP routes. `models.py` holds the shared shapes. `static/index.html` is the whole frontend.

## Run and test

- Run: `./start.sh`, which serves http://localhost:8000.
- Test: `.venv/bin/python -m pytest -q`. `conftest.py` points `CLIP_STUDIO_DB` at a temp file, so tests never touch saved keys.
- A change to the page or a route is not done until you drive the real page in a browser against the real server: load a link, trim, export, and probe the clip with `ffprobe`.

## Rules

- Add a platform or an AI provider as one row in its table. Do not add if/else branches elsewhere.
- Never let a model write seconds. Timestamps come only from caption boundaries.
- A new AI task is an `LLMRequest` passed to `resolve_call`. Do not add per-provider code for it.
- Never send a saved API key back to the browser. Report only `saved_key_hint`.
- Validate a link or media key once, in `app.py`, and trust it everywhere else.

## Gotchas

- Twitch and Kick expose no subtitles today. The empty list is expected, not an error.
- Twitch clips arrive as HEVC. `ensure_browser_playable` re-encodes them to H.264.
- ffmpeg builds without libx264 (Fedora's ffmpeg-free) fall back to OpenH264. Test fixtures encode with `mpeg4` so they run anywhere.
- A whole VOD downloads before preview: about 1.5 GB per hour for Twitch at 720p60.

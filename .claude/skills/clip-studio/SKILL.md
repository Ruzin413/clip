---
name: clip-studio
description: Run, test and change Clip Studio, the FastAPI clipper for YouTube, Twitch and Kick links with AI-picked highlights. Use for any code change, bug fix or verification in this repo.
---

# Clip Studio

## Map

App code lives in the `clipstudio/` package, tests in `tests/`, and runtime data in `storage/` at the repo root. Dependencies and pytest settings live in `pyproject.toml`.

- `sources.py` turns a link into a `SourceRef`. Its pattern table is the only place that knows URL shapes. The media key `<platform>_<id>` names the cached file and is the `video_id` the page sees.
- `clip_engine.py` handles media only: video info, download, subtitles, the ffmpeg cut, and `burn_hook`, which draws a hook line on a finished clip.
- `highlights.py` picks moments. The `PROVIDERS` table drives listing, Automatic and every provider call. Models return transcript line numbers, and `plan_to_clips` maps them to real caption times.
- `hooks.py` writes the one-line context hook from the clip's subtitles plus the 3 lines before it. `check_grounded` rejects any name or number missing from those lines.
- `pipelines.py` holds `PIPELINES`, the only place that knows which post-processing pipelines exist, and `execute`, which runs a run's steps in order. A step takes a `StepContext` and returns its detail line or raises. The output is written to a temp path in `storage/clips` and renamed into place, then added to the library as a new clip.
- `library.py` keeps exported clips and pipeline runs in the same SQLite file as the settings. A clip keeps its source-video times and only the subtitle lines `hooks.hook_window` needs, so any pipeline can run on any clip, including one a run made. Opening the library fails any run a previous process left queued or running.
- `settings_store.py` keeps remembered keys, models and base URLs in `storage/clip_studio.db`.
- `app.py` holds the HTTP routes and the single pipeline worker, so one ffmpeg runs at a time. `models.py` holds the shared shapes. A run's `status` is derived from its steps, never stored. The frontend in `clipstudio/static/` is static files with no build step. Two pages: the editor (`index.html`, entry `js/editor.js`) and the Pipeline page (`pipeline.html`, entry `js/pipeline.js`), which tracks runs and lists the clip library.
  - Each page's HTML is markup only: no `<style>`, no `style=`, no inline script, no `on*=` handlers. `tests/test_static.py` enforces this.
  - `css/app.css` holds the design tokens and shared components. `css/<page>.css` holds rules only one page uses.
  - `js/api.js` holds one function per endpoint. `js/ui.js` holds `PAGES`, which renders the nav into each page's `<nav data-nav>`, plus the formatting helpers. `js/ai-prefs.js` reads the editor's remembered AI choice. `js/widgets.js` renders the clip card and the "Send to" control, once for every page.

## Run and test

- Run: `./start.sh`. It installs `pyproject.toml` into `.venv` and serves `clipstudio.app:app` on http://localhost:8000.
- Test: `uv pip install --python .venv/bin/python pytest`, then `.venv/bin/python -m pytest -q`. `tests/conftest.py` points `CLIP_STUDIO_DB` at a temp file, so tests never touch saved keys.
- A change to a page or a route is not done until you drive the real page in a browser against the real server: load a link, trim, export, and probe the clip with `ffprobe`. For pipelines, export with "Then send to" and watch the run finish on the Pipeline page.

## Rules

- Add a platform, an AI provider or a pipeline as one row in its table. Do not add if/else branches elsewhere.
- Never let a model write seconds. Timestamps come only from caption boundaries.
- A new AI task is an `LLMRequest` passed to `resolve_call`. Do not add per-provider code for it.
- Never send a saved API key back to the browser. Report only `saved_key_hint`.
- Never write an API key into the library. A run's `AIChoice` lives only in the worker's memory.
- Only the worker writes a run after `POST /api/runs` creates it.
- Validate a link or media key once, in `app.py`, and trust it everywhere else.
- Add a dependency to `pyproject.toml`, never only to `start.sh`.
- Inside `clipstudio/`, import siblings relatively (`from .models import ...`). Tests import `clipstudio.<module>`.
- A new page is a `PAGES` row plus `<name>.html` and `js/<name>.js`, and pages call the API only through `js/api.js`. A widget two pages show is one render function in `js/widgets.js`.

## Gotchas

- Twitch and Kick expose no subtitles today. The empty list is expected, not an error.
- Twitch clips arrive as HEVC. `ensure_browser_playable` re-encodes them to H.264.
- ffmpeg builds without libx264 (Fedora's ffmpeg-free) fall back to OpenH264. Test fixtures encode with `mpeg4` so they run anywhere.
- A whole VOD downloads before preview: about 1.5 GB per hour for Twitch at 720p60.
- A run that was queued or running when the server stopped shows as failed after the next start. Send the clip again.

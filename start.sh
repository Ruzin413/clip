#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    uv venv
fi
uv pip install -q fastapi uvicorn yt-dlp youtube-transcript-api pydantic python-multipart httpx anthropic

echo "Starting Clip Studio on http://localhost:8000 ..."
.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000

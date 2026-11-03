import os
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import clip_engine
import highlights
from models import AIProvider, CaptionSegment, ClipRequest, ClipResponse, ProviderSettings, VideoInfo
from settings_store import ProviderSettingsStore
from sources import SourceRef, UnsupportedSourceError, parse_source

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STORAGE_DIR = BASE_DIR / "storage"
MEDIA_DIR = STORAGE_DIR / "media"
CLIPS_DIR = STORAGE_DIR / "clips"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
CLIPS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

provider_settings = ProviderSettingsStore(Path(os.environ.get("CLIP_STUDIO_DB", STORAGE_DIR / "clip_studio.db")))

app = FastAPI(title="Video Clip Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UrlRequest(BaseModel):
    url: str


class HighlightsRequest(BaseModel):
    captions: list[CaptionSegment]
    provider: AIProvider = AIProvider.AUTO
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    query: str | None = None


def _source(url: str) -> SourceRef:
    try:
        return parse_source(url)
    except UnsupportedSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/info", response_model=VideoInfo)
def api_get_info(payload: UrlRequest):
    source = _source(payload.url)
    try:
        info = clip_engine.get_video_info(source)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read video info: {exc}")
    if (MEDIA_DIR / f"{source.media_key}.mp4").exists():
        info.local_path = f"/media/{source.media_key}.mp4"
    return info


@app.post("/api/download")
def api_download_video(payload: UrlRequest):
    source = _source(payload.url)
    if not (MEDIA_DIR / f"{source.media_key}.mp4").exists():
        try:
            clip_engine.download_video(source, MEDIA_DIR)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Download failed: {exc}")
    return {
        "video_id": source.media_key,
        "stream_url": f"/media/{source.media_key}.mp4",
        "status": "ready"
    }


@app.post("/api/captions")
def api_get_captions(payload: UrlRequest):
    source = _source(payload.url)
    try:
        segments = clip_engine.fetch_captions(source)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Captions extraction failed: {exc}")
    return {"video_id": source.media_key, "captions": segments}


@app.get("/api/ai/providers")
def api_list_providers():
    saved = provider_settings.all()
    return {
        "providers": highlights.list_providers(saved),
        "auto": highlights.resolve_provider(AIProvider.AUTO, saved),
    }


def _configurable(provider: AIProvider) -> AIProvider:
    if provider in (AIProvider.AUTO, AIProvider.LOCAL):
        raise HTTPException(status_code=400, detail=f"There is nothing to save for {provider.value}.")
    return provider


@app.put("/api/ai/providers/{provider}")
def api_save_provider_settings(provider: AIProvider, payload: ProviderSettings):
    cleaned = ProviderSettings(**{k: (v or "").strip() or None for k, v in payload.model_dump().items()})
    provider_settings.save(_configurable(provider), cleaned)
    return api_list_providers()


@app.delete("/api/ai/providers/{provider}")
def api_forget_provider_settings(provider: AIProvider):
    provider_settings.forget(_configurable(provider))
    return api_list_providers()


@app.post("/api/ai-highlights")
def api_get_highlights(payload: HighlightsRequest):
    try:
        provider, clips = highlights.find_highlights(
            payload.captions,
            payload.provider,
            api_key=payload.api_key or None,
            model=payload.model or None,
            base_url=payload.base_url or None,
            query=payload.query or None,
            saved=provider_settings.all(),
        )
    except highlights.ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except highlights.ProviderCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"provider": provider, "highlights": clips}


@app.post("/api/clip", response_model=ClipResponse)
def api_create_clip(payload: ClipRequest):
    source_path = MEDIA_DIR / f"{payload.video_id}.mp4"
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Load the video before exporting a clip.")

    clip_id = str(uuid.uuid4())[:8]
    safe_name = payload.filename or f"clip_{payload.video_id}_{int(payload.start_time)}_{int(payload.end_time)}.mp4"
    if not safe_name.endswith(".mp4"):
        safe_name += ".mp4"

    output_path = CLIPS_DIR / f"{clip_id}_{safe_name}"
    try:
        clip_engine.cut_clip(str(source_path), payload.start_time, payload.end_time, str(output_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Clip cutting failed: {exc}")

    return ClipResponse(
        clip_id=clip_id,
        file_path=str(output_path.resolve()),
        download_url=f"/downloads/{output_path.name}",
        duration=round(payload.end_time - payload.start_time, 2)
    )


@app.get("/media/{filename}")
def serve_media(filename: str):
    file_path = MEDIA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(file_path, media_type="video/mp4")


@app.get("/downloads/{filename}")
def serve_download(filename: str):
    file_path = CLIPS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")
    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

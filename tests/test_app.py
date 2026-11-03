import time
from fastapi.testclient import TestClient
from clipstudio.app import CLIPS_DIR, MEDIA_DIR, app, library
from clipstudio.models import Clip, Run, StepRecord, StepState
import subprocess
from pathlib import Path

client = TestClient(app)

def test_static_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "Clip Studio" in response.text

def test_unsupported_link_is_rejected_with_reason():
    for path in ("/api/info", "/api/download", "/api/captions"):
        response = client.post(path, json={"url": "https://vimeo.com/123456"})
        assert response.status_code == 400, path
        assert "Twitch" in response.json()["detail"]

def test_providers_endpoint_lists_every_provider():
    response = client.get("/api/ai/providers")
    assert response.status_code == 200
    data = response.json()
    ids = {p["id"] for p in data["providers"]}
    assert {"claude", "gemini", "openai", "local"} <= ids
    assert data["auto"] in ids

def test_ai_highlights_endpoint():
    payload = {
        "provider": "local",
        "captions": [
            {"start": 0.0, "end": 5.0, "text": "Welcome to the show."},
            {"start": 5.1, "end": 12.0, "text": "The key secret strategy to master coding is practice."},
            {"start": 12.1, "end": 20.0, "text": "An insane breakthrough tip is to test everything."},
            {"start": 20.1, "end": 30.0, "text": "Never make this mistake again in production."}
        ]
    }
    response = client.post("/api/ai-highlights", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "local"
    assert len(data["highlights"]) >= 1
    h = data["highlights"][0]
    assert h["end"] > h["start"]
    assert h["title"] and h["reason"]

def test_ai_highlights_without_key_is_a_client_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    payload = {
        "provider": "claude",
        "captions": [{"start": 0.0, "end": 5.0, "text": "Hello."}],
    }
    response = client.post("/api/ai-highlights", json=payload)
    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]

def test_clip_rejects_unknown_and_malformed_media_keys():
    missing = client.post("/api/clip", json={"video_id": "twitch_999999999", "start_time": 0, "end_time": 1})
    assert missing.status_code == 404
    traversal = client.post("/api/clip", json={"video_id": "../../app", "start_time": 0, "end_time": 1})
    assert traversal.status_code == 422

def test_clip_endpoint_with_synthetic_media():
    synth_vid_id = "youtube_testsynth"
    src = MEDIA_DIR / f"{synth_vid_id}.mp4"
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
        "-c:v", "mpeg4", "-c:a", "aac",
        str(src)
    ]
    subprocess.run(gen_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        clip_req = {
            "video_id": synth_vid_id,
            "start_time": 0.5,
            "end_time": 2.5
        }
        res = client.post("/api/clip", json=clip_req)
        assert res.status_code == 200
        data = res.json()
        assert data["duration"] == 2.0

        dl_res = client.get(data["download_url"])
        assert dl_res.status_code == 200
        assert len(dl_res.content) > 0
        assert dl_res.headers["content-disposition"].startswith("attachment")

        preview = client.get(data["preview_url"])
        assert preview.status_code == 200
        assert "attachment" not in preview.headers.get("content-disposition", "")

        media_res = client.get(f"/media/{synth_vid_id}.mp4")
        assert media_res.status_code == 200
        (CLIPS_DIR / data["file_name"]).unlink()
    finally:
        src.unlink(missing_ok=True)


def test_delete_clip_removes_file_and_library_entry():
    clip_id = "test_del_clip"
    clip_file = CLIPS_DIR / f"{clip_id}_test.mp4"
    clip_file.write_text("fake video data")
    clip = Clip(
        clip_id=clip_id,
        video_id="youtube_test",
        start_time=0.0,
        end_time=5.0,
        file_name=clip_file.name,
        captions=[],
        created_at=time.time(),
    )
    library.add_clip(clip)
    assert library.get_clip(clip_id) is not None
    assert clip_file.exists()

    res = client.delete(f"/api/clips/{clip_id}")
    assert res.status_code == 200
    assert res.json() == {"status": "deleted", "clip_id": clip_id}
    assert library.get_clip(clip_id) is None
    assert not clip_file.exists()

    # Deleting missing clip returns 404
    missing_res = client.delete(f"/api/clips/{clip_id}")
    assert missing_res.status_code == 404


def test_delete_run_and_clear_runs():
    done_run = Run(
        run_id="run_done_1",
        pipeline="test-pipeline",
        input_clip_id="clip_1",
        steps=[StepRecord(id="s1", label="Step 1", state=StepState.DONE)],
        created_at=time.time(),
    )
    active_run = Run(
        run_id="run_active_1",
        pipeline="test-pipeline",
        input_clip_id="clip_1",
        steps=[StepRecord(id="s1", label="Step 1", state=StepState.RUNNING)],
        created_at=time.time(),
    )
    library.add_run(done_run)
    library.add_run(active_run)

    # Cannot delete active run
    active_del = client.delete(f"/api/runs/{active_run.run_id}")
    assert active_del.status_code == 400

    # Delete single finished run
    del_res = client.delete(f"/api/runs/{done_run.run_id}")
    assert del_res.status_code == 200
    assert library.get_run(done_run.run_id) is None

    # Deleting missing run returns 404
    assert client.delete(f"/api/runs/{done_run.run_id}").status_code == 404

    # Add another finished run and test clear_runs
    failed_run = Run(
        run_id="run_failed_1",
        pipeline="test-pipeline",
        input_clip_id="clip_1",
        steps=[StepRecord(id="s1", label="Step 1", state=StepState.FAILED)],
        created_at=time.time(),
    )
    library.add_run(failed_run)

    clear_res = client.delete("/api/runs")
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "cleared"
    assert library.get_run(failed_run.run_id) is None
    assert library.get_run(active_run.run_id) is not None

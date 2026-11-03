from fastapi.testclient import TestClient
from app import app
import tempfile
from pathlib import Path
import subprocess

client = TestClient(app)

def test_static_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "Clip Studio" in response.text or "YouTube" in response.text

def test_ai_highlights_endpoint():
    payload = {
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
    assert "highlights" in data
    assert len(data["highlights"]) >= 1
    h = data["highlights"][0]
    assert "start" in h
    assert "end" in h
    assert "title" in h
    assert "reason" in h

def test_clip_endpoint_with_synthetic_media():
    from app import MEDIA_DIR
    synth_vid_id = "test_synth_vid"
    src = MEDIA_DIR / f"{synth_vid_id}.mp4"
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
        "-c:v", "libx264", "-c:a", "aac",
        str(src)
    ]
    subprocess.run(gen_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    clip_req = {
        "video_id": synth_vid_id,
        "start_time": 0.5,
        "end_time": 2.5
    }
    res = client.post("/api/clip", json=clip_req)
    assert res.status_code == 200
    data = res.json()
    assert "download_url" in data
    assert data["duration"] == 2.0

    dl_res = client.get(data["download_url"])
    assert dl_res.status_code == 200
    assert len(dl_res.content) > 0

    media_res = client.get(f"/media/{synth_vid_id}.mp4")
    assert media_res.status_code == 200

if __name__ == "__main__":
    test_static_index()
    test_ai_highlights_endpoint()
    test_clip_endpoint_with_synthetic_media()
    print("ALL API ENDPOINT TESTS PASSED SUCCESSFULLY!")

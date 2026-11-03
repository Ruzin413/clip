import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipstudio import hooks
from clipstudio.app import CLIPS_DIR, DB_PATH, MEDIA_DIR, app
from clipstudio.hooks import HookDraft
from clipstudio.library import INTERRUPTED, Library
from clipstudio.models import AIProvider, Clip, Run, RunStatus, StepRecord, StepState

client = TestClient(app)

VIDEO = "youtube_pipelinesynth"
CAPTIONS = [
    {"start": 0.0, "end": 1.0, "text": "welcome back everyone"},
    {"start": 1.0, "end": 2.0, "text": "we lost the last two games"},
    {"start": 2.0, "end": 3.0, "text": "so Marcus says we go all in"},
    {"start": 3.0, "end": 4.0, "text": "this is round 13 and we have 800 credits"},
    {"start": 4.0, "end": 6.0, "text": "okay everyone push B now"},
    {"start": 6.0, "end": 7.5, "text": "no way that worked"},
]
RANGE = {"start_time": 4.0, "end_time": 7.5}
GROUNDED = HookDraft(text="Round 13 and Marcus goes all in", source_lines=[1, 2])
SECRET = "sk-test-pipeline-4321"


@pytest.fixture(scope="module", autouse=True)
def source_video():
    path = MEDIA_DIR / f"{VIDEO}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=25",
         "-f", "lavfi", "-i", "sine=duration=8", "-c:v", "mpeg4", "-q:v", "3", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )
    yield
    path.unlink(missing_ok=True)


@pytest.fixture
def fake_ai(monkeypatch):
    """Stand in for every provider: the hook call returns `draft` and records what the step asked for."""
    state = {"draft": GROUNDED, "asked": []}

    def resolve_call(provider, *, api_key, model, base_url, saved):
        state["asked"].append({"provider": provider, "api_key": api_key, "model": model})
        return AIProvider.OPENAI, lambda request: state["draft"]

    monkeypatch.setattr(hooks, "resolve_call", resolve_call)
    return state


def _export(captions=CAPTIONS) -> dict:
    res = client.post("/api/clip", json={"video_id": VIDEO, **RANGE, "captions": captions})
    assert res.status_code == 200, res.text
    return res.json()


def _send(clip_id: str, ai: dict | None = None) -> dict:
    res = client.post("/api/runs", json={"clip_id": clip_id, "pipeline": "context-hook", "ai": ai or {"provider": "openai"}})
    assert res.status_code == 202, res.text
    return res.json()


def _finished(run_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("done", "failed"):
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} still {run['status']} after {timeout} s")


def _clip(clip_id: str) -> dict:
    return next(c for c in client.get("/api/clips").json() if c["clip_id"] == clip_id)


def _duration(path: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout)


def _hook_band(path: Path, at: float) -> bytes:
    """Grayscale pixels of the top quarter of the frame, where the hook is drawn."""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
         "-vf", "crop=iw:ih/4:0:0,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout


def _band_difference(a: Path, b: Path, at: float) -> float:
    x, y = _hook_band(a, at), _hook_band(b, at)
    return sum(abs(p - q) for p, q in zip(x, y)) / len(x)


def test_pipelines_endpoint_lists_the_context_hook():
    hook = next(p for p in client.get("/api/pipelines").json() if p["id"] == "context-hook")
    assert hook["needs_captions"] is True
    assert [s["id"] for s in hook["steps"]] == ["write-hook", "draw-hook"]


def test_export_keeps_the_clip_with_the_hook_window_of_subtitles():
    clip = _export()
    assert clip["hook"] is None and clip["made_by_run"] is None
    assert clip["duration"] == 3.5
    assert [c["text"] for c in clip["captions"]] == [c["text"] for c in CAPTIONS[1:]]
    assert _clip(clip["clip_id"]) == clip
    assert (CLIPS_DIR / clip["file_name"]).is_file()


def test_context_hook_run_draws_the_hook_and_saves_the_output(fake_ai):
    clip = _export()
    queued = _send(clip["clip_id"], {"provider": "openai", "api_key": SECRET, "model": "fake-model"})
    assert queued["status"] == "queued"
    assert [s["state"] for s in queued["steps"]] == ["queued", "queued"]

    run = _finished(queued["run_id"])
    assert run["status"] == "done", run
    assert [s["state"] for s in run["steps"]] == ["done", "done"]
    assert run["steps"][0]["detail"] == GROUNDED.text
    assert all(s["finished_at"] >= s["started_at"] for s in run["steps"])
    assert fake_ai["asked"] == [{"provider": AIProvider.OPENAI, "api_key": SECRET, "model": "fake-model"}]

    output = _clip(run["output_clip_id"])
    assert output["made_by_run"] == run["run_id"] and output["hook"] == GROUNDED.text
    assert {k: output[k] for k in ("video_id", "start_time", "end_time", "captions")} == \
        {k: clip[k] for k in ("video_id", "start_time", "end_time", "captions")}

    plain, hooked = CLIPS_DIR / clip["file_name"], CLIPS_DIR / output["file_name"]
    assert hooked.is_file()
    assert abs(_duration(hooked) - _duration(plain)) < 0.1
    shown, gone = _band_difference(plain, hooked, 0.5), _band_difference(plain, hooked, 3.0)
    assert shown > 10, f"hook visible at 0.5 s (mean difference {shown:.1f})"
    assert gone < 2, f"hook gone by 3 s (mean difference {gone:.1f})"
    assert SECRET.encode() not in DB_PATH.read_bytes(), "the API key never reaches the library"


def test_output_clip_can_be_sent_to_another_run(fake_ai):
    first = _finished(_send(_export()["clip_id"])["run_id"])
    second = _finished(_send(first["output_clip_id"])["run_id"])
    assert second["status"] == "done", second
    assert second["input_clip_id"] == first["output_clip_id"]
    assert _clip(second["output_clip_id"])["made_by_run"] == second["run_id"]
    assert [r["run_id"] for r in client.get("/api/runs").json()[:2]] == [second["run_id"], first["run_id"]]


def test_ungrounded_hook_fails_the_first_step_and_skips_drawing(fake_ai):
    fake_ai["draft"] = HookDraft(text="Shroud says welcome back", source_lines=[0])
    run = _finished(_send(_export()["clip_id"])["run_id"])
    assert run["status"] == "failed"
    assert [s["state"] for s in run["steps"]] == ["failed", "skipped"]
    assert "Shroud" in run["steps"][0]["detail"]
    assert run["output_clip_id"] is None
    assert not any(c["made_by_run"] == run["run_id"] for c in client.get("/api/clips").json())
    assert not (CLIPS_DIR / f".{run['run_id']}.part.mp4").exists()


def test_rerun_retries_existing_run_without_creating_new_run(fake_ai):
    fake_ai["draft"] = HookDraft(text="Shroud says welcome back", source_lines=[0])
    orig_runs_count = len(client.get("/api/runs").json())
    run = _finished(_send(_export()["clip_id"])["run_id"])
    assert run["status"] == "failed"
    assert len(client.get("/api/runs").json()) == orig_runs_count + 1

    fake_ai["draft"] = GROUNDED
    rerun_res = client.post(f"/api/runs/{run['run_id']}/rerun", json={"ai": {"provider": "openai"}})
    assert rerun_res.status_code == 200
    assert rerun_res.json()["run_id"] == run["run_id"]

    finished_run = _finished(run["run_id"])
    assert finished_run["status"] == "done"
    assert finished_run["run_id"] == run["run_id"]
    assert len(client.get("/api/runs").json()) == orig_runs_count + 1


def test_runs_are_rejected_before_queuing_when_they_cannot_start():
    bare = _export(captions=[])
    res = client.post("/api/runs", json={"clip_id": bare["clip_id"], "pipeline": "context-hook"})
    assert res.status_code == 400 and "subtitles" in res.json()["detail"]
    assert client.post("/api/runs", json={"clip_id": "nope", "pipeline": "context-hook"}).status_code == 404
    assert client.post("/api/runs", json={"clip_id": bare["clip_id"], "pipeline": "nope"}).status_code == 400
    assert not any(r["input_clip_id"] == bare["clip_id"] for r in client.get("/api/runs").json())


@pytest.mark.parametrize("states, status", [
    (["queued", "queued"], RunStatus.QUEUED),
    (["running", "queued"], RunStatus.RUNNING),
    (["done", "running"], RunStatus.RUNNING),
    (["done", "done"], RunStatus.DONE),
    (["failed", "skipped"], RunStatus.FAILED),
    (["done", "failed"], RunStatus.FAILED),
])
def test_run_status_follows_its_steps(states, status):
    run = Run(run_id="r", pipeline="p", input_clip_id="c", created_at=0,
              steps=[StepRecord(id=str(i), label=str(i), state=s) for i, s in enumerate(states)])
    assert run.status is status


def test_restart_fails_runs_the_old_process_left_open(tmp_path):
    path = tmp_path / "library.db"
    library = Library(path)
    clip = Clip(clip_id="c1", video_id=VIDEO, start_time=0, end_time=1, file_name="c1.mp4", captions=[], created_at=1)
    library.add_clip(clip)
    steps = [StepRecord(id="a", label="A"), StepRecord(id="b", label="B")]
    running = Run(run_id="r1", pipeline="context-hook", input_clip_id="c1", steps=steps, created_at=1)
    running.begin()
    done = Run(run_id="r2", pipeline="context-hook", input_clip_id="c1", created_at=2,
               steps=[StepRecord(id="a", label="A", state=StepState.DONE)])
    library.add_run(running)
    library.add_run(done)

    restarted = Library(path)
    interrupted = restarted.get_run("r1")
    assert interrupted.status is RunStatus.FAILED
    assert [s.state for s in interrupted.steps] == [StepState.FAILED, StepState.SKIPPED]
    assert interrupted.steps[0].detail == INTERRUPTED
    assert restarted.get_run("r2") == done
    assert restarted.get_clip("c1") == clip
    assert Library(path).list_runs() == restarted.list_runs()

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import Clip, Run, RunStatus

INTERRUPTED = "Stopped when the server restarted. Run it again."

_CLIP_COLUMNS = ("clip_id", "video_id", "start_time", "end_time", "file_name", "captions", "hook", "made_by_run", "created_at")
_RUN_COLUMNS = ("run_id", "pipeline", "input_clip_id", "steps", "output_clip_id", "created_at")


class Library:
    """Exported clips and pipeline runs, kept in SQLite. Nothing here ever holds an API key."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS clips ("
                "clip_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, start_time REAL NOT NULL, end_time REAL NOT NULL, "
                "file_name TEXT NOT NULL, captions TEXT NOT NULL, hook TEXT, made_by_run TEXT, created_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, input_clip_id TEXT NOT NULL, steps TEXT NOT NULL, "
                "output_clip_id TEXT, created_at REAL NOT NULL)"
            )
        self._fail_interrupted_runs()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _fail_interrupted_runs(self) -> None:
        # Only the worker of the process that queued a run can finish it, so a run still open now never will.
        for run in self.list_runs():
            if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                run.fail(INTERRUPTED)
                self.save_run(run)

    def add_clip(self, clip: Clip) -> None:
        row = clip.model_dump(mode="json", include=set(_CLIP_COLUMNS))
        row["captions"] = json.dumps(row["captions"])
        with closing(self._connect()) as db, db:
            db.execute(
                f"INSERT INTO clips ({', '.join(_CLIP_COLUMNS)}) VALUES ({', '.join(':' + c for c in _CLIP_COLUMNS)})",
                row,
            )

    def get_clip(self, clip_id: str) -> Clip | None:
        clips = self._clips("WHERE clip_id = ?", (clip_id,))
        return clips[0] if clips else None

    def list_clips(self) -> list[Clip]:
        return self._clips("ORDER BY created_at DESC, rowid DESC")

    def _clips(self, where: str, params: tuple = ()) -> list[Clip]:
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT {', '.join(_CLIP_COLUMNS)} FROM clips {where}", params).fetchall()
        return [Clip.model_validate({**row, "captions": json.loads(row["captions"])}) for row in map(dict, rows)]

    def add_run(self, run: Run) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                f"INSERT INTO runs ({', '.join(_RUN_COLUMNS)}) VALUES ({', '.join(':' + c for c in _RUN_COLUMNS)})",
                _run_row(run),
            )

    def save_run(self, run: Run) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE runs SET steps = :steps, output_clip_id = :output_clip_id WHERE run_id = :run_id",
                _run_row(run),
            )

    def get_run(self, run_id: str) -> Run | None:
        runs = self._runs("WHERE run_id = ?", (run_id,))
        return runs[0] if runs else None

    def list_runs(self) -> list[Run]:
        return self._runs("ORDER BY created_at DESC, rowid DESC")

    def _runs(self, where: str, params: tuple = ()) -> list[Run]:
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT {', '.join(_RUN_COLUMNS)} FROM runs {where}", params).fetchall()
        return [Run.model_validate({**row, "steps": json.loads(row["steps"])}) for row in map(dict, rows)]


def _run_row(run: Run) -> dict:
    row = run.model_dump(mode="json", include=set(_RUN_COLUMNS))
    row["steps"] = json.dumps(row["steps"])
    return row

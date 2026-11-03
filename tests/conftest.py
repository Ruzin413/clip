import os
import tempfile
from pathlib import Path

import pytest

# app.py opens the settings database at import time, so point it away from the real one first.
os.environ["CLIP_STUDIO_DB"] = str(Path(tempfile.mkdtemp()) / "test_clip_studio.db")


@pytest.fixture(scope="session", autouse=True)
def remove_test_clip_files():
    """Tests export into the real storage/clips. The test library lists exactly the files they made."""
    yield
    from clipstudio.app import CLIPS_DIR, library
    for clip in library.list_clips():
        (CLIPS_DIR / clip.file_name).unlink(missing_ok=True)

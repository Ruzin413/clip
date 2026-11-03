import os
import tempfile
from pathlib import Path

# app.py opens the settings database at import time, so point it away from the real one first.
os.environ["CLIP_STUDIO_DB"] = str(Path(tempfile.mkdtemp()) / "test_clip_studio.db")

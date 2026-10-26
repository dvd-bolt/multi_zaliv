import os
import tempfile
from pathlib import Path

import pytest

# Set isolation BEFORE importing the application; never touch the user's real data.
ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="survivalflow-tests-"))
os.environ["SURVIVALFLOW_DATA"] = str(TEST_DATA)
os.environ["SURVIVALFLOW_FFMPEG"] = os.environ.get(
    "SURVIVALFLOW_TEST_FFMPEG", str(ROOT / "data" / "tools" / "ffmpeg.exe")
)
os.environ["SURVIVALFLOW_FFPROBE"] = os.environ.get(
    "SURVIVALFLOW_TEST_FFPROBE", str(ROOT / "data" / "tools" / "ffprobe.exe")
)


@pytest.fixture(scope="session", autouse=True)
def legacy_non_campaign_mode():
    """Existing pipeline tests exercise the generic mode; campaign tests opt in explicitly."""
    from backend.db import Session, Setting, settings

    with Session.begin() as db:
        db.get(Setting, "application").value = {**settings(db), "buba_required": False}

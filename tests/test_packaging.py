import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("packaged", [False, True])
def test_data_root_and_runtime_are_isolated_and_keep_development_compatibility(tmp_path, packaged):
    root = tmp_path / "Application"
    (root / "backend").mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / "backend/config.py", root / "backend/config.py")
    if packaged:
        (root / "frameflow-build.json").write_text('{}')
    local = tmp_path / "User profile"
    env = {**os.environ, "LOCALAPPDATA": str(local), "PYTHONPATH": str(root)}
    env.pop("SURVIVALFLOW_DATA", None)
    script = "from backend.config import DATA,python_binary;import json;print(json.dumps({'data':str(DATA),'python':str(python_binary())}))"
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    expected = local / "FrameFlow/data" if packaged else root / "data"
    assert Path(value["data"]) == expected
    assert Path(value["python"]) == (root / "runtime/python.exe" if packaged else root / ".venv/Scripts/python.exe")
    assert (expected / "secrets").is_dir()


def test_explicit_data_directory_remains_compatible(tmp_path):
    from backend import config

    assert config.DATA != config.ROOT / 'data'  # conftest configured a disposable path.
    assert config.binary('ffmpeg').endswith('ffmpeg.exe')

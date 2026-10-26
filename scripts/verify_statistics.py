"""Copy a synthetic validation database, then test statistics without a worker or credentials."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]).resolve()
if not source.name.startswith("validation-") or source.parent != ROOT / "data":
    raise ValueError("Use an isolated validation-* directory, never the user's database")
target = Path(tempfile.mkdtemp(prefix="survivalflow-statistics-"))
shutil.copytree(source / "thumbnails", target / "thumbnails")
with sqlite3.connect(source / "survivalflow.sqlite3") as original:
    with sqlite3.connect(target / "survivalflow.sqlite3") as copied:
        original.backup(copied)
port = 8771
base = f"http://127.0.0.1:{port}"
env = {
    **os.environ,
    "SURVIVALFLOW_DATA": str(target),
    "SURVIVALFLOW_PORT": str(port),
    "SURVIVALFLOW_TEST_DATA": str(target),
    "SURVIVALFLOW_TEST_URL": base,
}
with (target / "server.log").open("wb") as log:
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
try:
    for _ in range(80):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as response:
                health = json.load(response)
                assert Path(health["data_directory"]) == target
                break
        except OSError:
            if server.poll() is not None:
                raise RuntimeError((target / "server.log").read_text()) from None
            time.sleep(0.1)
    else:
        raise RuntimeError("Statistics validation API did not start")
    subprocess.run(["node", "scripts/verify_statistics.cjs"], cwd=ROOT, env=env, check=True)
finally:
    if server.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(server.pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        server.wait(timeout=10)

"""Rapid start/stop/restart regression, in a fresh local data directory."""

import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
directory = Path(tempfile.mkdtemp(prefix="survivalflow-launch-"))
os.environ["SURVIVALFLOW_DATA"] = str(directory)
os.environ["SURVIVALFLOW_PORT"] = "8769"
from scripts import launch, stop  # noqa: E402


def wait_worker():
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8769/api/state", timeout=2) as response:
                value = json.load(response)
            if value["worker"]:
                return json.loads((directory / "worker.pid.json").read_text())["pid"]
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError("Launcher did not start worker")


try:
    launch.ensure_services()
    first = wait_worker()
    stop.main()
    assert not (directory / "worker.heartbeat").exists()
    launch.ensure_services()
    second = wait_worker()
    assert second != first
    launch.ensure_services()  # Duplicate launch must not replace the live worker.
    time.sleep(2)
    assert wait_worker() == second
    report = {"rapid_restart": "passed", "duplicate_launch": "passed", "isolated_data": str(directory)}
    (ROOT / "docs" / "validation" / "v0.1" / "launcher-results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
finally:
    stop.main()

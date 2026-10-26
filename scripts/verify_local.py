"""Exercise the UI and actual subprocess restart in isolated local data, without credentials."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
directory = ROOT / "data" / ("validation-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
directory.mkdir(parents=True)
port = 8767
base = f"http://127.0.0.1:{port}"
environment = {
    **os.environ,
    "SURVIVALFLOW_DATA": str(directory),
    "SURVIVALFLOW_PORT": str(port),
    "SURVIVALFLOW_TEST_URL": base,
    "SURVIVALFLOW_TEST_FIXTURES": sys.argv[1],
}
processes = []


def launch(module, *args):
    log = (directory / (module.replace(".", "-") + ".log")).open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", module, *args],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log.close()
    processes.append(process)
    return process


def stop(process):
    if process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process.wait(timeout=10)


def state():
    with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
        return json.load(response)


def wait_for(predicate, timeout=90):
    began = time.monotonic()
    while time.monotonic() - began < timeout:
        try:
            value = state()
            failures = [j.get("error") for j in value["jobs"] if j["status"] == "failed"]
            if failures:
                raise RuntimeError("Render failed during validation: " + str(failures))
            if predicate(value):
                return value
        except OSError:
            pass
        time.sleep(0.1)
    raise TimeoutError("Validation condition timed out; inspect " + str(directory))


try:
    server = launch(
        "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"
    )
    wait_for(lambda _: True)
    with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
        health = json.load(response)
    assert Path(health["data_directory"]).resolve() == directory.resolve(), "API belongs to another instance"
    subprocess.run(["node", "scripts/verify_ui.cjs"], env=environment, cwd=ROOT, check=True)
    if os.environ.get("SURVIVALFLOW_VERIFY_EDGES") == "1":
        subprocess.run(["node", "scripts/verify_edges.cjs"], env=environment, cwd=ROOT, check=True)
    assert len(state()["jobs"]) == 6
    worker = launch("backend.worker")
    wait_for(lambda s: any(j["status"] == "rendering" for j in s["jobs"]))
    stop(worker)  # Crash during a real render, with the browser already closed.
    assert any(j["status"] == "rendering" for j in state()["jobs"])
    worker = launch("backend.worker")
    ready = wait_for(lambda s: len(s["jobs"]) == 6 and all(j["status"] == "ready" for j in s["jobs"]))
    assert all(not j["approved"] and j["external_id"] is None for j in ready["jobs"])
    subprocess.run(["node", "scripts/verify_ready_ui.cjs"], env=environment, cwd=ROOT, check=True)
    duplicate = launch("backend.worker")
    assert duplicate.wait(timeout=10) != 0, "A second worker must not start"
    stop(server)
    server = launch(
        "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"
    )
    restored = wait_for(lambda s: len(s["jobs"]) == 6)
    assert all(j["status"] == "ready" and j["has_output"] for j in restored["jobs"])
    report = {
        "ui": "passed",
        "closed_browser_render": "passed",
        "crash_during_render": "passed",
        "restart_persistence": "passed",
        "single_worker": "passed",
        "no_publications": True,
        "data_directory": str(directory),
        "jobs": 6,
    }
    output = ROOT / "docs" / "validation" / "v0.1" / "process-results.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
finally:
    for process in reversed(processes):
        stop(process)

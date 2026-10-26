"""Stop only this application's live API and its recorded worker process tree."""

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import DATA, PORT  # noqa: E402


def terminate(pid):
    result = subprocess.run(
        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
        check=False,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def main():
    server_pid = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=3) as response:
            health = json.load(response)
        if health.get("application") == "SurvivalFlow" and Path(health["data_directory"]).resolve() == DATA:
            server_pid = health["pid"]
    except OSError:
        pass
    try:
        record = json.loads((DATA / "worker.pid.json").read_text())
        pid = int(record["pid"])
        # A PID can be reused. Check command line and birth time before stopping the recorded process.
        script = (
            f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
            "if ($p) { @{command=$p.CommandLine; created=$p.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process = json.loads(result.stdout.decode("utf-8-sig"))
        created = datetime.fromisoformat(process["created"])
        if "-m backend.worker" in process["command"] and created <= datetime.fromisoformat(
            record["recorded"]
        ):
            if terminate(pid):
                (DATA / "worker.heartbeat").unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if server_pid and server_pid != os.getpid():
        terminate(server_pid)


if __name__ == "__main__":
    main()

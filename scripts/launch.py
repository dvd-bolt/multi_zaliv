"""Launch the local API and independent worker, without a visible service terminal."""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import DATA, PORT, binary  # noqa: E402


def ensure_services():
    for name in ("ffmpeg", "ffprobe"):
        binary(name)
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise RuntimeError("Сначала выполните scripts/setup.ps1")
    log_dir = DATA / "logs"
    flags = subprocess.CREATE_NO_WINDOW
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    base = f"http://127.0.0.1:{PORT}"
    try:
        with urllib.request.urlopen(base + "/api/state", timeout=2) as response:
            state = json.load(response)
    except OSError:
        with (log_dir / "server.log").open("ab") as output:
            subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "backend.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORT),
                    "--no-access-log",
                ],
                cwd=ROOT,
                stdout=output,
                stderr=output,
                creationflags=flags,
            )
        state = None
        for _ in range(50):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(base + "/api/state", timeout=1) as response:
                    state = json.load(response)
                break
            except OSError:
                pass
        if state is None:
            raise RuntimeError("Сервер не запустился. Подробности: data/logs/server.log") from None
    with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
        health = json.load(response)
    if (
        health.get("application") != "SurvivalFlow"
        or Path(health.get("data_directory", "")).resolve() != DATA
    ):
        raise RuntimeError("Этот порт занят другой программой или другим экземпляром SurvivalFlow")
    # Do not trust a recent heartbeat after a rapid stop/restart. The worker's OS lock
    # safely rejects an extra launch when another worker is actually still running.
    with (log_dir / "worker.log").open("ab") as output:
        subprocess.Popen(
            [python, "-m", "backend.worker"], cwd=ROOT, stdout=output, stderr=output, creationflags=flags
        )
    return base


def main():
    from scripts.desktop import focus_existing, instance_lock, open_window

    with instance_lock() as primary:
        if not primary:
            focus_existing()
            return
        open_window(ensure_services())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, str(error), "SurvivalFlow — запуск", 16)

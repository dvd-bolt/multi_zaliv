"""Isolated statistics settings UI check; never uses the owner's database or connection."""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if "--serve" in sys.argv:
        directory = os.environ.get("SURVIVALFLOW_DATA")
        if not directory or Path(directory).resolve() == (ROOT / "data").resolve():
            raise RuntimeError("The UI fixture requires an isolated data directory")
        sys.path.insert(0, str(ROOT))
        import uvicorn

        from backend import notify

        notify.connection = lambda: {"url": "https://example.invalid", "secret": "synthetic-test-key"}
        uvicorn.run(
            "backend.app:app", host="127.0.0.1", port=int(os.environ["SURVIVALFLOW_PORT"]), log_level="error"
        )
        return
    with tempfile.TemporaryDirectory(prefix="frameflow-stats-ui-") as directory:
        base = "http://127.0.0.1:8772"
        env = {
            **os.environ,
            "SURVIVALFLOW_DATA": directory,
            "SURVIVALFLOW_PORT": "8772",
            "SURVIVALFLOW_TEST_URL": base,
        }
        process = subprocess.Popen(
            [sys.executable, __file__, "--serve"],
            cwd=ROOT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Isolated server did not start")
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=1) as response:
                        import json

                        assert (
                            Path(json.load(response)["data_directory"]).resolve() == Path(directory).resolve()
                        )
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Isolated server did not become ready")
            subprocess.run(["node", "scripts/verify_stats_push_ui.cjs"], cwd=ROOT, env=env, check=True)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

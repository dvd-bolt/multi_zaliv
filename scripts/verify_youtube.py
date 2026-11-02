"""Isolated UI fixtures for channel library, covers and explicit reupload. No remote writes."""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serve():
    if Path(os.environ["SURVIVALFLOW_DATA"]).resolve() == ROOT / "data":
        raise RuntimeError("Isolated data directory required")
    sys.path.insert(0, str(ROOT))
    import uvicorn
    from sqlalchemy import func, select

    from backend import youtube_library
    from backend.db import Account, Asset, ChannelVideo, Job, Session, Variant

    with Session.begin() as db:
        db.add(
            Account(
                id="yt-test",
                platform="youtube",
                name="Тестовый канал",
                connected=True,
                external_id="UC_TEST",
                method="oauth",
            )
        )
        db.add(
            Asset(
                id="source-test",
                kind="video",
                name="Тестовый ролик.mp4",
                path="synthetic",
                sha256="a" * 64,
                media={"duration": 10},
                edit={},
            )
        )
        db.flush()
        db.add(
            Variant(
                id="variant-test",
                asset_id="source-test",
                account_id="yt-test",
                fingerprint="test",
                config={"title": "Тестовый ролик", "visibility": "private"},
            )
        )
        db.flush()
        db.add(
            Job(
                id="job-test",
                variant_id="variant-test",
                status="published",
                external_id="old-video01",
                url="https://www.youtube.com/watch?v=old-video01",
                thumbnail={
                    "status": "error",
                    "error": "Тестовая ошибка обложки. Видео уже отправлено; повторяется только обложка.",
                },
            )
        )
        for i in range(57):
            db.add(
                ChannelVideo(
                    account_id="yt-test",
                    external_id=f"manual{i:05d}",
                    present=True,
                    details={
                        "title": f"Ручной ролик {i:02d} — " + "Длинное название " * 8,
                        "published_at": "2026-11-01T10:00:00Z",
                        "visibility": "private",
                        "values": {"views": i + 100, "likes": i, "comments": 0},
                    },
                    history=[
                        {
                            "captured": "2026-11-02T10:00:00Z",
                            "values": {"views": i + 100, "likes": i, "comments": 0},
                        }
                    ],
                )
            )
    youtube_library.sync = lambda db, account: db.scalar(
        select(func.count()).select_from(ChannelVideo).where(ChannelVideo.account_id == account.id)
    )
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8773, log_level="error")


def main():
    if "--serve" in sys.argv:
        serve()
        return
    with tempfile.TemporaryDirectory(prefix="frameflow-youtube-ui-") as directory:
        env = {
            **os.environ,
            "SURVIVALFLOW_DATA": directory,
            "SURVIVALFLOW_PORT": "8773",
            "SURVIVALFLOW_TEST_URL": "http://127.0.0.1:8773",
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
                    raise RuntimeError("Isolated server stopped")
                try:
                    with urllib.request.urlopen(env["SURVIVALFLOW_TEST_URL"] + "/api/health", timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Isolated server did not start")
            subprocess.run(["node", "scripts/verify_youtube_ui.cjs"], cwd=ROOT, env=env, check=True)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

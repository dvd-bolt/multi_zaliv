"""Regressions found during the overnight quality pass. No remote writes are made."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, event, select, update

from backend import platforms, worker
from backend.app import app
from backend.config import DATA, binary
from backend.db import Account, Asset, Job, Metric, Session, Variant, engine, uid
from backend.media import builtin_banner, command, probe, render
from backend.schemas import Edit

client = TestClient(app)


@pytest.fixture
def sample():
    path = DATA / (uid() + ".mp4")
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=8:duration=2",
            "-c:v",
            "libx264",
            str(path),
        ]
    )
    with path.open("rb") as file:
        asset = client.post("/api/assets?kind=video", files={"file": ("QA.mp4", file, "video/mp4")}).json()
    account = client.post("/api/accounts", json={"platform": "youtube", "name": "QA isolate"}).json()
    response = client.put(f"/api/assets/{asset['id']}/edit", json={**asset["edit"], "buba_mode": False})
    assert response.status_code == 200
    payload = {"asset_ids": [asset["id"]], "account_ids": [account["id"]]}
    yield asset, account, payload
    with Session.begin() as db:
        variants = db.scalars(select(Variant).where(Variant.asset_id == asset["id"])).all()
        for variant in variants:
            job = db.scalar(select(Job).where(Job.variant_id == variant.id))
            if job:
                db.execute(delete(Metric).where(Metric.job_id == job.id))
                db.delete(job)
                db.flush()
            db.delete(variant)
        db.flush()
        db.delete(db.get(Asset, asset["id"]))
        db.delete(db.get(Account, account["id"]))


def test_simultaneous_queue_clicks_create_one_job(sample):
    _, _, payload = sample

    def enqueue(_):
        with TestClient(app) as connection:
            result = connection.post("/api/queue", json=payload)
            assert result.status_code == 200, result.text
            return result.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(enqueue, range(8)))
    assert sum(len(r["created"]) for r in responses) == 1
    assert sum(len(r["existing"]) for r in responses) == 7


def test_cancel_wins_if_committed_before_upload_claim(sample, monkeypatch):
    _, account, payload = sample
    key = client.post("/api/queue", json=payload).json()["created"][0]
    with Session.begin() as db:
        job = db.get(Job, key)
        job.status, job.approved, job.created = "scheduled", True, "1900-01-01T00:00:00+00:00"
        db.get(Account, account["id"]).connected = True
        db.get(Variant, job.variant_id).output = db.get(Asset, sample[0]["id"]).path
    injected = False

    def cancel_before_claim(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if not injected and statement.startswith("UPDATE jobs SET") and "uploading" in parameters:
            injected = True
            with Session.begin() as second:
                second.execute(update(Job).where(Job.id == key).values(status="cancelled", approved=False))

    def remote_must_not_run(_):
        raise AssertionError("An acknowledged cancellation must prevent a new remote write")

    monkeypatch.setattr(platforms, "youtube_service", remote_must_not_run)
    event.listen(engine, "before_cursor_execute", cancel_before_claim)
    try:
        with Session() as db:
            platforms.dispatch_one(db)
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", cancel_before_claim)
    assert injected
    with Session() as db:
        assert db.get(Job, key).status == "cancelled"


def test_failed_builtin_banner_releases_job(sample, monkeypatch):
    # New imports reuse the last banner preset; this regression explicitly needs the built-in banner.
    with Session.begin() as db:
        source = db.get(Asset, sample[0]["id"])
        source.edit = {**source.edit, "banner_id": None}
    key = client.post("/api/queue", json=sample[2]).json()["created"][0]
    with Session.begin() as db:
        db.get(Job, key).created = "1900-01-01T00:00:00+00:00"
    client.patch("/api/settings", json={"paused": False})

    def fail(*_):
        raise OSError("Synthetic filesystem error")

    monkeypatch.setattr(worker, "builtin_banner", fail)
    worker.tick()
    with Session() as db:
        assert db.get(Job, key).status == "failed"
        assert "Synthetic filesystem error" in db.get(Job, key).error


def test_rejected_or_known_remote_id_cannot_be_retried(sample):
    key = client.post("/api/queue", json=sample[2]).json()["created"][0]
    with Session.begin() as db:
        job = db.get(Job, key)
        job.status, job.external_id = "rejected", "KNOWN_ID"
    assert client.post(f"/api/jobs/{key}/action", json={"action": "cancel"}).status_code == 400
    with Session.begin() as db:
        db.get(Job, key).status = "failed"
    assert client.post(f"/api/jobs/{key}/action", json={"action": "retry"}).status_code == 400


@pytest.mark.parametrize("outcome", ["ready", "failed"])
def test_cancel_at_render_completion_keeps_cancelled_and_removes_orphan(sample, monkeypatch, outcome):
    key = client.post("/api/queue", json=sample[2]).json()["created"][0]
    with Session.begin() as db:
        job = db.get(Job, key)
        job.created = "1900-01-01T00:00:00+00:00"
        variant_id = job.variant_id
    client.patch("/api/settings", json={"paused": False})
    output = DATA / "exports" / f"{variant_id}.mp4"

    def finish(*args):
        if outcome == "failed":
            raise ValueError("Synthetic render failure during cancellation")
        args[3].write_bytes(b"synthetic completed export")

    injected = False

    def cancel_before_finish(_conn, _cursor, statement, parameters, _context, _many):
        nonlocal injected
        if not injected and statement.startswith("UPDATE jobs") and outcome in parameters:
            injected = True
            response = client.post(f"/api/jobs/{key}/action", json={"action": "cancel"})
            assert response.status_code == 200

    monkeypatch.setattr(worker, "render", finish)
    event.listen(engine, "before_cursor_execute", cancel_before_finish)
    try:
        worker.tick()
    finally:
        event.remove(engine, "before_cursor_execute", cancel_before_finish)
    assert injected
    with Session() as db:
        assert db.get(Job, key).status == "cancelled"
        assert db.get(Variant, variant_id).output is None
    assert not output.exists()


def test_corrupt_banner_returns_actionable_error_and_no_orphan():
    before = set((DATA / "uploads").iterdir())
    response = client.post("/api/assets?kind=banner", files={"file": ("broken.png", b"broken", "image/png")})
    assert response.status_code == 400
    assert "миниатюру" in response.json()["detail"]
    assert set((DATA / "uploads").iterdir()) == before


def test_long_text_banner_has_padding_and_multiple_lines():
    output = DATA / "long-text.png"
    builtin_banner("Ш" * 120, output)
    with Image.open(output) as image:
        pixels = image.convert("RGB")
        ink = [
            (x, y)
            for y in range(180)
            for x in range(960)
            if all(v < 80 for v in pixels.getpixel((x, y))) and image.getpixel((x, y))[3] > 0
        ]
        assert ink
        assert min(x for x, y in ink) > 20 and max(x for x, y in ink) < 940
        assert min(y for x, y in ink) > 10 and max(y for x, y in ink) < 170


def test_nonzero_matroska_timestamps_do_not_extend_the_video():
    source = DATA / "offset.mkv"
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=12:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=2:frequency=880",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-output_ts_offset",
            "3",
            str(source),
        ]
    )
    info = probe(source)
    assert abs(info["duration"] - 2) < 0.1
    banner = DATA / "offset-banner.png"
    builtin_banner("Проверка", banner)
    output = DATA / "offset-result.mp4"
    config = Edit(format="720", duration=0.5).effective(info["duration"])
    render(source, banner, {"image": True}, output, config, info["duration"], lambda _: None, lambda: False)
    result = probe(output)
    assert abs(result["duration"] - 2) < 0.1 and result["has_audio"]


def test_archive_hides_input_but_preserves_source_and_jobs(sample):
    asset, _, payload = sample
    key = client.post("/api/queue", json=payload).json()["created"][0]
    result = client.delete(f"/api/assets/{asset['id']}")
    assert result.status_code == 200 and result.json()["original_preserved"]
    state = client.get("/api/state").json()
    assert all(a["id"] != asset["id"] for a in state["assets"])
    assert any(j["id"] == key for j in state["jobs"])
    assert client.get(f"/api/assets/{asset['id']}/file").status_code == 200
    assert client.post("/api/queue", json=payload).status_code == 400


def test_text_preview_is_the_same_png_as_the_renderer():
    import io

    response = client.get("/api/banner-preview", params={"text": "Длинная реклама — " + "Я" * 90})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    expected = io.BytesIO()
    builtin_banner("Длинная реклама — " + "Я" * 90, expected)
    assert response.content == expected.getvalue()
    assert client.get("/api/banner-preview", params={"text": "X" * 121}).status_code == 400


def test_migration_preserves_a_version_one_database(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys

    from sqlalchemy import create_engine

    from backend.db import Base

    path = tmp_path / "survivalflow.sqlite3"
    old = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(old)
    old.dispose()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE assets DROP COLUMN archived")
        db.execute("ALTER TABLE jobs DROP COLUMN upload_started_at")
        db.execute("ALTER TABLE jobs DROP COLUMN validation_upload")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO schema_migrations VALUES (1)")
        db.execute(
            "INSERT INTO assets(id,kind,name,path,sha256,media,edit,created) VALUES ('kept','video','Keep me','original.mp4','abc','{}','{}','2026-01-01')"
        )
    result = subprocess.run(
        [sys.executable, "-c", "from backend.db import migrate;migrate()"],
        env={**os.environ, "SURVIVALFLOW_DATA": str(tmp_path)},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name,archived FROM assets WHERE id='kept'").fetchone() == ("Keep me", 0)
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 6
        assert "upload_started_at" in [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
        assert "validation_upload" in [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
        assert "auto_publish" in [row[1] for row in db.execute("PRAGMA table_info(jobs)")]


def test_processing_poll_does_not_restart_upload_interval(sample, monkeypatch):
    from datetime import UTC, datetime, timedelta

    asset, account, payload = sample
    first = client.post("/api/queue", json=payload).json()["created"][0]
    with Session.begin() as db:
        previous = db.get(Job, first)
        previous.status, previous.external_id, previous.created = "platform_processing", "KNOWN", "1900-01-01"
        previous.updated = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
        previous.upload_started_at = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
        channel = db.get(Account, account["id"])
        channel.connected = True
        channel.external_id = "UC_QA"
        old = db.get(Variant, previous.variant_id)
        variant = Variant(
            asset_id=asset["id"],
            account_id=channel.id,
            fingerprint=uid(),
            config=old.config,
            output=db.get(Asset, asset["id"]).path,
        )
        db.add(variant)
        db.flush()
        next_job = Job(variant_id=variant.id, status="scheduled", approved=True, created="1901-01-01")
        db.add(next_job)
        db.flush()
        next_id = next_job.id
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def execute(self):
            return self.body

    class Service:
        def channels(self):
            return self

        def videos(self):
            return self

        def list(self, **kw):
            return Response(
                {"items": [{"id": "UC_QA"}] if kw.get("mine") else [{"status": {"uploadStatus": "uploaded"}}]}
            )

        def insert(self, **kw):
            calls.append("attempt")
            raise ValueError("Synthetic transport stop; no network called")

    monkeypatch.setattr(platforms, "youtube_service", lambda _: Service())
    with Session() as db:
        platforms.dispatch_one(db)
        db.commit()
        assert db.get(Job, next_id).status == "unknown"
        assert db.get(Job, first).status == "platform_processing"
    assert calls == ["attempt"]


@pytest.fixture
def fake_oauth(monkeypatch):
    import googleapiclient.discovery
    from google_auth_oauthlib.flow import InstalledAppFlow

    control = {"wait": None, "writes": []}

    class Credentials:
        def to_json(self):
            return '{"token":"synthetic-only"}'

    class Flow:
        def run_local_server(self, **kw):
            if control["wait"] is not None:
                assert control["wait"].wait(5)
            return Credentials()

    class Service:
        def channels(self):
            return self

        def list(self, **kw):
            return self

        def execute(self):
            return {"items": [{"id": "UC_NEW", "snippet": {"title": "Synthetic channel"}}]}

    original_get = platforms.vault.get
    monkeypatch.setattr(
        platforms.vault,
        "get",
        lambda key: {"installed": {"client_id": "fake"}} if key == "google_client" else original_get(key),
    )
    monkeypatch.setattr(platforms.vault, "put", lambda key, value: control["writes"].append(key))
    monkeypatch.setattr(InstalledAppFlow, "from_client_config", lambda *args, **kw: Flow())
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kw: Service())
    return control


def test_reconnect_cannot_redirect_existing_jobs_to_a_different_channel(sample, fake_oauth):
    client.post("/api/queue", json=sample[2])
    with Session.begin() as db:
        db.get(Account, sample[1]["id"]).external_id = "UC_OLD"
    thread = platforms.begin_youtube(sample[1]["id"])
    thread.join(5)
    assert not thread.is_alive() and not fake_oauth["writes"]
    with Session() as db:
        assert db.get(Account, sample[1]["id"]).external_id == "UC_OLD"
    assert "отдельной записью" in platforms.AUTH[sample[1]["id"]]


def test_disconnecting_invalidates_a_pending_oauth_callback(sample, fake_oauth):
    import threading

    fake_oauth["wait"] = threading.Event()
    thread = platforms.begin_youtube(sample[1]["id"])
    assert client.post(f"/api/accounts/{sample[1]['id']}/disconnect").status_code == 200
    fake_oauth["wait"].set()
    thread.join(5)
    assert not thread.is_alive() and not fake_oauth["writes"]
    with Session() as db:
        assert not db.get(Account, sample[1]["id"]).connected
    assert platforms.AUTH[sample[1]["id"]] == "disconnected"

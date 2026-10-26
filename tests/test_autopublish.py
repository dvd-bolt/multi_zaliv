import hashlib
import importlib
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import platforms, worker
from backend.app import app
from backend.config import DATA, binary
from backend.db import Account, Asset, Job, Session, Setting, Variant, settings, uid
from backend.media import command
from backend.queue import recover
from backend.schemas import Edit

client = TestClient(app)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.import_module("backend.app"), "ensure_queue_worker", lambda: None)
    worker.RECOVERY_NEEDED.clear()
    (DATA / "worker.heartbeat").touch()
    path = tmp_path / "auto.mp4"
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=90x160:rate=5:duration=1.5",
            "-c:v",
            "libx264",
            str(path),
        ]
    )
    source_id, account_id = uid(), uid()
    with Session.begin() as db:
        db.add(
            Asset(
                id=source_id,
                kind="video",
                name="Synthetic auto",
                path=str(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                media={"duration": 1.5, "width": 90, "height": 160, "has_audio": False},
                edit=Edit(format="720", duration=0.4, caption="Synthetic", visibility="public").model_dump(),
            )
        )
        db.add(
            Account(
                id=account_id,
                platform="youtube",
                name="Synthetic YouTube",
                connected=True,
                external_id="UC_SYNTHETIC",
                method="oauth",
            )
        )
        previous = dict(db.get(Setting, "application").value)
        db.get(Setting, "application").value = {
            **settings(db),
            "paused": True,
            "buba_required": False,
            "interval": 0,
        }
    writes = []

    class Response:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Upload:
        def next_chunk(self, **kwargs):
            return None, {"id": "TESTvideo01"}

    class Service:
        def channels(self):
            return self

        def videos(self):
            return self

        def list(self, **kwargs):
            return Response({"items": [{"id": "UC_SYNTHETIC"}]})

        def insert(self, **kwargs):
            writes.append(kwargs["body"])
            return Upload()

    monkeypatch.setattr(platforms, "youtube_service", lambda _: Service())
    body = {"asset_ids": [source_id], "account_ids": [account_id], "auto_publish": True}
    yield body, writes
    with Session.begin() as db:
        for variant in db.scalars(select(Variant).where(Variant.asset_id == source_id)).all():
            for job in db.scalars(select(Job).where(Job.variant_id == variant.id)).all():
                db.delete(job)
            db.flush()
            db.delete(variant)
        db.flush()
        for banner in db.scalars(select(Asset).where(Asset.kind == "banner", Asset.path == str(path))).all():
            db.delete(banner)
        db.delete(db.get(Asset, source_id))
        db.delete(db.get(Account, account_id))
        db.get(Setting, "application").value = previous


def enqueue(body):
    response = client.post("/api/queue", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_single_queue_click_renders_then_posts_at_time_without_approval(setup):
    body, writes = setup
    body["scheduled"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    jid = enqueue(body)["created"][0]
    with Session() as db:
        job = db.get(Job, jid)
        assert job.approved and job.auto_publish and job.status == "queued"
        assert not settings(db)["paused"]
    worker.tick()  # Real FFmpeg render. No upload before scheduled time.
    with Session() as db:
        job = db.get(Job, jid)
        assert job.status == "scheduled" and job.approved and job.progress == 100
        assert Path(db.get(Variant, job.variant_id).output).is_file()
    assert not writes
    with Session.begin() as db:
        db.get(Job, jid).scheduled = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    worker.tick()  # Controlled platform adapter; no extra user action.
    with Session() as db:
        assert db.get(Job, jid).status == "platform_processing"
    assert len(writes) == 1 and writes[0]["status"]["privacyStatus"] == "public"


def test_legacy_render_only_is_not_silently_approved(setup):
    body, writes = setup
    body["auto_publish"] = False
    jid = enqueue(body)["created"][0]
    with Session.begin() as db:
        assert not db.get(Job, jid).approved
        db.get(Setting, "application").value = {**settings(db), "paused": False}
    worker.tick()
    with Session() as db:
        assert db.get(Job, jid).status == "ready"
    assert not writes


def test_repeated_click_reuses_pending_and_never_revives_sent_or_unknown(setup):
    body, _ = setup
    manual = enqueue({**body, "auto_publish": False})
    jid = manual["created"][0]
    changed = enqueue(body)
    assert changed["activated"] == [jid] and not changed["created"]
    repeated = enqueue(body)
    assert repeated["existing"] == [jid] and not repeated["activated"]
    for status in ("unknown", "platform_processing", "published", "cancelled", "failed"):
        with Session.begin() as db:
            job = db.get(Job, jid)
            job.status, job.approved = status, False
            job.upload_started_at = datetime.now(UTC).isoformat() if status == "unknown" else None
        assert not enqueue(body)["activated"]
        with Session() as db:
            assert db.get(Job, jid).status == status and not db.get(Job, jid).approved


@pytest.mark.parametrize("failure", ["disconnected", "unverified_tiktok", "unlisted_tiktok"])
def test_account_preflight_rolls_back_whole_autopublish_request(setup, failure):
    body, _ = setup
    with Session.begin() as db:
        account = db.get(Account, body["account_ids"][0])
        if failure == "disconnected":
            account.connected = False
        else:
            account.platform = "tiktok"
            account.method = "browser_login" if failure == "unverified_tiktok" else "browser_verified"
        if failure == "unlisted_tiktok":
            asset = db.get(Asset, body["asset_ids"][0])
            asset.edit = {**asset.edit, "visibility": "unlisted"}
    result = client.post("/api/queue", json=body)
    assert result.status_code == 400
    with Session() as db:
        assert not db.scalars(select(Variant).where(Variant.asset_id == body["asset_ids"][0])).all()
        assert settings(db)["paused"]


def test_late_render_is_allowed_while_running_but_restart_and_sleep_stop_overdue_jobs(setup):
    body, writes = setup
    jid = enqueue(body)["created"][0]
    worker.tick()
    with Session.begin() as db:
        job = db.get(Job, jid)
        job.scheduled = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        recover(db)
        assert db.get(Job, jid).status == "missed"
        db.get(Job, jid).status = "scheduled"
    old = time.time() - 700
    os.utime(DATA / "worker.heartbeat", (old, old))
    worker.tick()
    with Session() as db:
        assert db.get(Job, jid).status == "missed"
    assert not writes
    with Session.begin() as db:
        db.get(Job, jid).status = "scheduled"
    worker.tick()  # No outage now: rendering/interval delay may pass the original target time.
    assert len(writes) == 1


def test_due_publication_precedes_next_render_and_disabled_account_waits(setup, monkeypatch):
    body, writes = setup
    jid = enqueue(body)["created"][0]
    worker.tick()
    with Session.begin() as db:
        db.get(Account, body["account_ids"][0]).enabled = False
    worker.tick()
    assert not writes
    with Session.begin() as db:
        db.get(Account, body["account_ids"][0]).enabled = True
    # A second queued item makes ordering observable without any remote upload.
    with Session.begin() as db:
        first = db.get(Variant, db.get(Job, jid).variant_id)
        second = Variant(
            asset_id=first.asset_id, account_id=first.account_id, fingerprint=uid(), config=first.config
        )
        db.add(second)
        db.flush()
        db.add(Job(variant_id=second.id))

    def render(*args):
        assert len(writes) == 1
        raise InterruptedError()

    monkeypatch.setattr(worker, "render", render)
    worker.tick()
    assert len(writes) == 1


def test_verified_tiktok_autopublishes_without_validation_button(setup, monkeypatch):
    from backend import tiktok_upload

    body, _ = setup
    sent = []
    with Session.begin() as db:
        account = db.get(Account, body["account_ids"][0])
        account.platform, account.method, account.external_id = "tiktok", "browser_verified", "synthetic.test"

    def upload(token, output, config, expected, *, before_post):
        assert expected == "synthetic.test" and Path(output).is_file()
        before_post()
        sent.append(True)
        return tiktok_upload.UploadResult(
            "7701010935195962999", "https://www.tiktok.com/@synthetic.test/video/7701010935195962999"
        )

    monkeypatch.setattr(tiktok_upload, "upload", upload)
    monkeypatch.setattr(platforms.vault, "get", lambda _: {"cookies": []})
    jid = enqueue(body)["created"][0]
    worker.tick()
    worker.tick()
    with Session() as db:
        job = db.get(Job, jid)
        assert job.status == "platform_processing" and not job.validation_upload and job.upload_started_at
    assert len(sent) == 1


def test_buba_conditions_require_explicit_aggregate_confirmation(setup):
    from backend.buba import REVIEW

    body, _ = setup
    with Session.begin() as db:
        source = db.get(Asset, body["asset_ids"][0])
        banner = Asset(
            id=uid(),
            kind="banner",
            name="Synthetic animated ad",
            path=source.path,
            sha256="f" * 64,
            media={"duration": 5, "width": 1374, "height": 552, "has_audio": False, "image": False},
            edit={},
        )
        db.add(banner)
        source.media = {**source.media, "duration": 20, "has_audio": True}
        source.edit = Edit(
            banner_id=banner.id,
            buba_mode=True,
            freeze_video=True,
            duration=5,
            width=100,
            banner_height=26,
            y=50,
            visibility="public",
        ).model_dump()
    assert client.post("/api/queue", json=body).status_code == 400
    with Session() as db:
        assert not any(db.get(Asset, body["asset_ids"][0]).edit["buba_review"].values())
    enqueue({**body, "buba_confirmed": True})
    with Session() as db:
        assert all(db.get(Asset, body["asset_ids"][0]).edit["buba_review"][key] for key in REVIEW)


def test_next_source_reuses_setup_but_never_previous_caption_or_declarations(setup):
    from backend.app import new_video_edit

    body, _ = setup
    with Session.begin() as db:
        source = db.get(Asset, body["asset_ids"][0])
        source.edit = {
            **source.edit,
            "targets": body["account_ids"],
            "title": "Previous title",
            "caption": "Previous story",
            "buba_mode": True,
            "auto_hashtags": True,
            "timing": "manual",
            "start": 1,
            "buba_review": {"rights": True},
        }
    with Session() as db:
        edit = new_video_edit(db, {"duration": 1})
        assert edit["targets"] == body["account_ids"] and edit["visibility"] == "public"
        assert edit["title"] == "" and "Previous story" not in edit["caption"]
        assert not any(edit["buba_review"].values())
        assert edit["timing"] == "middle" and edit["duration"] <= 0.5


def test_full_three_by_six_batch_renders_and_dispatches_once(setup, tmp_path, monkeypatch):
    from backend import tiktok_public, tiktok_upload
    from backend.media import probe

    body, _ = setup
    sources, accounts = list(body["asset_ids"]), list(body["account_ids"])
    sent = {"youtube": [], "tiktok": []}

    class Result:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

        def next_chunk(self, **kwargs):
            return None, self.value

    class Service:
        def __init__(self, channel):
            self.channel = channel

        def channels(self):
            return self

        def videos(self):
            return self

        def list(self, **kwargs):
            return Result({"items": [{"id": self.channel}]})

        def insert(self, **kwargs):
            sent["youtube"].append(kwargs["body"])
            return Result({"id": f"BULKx{len(sent['youtube']):06d}"})

    def upload(token, output, config, expected, *, before_post):
        assert expected.startswith("qa.bulk.") and Path(output).is_file()
        before_post()
        sent["tiktok"].append(config)
        vid = str(7701010935195962000 + len(sent["tiktok"]))
        return tiktok_upload.UploadResult(vid, f"https://www.tiktok.com/@{expected}/video/{vid}")

    monkeypatch.setattr(platforms, "youtube_service", lambda account: Service(account.external_id))
    monkeypatch.setattr(tiktok_upload, "upload", upload)
    monkeypatch.setattr(tiktok_public, "available", lambda *_: False)
    monkeypatch.setattr(platforms.vault, "get", lambda _: None)
    try:
        for color in ("green", "red"):
            source = tmp_path / f"{color}.mp4"
            command(
                [
                    binary("ffmpeg"),
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color={color}:size=90x160:rate=5:duration=1.5",
                    "-c:v",
                    "libx264",
                    str(source),
                ]
            )
            source_id = uid()
            sources.append(source_id)
            with Session.begin() as db:
                db.add(
                    Asset(
                        id=source_id,
                        kind="video",
                        name=source.name,
                        path=str(source),
                        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        media={"duration": 1.5, "width": 90, "height": 160, "has_audio": False},
                        edit=Edit(format="720", duration=0.4, visibility="public").model_dump(),
                    )
                )
        with Session.begin() as db:
            for i in range(1, 6):
                account_id = uid()
                accounts.append(account_id)
                db.add(
                    Account(
                        id=account_id,
                        platform="youtube" if i < 3 else "tiktok",
                        name=f"Synthetic bulk {i}",
                        connected=True,
                        external_id=f"UC_SYNTHETIC_{i}" if i < 3 else f"qa.bulk.{i}",
                        method="oauth" if i < 3 else "browser_verified",
                    )
                )
        request = {
            "asset_ids": sources,
            "account_ids": accounts,
            "auto_publish": True,
            "scheduled": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        result = enqueue(request)
        assert len(result["created"]) == 18
        for _ in range(18):
            worker.tick()
        assert not sent["youtube"] and not sent["tiktok"]
        with Session.begin() as db:
            jobs = db.scalars(select(Job).where(Job.id.in_(result["created"]))).all()
            assert len(jobs) == 18 and all(
                j.status == "scheduled" and j.approved and j.auto_publish for j in jobs
            )
            for job in jobs:
                output = Path(db.get(Variant, job.variant_id).output)
                info = probe(output)
                assert (info["width"], info["height"]) == (720, 1280)
                assert abs(info["duration"] - 1.5) < 0.2
                job.scheduled = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        for _ in range(18):
            worker.tick()
        assert len(sent["youtube"]) == 9 and len(sent["tiktok"]) == 9
        with Session() as db:
            assert all(db.get(Job, jid).status == "platform_processing" for jid in result["created"])
        repeated = enqueue({**request, "scheduled": None})
        assert not repeated["created"] and not repeated["activated"]
        worker.tick()
        assert len(sent["youtube"]) == 9 and len(sent["tiktok"]) == 9
    finally:
        with Session.begin() as db:
            for variant in db.scalars(select(Variant).where(Variant.asset_id.in_(sources))).all():
                for job in db.scalars(select(Job).where(Job.variant_id == variant.id)).all():
                    db.delete(job)
                db.flush()
                db.delete(variant)
            db.flush()
            for aid in sources[1:]:
                db.delete(db.get(Asset, aid))
            for aid in accounts[1:]:
                db.delete(db.get(Account, aid))

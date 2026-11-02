import base64
import io
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, select

from backend import platforms, youtube_covers, youtube_library
from backend.app import app
from backend.db import Account, Asset, ChannelVideo, Job, Metric, Session, Setting, Variant, settings, uid
from backend.queue import action, enqueue
from backend.schemas import Enqueue, JobAction


class Result:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class YouTube:
    def __init__(self):
        self.ids = ["old-video01", "manual00001"]
        self.fail_page = False
        self.channel = "UC_TEST"
        self.calls = []
        self.uploads = 0
        self.fail_cover = False
        self.cover_ids = []

    def channels(self):
        return self

    def videos(self):
        return self

    def playlistItems(self):
        return self

    def thumbnails(self):
        return self

    def list(self, **params):
        self.calls.append(params)
        if params.get("mine"):
            return Result(
                {
                    "items": [
                        {"id": self.channel, "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}}}
                    ]
                }
            )
        if "playlistId" in params:
            if self.fail_page and params.get("pageToken"):
                raise ConnectionError("quota failure")
            offset = int(params.get("pageToken", 0))
            page = {"items": [{"contentDetails": {"videoId": key}} for key in self.ids[offset : offset + 50]]}
            if len(self.ids) > offset + 50:
                page["nextPageToken"] = str(offset + 50)
            return Result(page)
        return Result(
            {
                "items": [
                    {
                        "id": key,
                        "snippet": {
                            "channelId": "UC_TEST",
                            "title": key,
                            "publishedAt": "2026-11-01T00:00:00Z",
                        },
                        "status": {"privacyStatus": "private", "uploadStatus": "processed"},
                        "statistics": {"viewCount": "10", "commentCount": "0"},
                    }
                    for key in params["id"].split(",")
                    if key in self.ids
                ]
            }
        )

    def insert(self, **_):
        return self

    def next_chunk(self, **_):
        self.uploads += 1
        return None, {"id": "new-video01"}

    def set(self, **params):
        self.cover_ids.append(params["videoId"])
        # The video outcome must already be persisted before the thumbnail request.
        with Session() as db:
            assert (
                db.scalar(select(Job).where(Job.external_id == params["videoId"])).status
                == "platform_processing"
            )
        if self.fail_cover:
            raise ConnectionError("thumbnail rejected")
        return Result({"items": [{}]})


@pytest.fixture
def channel(tmp_path, monkeypatch):
    service = YouTube()
    monkeypatch.setattr(platforms, "youtube_service", lambda _: service)
    account_id, asset_id, variant_id, job_id = [uid() for _ in range(4)]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic source")
    with Session.begin() as db:
        saved = settings(db)
        db.get(Setting, "application").value = {**saved, "interval": 0, "youtube_thumbnail": False}
        db.add(Account(id=account_id, platform="youtube", name="TEST", external_id="UC_TEST", connected=True))
        db.add(
            Asset(
                id=asset_id,
                kind="video",
                name="source.mp4",
                path=str(source),
                sha256=uid(),
                media={"duration": 20},
                edit={"title": "Test", "duration": 1},
            )
        )
        db.flush()
        db.add(
            Variant(
                id=variant_id,
                asset_id=asset_id,
                account_id=account_id,
                fingerprint=uid(),
                output=str(source),
                config={
                    "title": "Test",
                    "caption": "",
                    "visibility": "private",
                    "made_for_kids": False,
                    "synthetic": False,
                },
            )
        )
        db.flush()
        db.add(Job(id=job_id, variant_id=variant_id, status="published", external_id="old-video01"))
    yield service, account_id, asset_id, variant_id, job_id
    with Session.begin() as db:
        variants = select(Variant.id).where(Variant.account_id == account_id)
        jobs = select(Job.id).where(Job.variant_id.in_(variants))
        db.execute(delete(Metric).where(Metric.job_id.in_(jobs)))
        db.execute(delete(Job).where(Job.variant_id.in_(variants)))
        db.execute(delete(Variant).where(Variant.account_id == account_id))
        db.execute(delete(ChannelVideo).where(ChannelVideo.account_id == account_id))
        db.execute(delete(Setting).where(Setting.key == "youtube_sync_" + account_id))
        db.delete(db.get(Account, account_id))
        db.delete(db.get(Asset, asset_id))
        db.get(Setting, "application").value = saved


def test_all_pages_external_statistics_and_no_duplicate_jobs(channel):
    service, account_id, _, _, job_id = channel
    service.ids += [f"extra{i:06d}" for i in range(103)]
    with Session.begin() as db:
        assert youtube_library.sync(db, db.get(Account, account_id)) == 105
        db.flush()
        stats = youtube_library.external_statistics(db)
        external = [j for j in stats["jobs"] if j["account"]["id"] == account_id]
        assert len(external) == 104
        assert all(j["external_only"] and j["config"]["visibility"] == "private" for j in external)
        assert db.scalar(select(Job).where(Job.id == job_id)).external_id == "old-video01"
        assert len(db.scalars(select(Job).join(Variant).where(Variant.account_id == account_id)).all()) == 1
    assert len([c for c in service.calls if "playlistId" in c]) == 3
    assert len([c for c in service.calls if "id" in c]) == 3
    client = TestClient(app)
    assert len(client.get(f"/api/accounts/{account_id}/videos").json()["videos"]) == 105
    assert any(m["job_id"].startswith("channel:" + account_id) for m in client.get("/api/metrics").json())


def test_partial_fetch_does_not_mark_existing_video_missing(channel):
    service, account_id, *_ = channel
    with Session.begin() as db:
        youtube_library.sync(db, db.get(Account, account_id))
    service.ids = [f"page{i:07d}" for i in range(60)]
    service.fail_page = True
    with Session() as db:
        with pytest.raises(ConnectionError):
            youtube_library.sync(db, db.get(Account, account_id))
        assert db.get(ChannelVideo, (account_id, "old-video01")).present


def test_missing_video_excluded_from_statistics_without_authorizing_retry(channel):
    service, account_id, _, _, job_id = channel
    with Session.begin() as db:
        account = db.get(Account, account_id)
        youtube_library.sync(db, account)
        db.flush()
        service.ids.remove("old-video01")
        youtube_library.sync(db, account)
        db.flush()
        assert job_id in youtube_library.external_statistics(db)["missing"]
        job = db.get(Job, job_id)
        assert job.status == "published" and not job.approved


@pytest.mark.parametrize("mode", ["exists", "wrong_channel", "network"])
def test_repeat_requires_authenticated_absence(channel, monkeypatch, mode):
    service, _, _, _, job_id = channel
    if mode == "wrong_channel":
        service.channel = "OTHER"
    elif mode == "network":
        monkeypatch.setattr(service, "list", lambda **_: (_ for _ in ()).throw(ConnectionError()))
    with Session() as db:
        with pytest.raises(ValueError):
            action(db, db.get(Job, job_id), JobAction(action="reupload"))
        assert db.get(Job, job_id).status == "published"
    assert service.uploads == 0


def test_repeat_preserves_history_and_reuses_original_fingerprint(channel):
    service, account_id, asset_id, variant_id, job_id = channel
    service.ids.remove("old-video01")
    with Session.begin() as db:
        # Match the actual enqueue fingerprint, then exercise the explicit replacement path.
        request = Enqueue(asset_ids=[asset_id], account_ids=[account_id])
        result = enqueue(db, request)
        original = db.get(Job, result["created"][0])
        original.external_id, original.status = "old-video01", "published"
        variant = db.get(Variant, original.variant_id)
        variant.output = db.get(Variant, variant_id).output
        original_id = original.id
        action(db, original, JobAction(action="reupload"))
        db.flush()
        result = enqueue(db, request)
        assert not result["created"] and len(result["existing"]) == 1
        replacement = db.get(Job, result["existing"][0])
        assert replacement.id != original_id and not replacement.approved
        assert replacement.status == "ready" and replacement.external_id is None
        assert db.get(Job, original_id).external_id == "old-video01"
        assert db.get(Job, original_id).status == "deleted"
        with pytest.raises(ValueError):
            action(db, original, JobAction(action="reupload"))
    assert service.uploads == 0


def test_cover_failure_cannot_lose_video_id_or_repeat_video(channel, monkeypatch, tmp_path):
    service, _, _, _, job_id = channel
    service.fail_cover = True
    cover = tmp_path / "test.jpg"
    cover.write_bytes(b"synthetic cover")

    def prepare(*_):
        assert service.uploads == 1
        with Session() as check:
            job = check.get(Job, job_id)
            assert job.external_id == "new-video01" and job.status == "platform_processing"
            assert job.thumbnail["status"] == "generating"
        return cover, youtube_covers.MODELS[0]

    monkeypatch.setattr(youtube_covers, "prepare", prepare)
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.status, job.external_id, job.approved = "scheduled", None, True
        db.get(Setting, "application").value = {**settings(db), "youtube_thumbnail": True}
    with Session() as db:
        platforms.dispatch_one(db)
        job = db.get(Job, job_id)
        assert job.status == "platform_processing" and job.external_id == "new-video01"
        assert job.thumbnail["status"] == "error"
        platforms.dispatch_one(db)
    assert service.uploads == 1 and service.cover_ids == ["new-video01"]


@pytest.mark.parametrize(
    "error", [ValueError("Google HTTP 429"), ValueError("No Gemini key"), RuntimeError("private-payload")]
)
def test_cover_generation_failure_does_not_block_upload_or_retry_video(channel, monkeypatch, error):
    service, _, _, _, job_id = channel
    monkeypatch.setattr(youtube_covers, "prepare", lambda *_: (_ for _ in ()).throw(error))
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.status, job.external_id, job.approved = "scheduled", None, True
        db.get(Setting, "application").value = {**settings(db), "youtube_thumbnail": True}
    with Session() as db:
        platforms.dispatch_one(db)
        job = db.get(Job, job_id)
        assert job.status == "platform_processing" and job.external_id == "new-video01" and job.approved
        assert job.thumbnail["status"] == "error" and job.error is None
        assert "private-payload" not in job.thumbnail["error"]
        assert "Видео уже отправлено" in job.thumbnail["error"]
        platforms.dispatch_one(db)
    assert service.uploads == 1 and not service.cover_ids


def test_unknown_video_upload_never_starts_cover_generation(channel, monkeypatch):
    service, _, _, _, job_id = channel
    generations = []
    monkeypatch.setattr(youtube_covers, "prepare", lambda *_: generations.append(True))

    def lost(**_):
        service.uploads += 1
        raise ConnectionError("Lost video response")

    monkeypatch.setattr(service, "next_chunk", lost)
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.status, job.external_id, job.approved = "scheduled", None, True
        db.get(Setting, "application").value = {**settings(db), "youtube_thumbnail": True}
    with Session() as db:
        platforms.dispatch_one(db)
        assert db.get(Job, job_id).status == "unknown"
        platforms.dispatch_one(db)
    assert service.uploads == 1 and not generations


def test_google_cover_uses_frames_and_cache_and_normalizes_jpeg(channel, monkeypatch, tmp_path):
    _, _, _, variant_id, _ = channel
    monkeypatch.setattr(youtube_covers, "key", lambda: "synthetic-secret")
    monkeypatch.setattr(youtube_covers, "DATA", tmp_path)
    (tmp_path / "temporary").mkdir()
    (tmp_path / "thumbnails").mkdir()
    image = io.BytesIO()
    Image.new("RGB", (768, 768), "orange").save(image, "PNG")
    monkeypatch.setattr(youtube_covers, "command", lambda args: Path(args[-1]).write_bytes(image.getvalue()))
    calls = []

    def post(url, **kw):
        calls.append(kw)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(image.getvalue()).decode(),
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(youtube_covers.httpx, "post", post)
    with Session() as db:
        path, _ = youtube_covers.prepare(db, db.get(Variant, variant_id))
        assert Image.open(path).size == (1280, 720)
        assert path.stat().st_size < 2 * 1024 * 1024
        assert youtube_covers.prepare(db, db.get(Variant, variant_id))[0] == path
    assert len(calls) == 1
    assert len(calls[0]["json"]["contents"][0]["parts"]) == 4
    assert calls[0]["headers"] == {"x-goog-api-key": "synthetic-secret"}

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from backend import platforms
from backend.config import DATA
from backend.db import Account, Asset, ChannelVideo, Job, Metric, Session, Setting, Variant, uid


@pytest.fixture
def publication():
    keys = [uid() for _ in range(4)]
    path = DATA / f"{keys[0]}.mp4"
    path.write_bytes(b"synthetic-test-placeholder")
    with Session.begin() as db:
        db.add(
            Account(
                id=keys[0], platform="youtube", name="ISOLATED TEST", external_id="UC_TEST", connected=True
            )
        )
        db.add(
            Asset(id=keys[1], kind="video", name="test.mp4", path=str(path), sha256=uid(), media={}, edit={})
        )
        db.flush()
        db.add(
            Variant(
                id=keys[2],
                asset_id=keys[1],
                account_id=keys[0],
                fingerprint=uid(),
                output=str(path),
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
        db.add(Job(id=keys[3], variant_id=keys[2], status="scheduled", approved=True))
    yield keys
    with Session.begin() as db:
        db.execute(delete(ChannelVideo).where(ChannelVideo.account_id == keys[0]))
        db.execute(delete(Setting).where(Setting.key == "youtube_sync_" + keys[0]))
        db.execute(delete(Metric).where(Metric.job_id == keys[3]))
        db.execute(delete(Job).where(Job.id == keys[3]))
        db.execute(delete(Variant).where(Variant.id == keys[2]))
        db.execute(delete(Asset).where(Asset.id == keys[1]))
        db.execute(delete(Account).where(Account.id == keys[0]))
    path.unlink()


class Result:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeYouTube:
    def __init__(self, fail=False):
        self.fail, self.uploads, self.body = fail, 0, None

    def channels(self):
        return self

    def videos(self):
        return self

    def playlistItems(self):
        return self

    def list(self, **kw):
        if kw.get("mine"):
            return Result(
                {"items": [{"id": "UC_TEST", "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}}}]}
            )
        if "playlistId" in kw:
            return Result({"items": [{"contentDetails": {"videoId": "VIDEO_TEST"}}]})
        if "snippet,status,statistics,contentDetails" == kw["part"]:
            return Result(
                {
                    "items": [
                        {
                            "id": "VIDEO_TEST",
                            "snippet": {
                                "title": "Test",
                                "channelId": "UC_TEST",
                                "publishedAt": "2026-11-01T00:00:00Z",
                            },
                            "status": {"privacyStatus": "private"},
                            "statistics": {"viewCount": "0", "likeCount": "2"},
                        }
                    ]
                }
            )
        if kw["part"] == "statistics":
            return Result({"items": [{"statistics": {"viewCount": "0", "likeCount": "2"}}]})
        return Result({"items": [{"status": {"uploadStatus": "processed"}}]})

    def insert(self, **kw):
        self.body = kw["body"]
        return self

    def next_chunk(self, **kw):
        self.uploads += 1
        if self.fail:
            raise ConnectionError("simulated lost response")
        return None, {"id": "VIDEO_TEST"}


def test_lost_response_is_unknown_and_not_retried(publication, monkeypatch):
    service = FakeYouTube(fail=True)
    monkeypatch.setattr(platforms, "youtube_service", lambda _: service)
    with Session() as db:
        platforms.dispatch_one(db)
        db.commit()
        assert db.get(Job, publication[3]).status == "unknown"
        platforms.dispatch_one(db)
        db.commit()
    assert service.uploads == 1


@pytest.mark.parametrize("failure", ["expired", "missing", "network"])
def test_youtube_preflight_failure_is_not_an_unknown_upload(publication, monkeypatch, failure):
    from google.auth.exceptions import RefreshError

    calls = []

    def unavailable(_):
        calls.append(True)
        if failure == "expired":
            raise RefreshError("PRIVATE-OAUTH-PAYLOAD", {"error": "invalid_grant"})
        if failure == "missing":
            raise platforms.AccountIdentityError("Подключите YouTube через Google OAuth")
        raise ConnectionError("PRIVATE-NETWORK-PAYLOAD")

    monkeypatch.setattr(platforms, "youtube_service", unavailable)
    with Session() as db:
        platforms.dispatch_one(db)
        job = db.get(Job, publication[3])
        assert job.status == ("ready" if failure == "network" else "waiting_auth")
        assert not job.approved and job.external_id is None and job.upload_started_at is None
        assert "Отправка видео не начиналась" in job.error and "PRIVATE" not in job.error
        assert db.get(Account, publication[0]).connected == (failure == "network")
        platforms.dispatch_one(db)
    assert len(calls) == 1


def test_youtube_id_processing_visibility_and_snapshot_values(publication, monkeypatch):
    service = FakeYouTube()
    monkeypatch.setattr(platforms, "youtube_service", lambda _: service)
    with Session() as db:
        platforms.dispatch_one(db)
        db.commit()
        job = db.get(Job, publication[3])
        assert job.status == "platform_processing" and job.external_id == "VIDEO_TEST"
        assert service.body["status"]["privacyStatus"] == "private"
        job.updated = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        db.commit()
        platforms.dispatch_one(db)
        db.commit()
        assert job.status == "published"
        platforms.refresh_metrics(db)
        platforms.refresh_metrics(db)
        db.commit()
        snapshots = db.scalars(select(Metric).where(Metric.job_id == job.id)).all()
        assert len(snapshots) == 2
        assert all(m.values == {"views": 0, "likes": 2, "comments": None} for m in snapshots)
    assert service.uploads == 1


def test_missed_schedule_and_unapproved_job_are_not_sent(publication, monkeypatch):
    service = FakeYouTube()
    monkeypatch.setattr(platforms, "youtube_service", lambda _: service)
    with Session() as db:
        job = db.get(Job, publication[3])
        job.scheduled = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        db.commit()
        platforms.dispatch_one(db)
        db.commit()
        assert job.status == "missed"
        job.status, job.scheduled, job.approved = "scheduled", None, False
        db.commit()
        platforms.dispatch_one(db)
        assert service.uploads == 0


def test_cleanup_never_deletes_original_or_unknown_export(publication):
    from backend.maintenance import cleanup_exports

    with Session.begin() as db:
        job = db.get(Job, publication[3])
        job.status, job.updated = "unknown", "2020-01-01T00:00:00+00:00"
    assert cleanup_exports(1) == {"deleted": 0}
    with Session.begin() as db:
        db.get(Job, publication[3]).status = "cancelled"
    # The fake variant points outside exports. Even an eligible status must not remove it.
    assert cleanup_exports(1) == {"deleted": 0}
    with Session() as db:
        from pathlib import Path

        assert Path(db.get(Asset, publication[1]).path).exists()


def test_youtube_metadata_category_tags_language_and_title(publication, monkeypatch):
    service = FakeYouTube()
    monkeypatch.setattr(platforms, "youtube_service", lambda _: service)
    with Session.begin() as db:
        variant = db.get(Variant, publication[2])
        variant.config = {
            **variant.config,
            "title": "",
            "caption": "Шахта №4: люди в лифте. Не свети вверх. #бубавпн",
            "genre": "auto",
        }
    with Session() as db:
        platforms.dispatch_one(db)
        db.commit()
    snippet = service.body["snippet"]
    assert snippet["title"] == "Шахта №4: люди в лифте."
    assert snippet["categoryId"] == "1"
    assert snippet["defaultLanguage"] == snippet["defaultAudioLanguage"] == "ru"
    assert snippet["tags"][:2] == ["бубавпн", "хоррор"]

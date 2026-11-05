import pytest
from sqlalchemy import delete

from backend import author_comment, platforms, vault
from backend.db import Account, Asset, Job, Session, Variant, uid


@pytest.fixture
def published():
    keys = [uid() for _ in range(4)]
    with Session.begin() as db:
        db.add(Account(id=keys[0], platform="youtube", name="Y", external_id="UC_Y", connected=True))
        db.add(Asset(id=keys[1], kind="video", name="v", path="x", sha256=uid(), media={}, edit={}))
        db.flush()
        db.add(
            Variant(
                id=keys[2],
                asset_id=keys[1],
                account_id=keys[0],
                fingerprint=uid(),
                config={"title": "Пельмень", "visibility": "public", "made_for_kids": False},
            )
        )
        db.flush()
        db.add(
            Job(
                id=keys[3],
                variant_id=keys[2],
                status="published",
                external_id="VIDEO123456",
                url="https://www.youtube.com/watch?v=VIDEO123456",
                extras={"comment": {"text": "Кто съел {название}?", "pin": True, "status": "pending"}},
            )
        )
    yield keys
    with Session.begin() as db:
        db.execute(delete(Job).where(Job.id == keys[3]))
        db.execute(delete(Variant).where(Variant.id == keys[2]))
        db.execute(delete(Asset).where(Asset.id == keys[1]))
        db.execute(delete(Account).where(Account.id == keys[0]))


def comment(key):
    with Session() as db:
        return db.get(Job, key).extras["comment"]


def test_posts_once_with_title_and_manual_pin_link(published):
    calls = []

    def fake(account, video_id, text):
        calls.append(text)
        return {"status": "pin_manual", "id": "C1", "pin_url": "u", "detail": "d"}

    author_comment.process({"youtube": fake})
    author_comment.process({"youtube": fake})
    assert calls == ["Кто съел Пельмень?"]
    result = comment(published[3])
    assert result["status"] == "pin_manual" and result["text_sent"] == "Кто съел Пельмень?"


def test_failure_is_final_and_waiting_jobs_are_untouched(published):
    with Session.begin() as db:
        db.get(Job, published[3]).status = "platform_processing"
    author_comment.process({"youtube": lambda *a: pytest.fail("not yet published")})
    assert comment(published[3])["status"] == "pending"
    with Session.begin() as db:
        db.get(Job, published[3]).status = "published"

    def broken(*args):
        raise RuntimeError("network")

    author_comment.process({"youtube": broken})
    author_comment.process({"youtube": lambda *a: pytest.fail("no automatic retry")})
    assert comment(published[3])["status"] == "failed"


def test_private_and_kids_videos_are_skipped(published):
    with Session.begin() as db:
        variant = db.get(Variant, published[2])
        variant.config = {**variant.config, "made_for_kids": True}
    author_comment.process({"youtube": lambda *a: pytest.fail("comments are off")})
    assert comment(published[3])["status"] == "skipped"


def test_interrupted_attempt_becomes_failed_not_retried(published):
    with Session.begin() as db:
        job = db.get(Job, published[3])
        job.extras = {
            "comment": {**job.extras["comment"], "status": "posting", "started": "2026-01-01T00:00:00+00:00"}
        }
    author_comment.process({"youtube": lambda *a: pytest.fail("no retry")})
    assert comment(published[3])["status"] == "failed"


class Call:
    def __init__(self, value, log, name):
        self.value, self.log, self.name = value, log, name

    def execute(self):
        self.log.append(self.name)
        return self.value


class Threads:
    def __init__(self, existing, log):
        self.existing, self.log = existing, log

    def list(self, **kwargs):
        return Call({"items": self.existing}, self.log, "list")

    def insert(self, **kwargs):
        return Call({"snippet": {"topLevelComment": {"id": "NEW"}}}, self.log, "insert")


class Service:
    def __init__(self, existing, log):
        self.threads = Threads(existing, log)

    def commentThreads(self):  # noqa: N802 - Google client naming
        return self.threads


def test_youtube_reuses_own_existing_comment_and_requires_permission(monkeypatch):
    account = Account(id="a", platform="youtube", name="Y", external_id="UC_Y")
    monkeypatch.setattr(vault, "get", lambda key: {"scopes": platforms.SCOPES})
    assert author_comment.youtube_post(account, "VID", "Привет")["status"] == "skipped"
    monkeypatch.setattr(vault, "get", lambda key: {"scopes": platforms.LOGIN_SCOPES})
    own = {
        "snippet": {
            "topLevelComment": {
                "id": "OLD",
                "snippet": {"authorChannelId": {"value": "UC_Y"}, "textOriginal": "привет "},
            }
        }
    }
    log = []
    monkeypatch.setattr(platforms, "youtube_service", lambda a: Service([own], log))
    result = author_comment.youtube_post(account, "VID", "Привет")
    assert log == ["list"] and result["id"] == "OLD" and "lc=OLD" in result["pin_url"]
    log.clear()
    monkeypatch.setattr(platforms, "youtube_service", lambda a: Service([], log))
    assert author_comment.youtube_post(account, "VID", "Привет")["id"] == "NEW"
    assert log == ["list", "insert"]


def test_old_tokens_keep_their_own_scopes():
    assert platforms.granted_scopes({}) == platforms.SCOPES
    assert platforms.COMMENT_SCOPE not in platforms.granted_scopes({"scopes": platforms.SCOPES})

import pytest
from sqlalchemy import delete

from backend import notify
from backend.db import Account, Asset, Job, Session, Variant, uid


@pytest.fixture
def jobs(monkeypatch):
    keys = {"account": uid(), "asset": uid()}
    with Session.begin() as db:
        db.add(
            Account(
                id=keys["account"],
                platform="tiktok",
                name="@labsclode",
                external_id="labsclode",
                connected=True,
            )
        )
        db.add(
            Asset(
                id=keys["asset"],
                kind="video",
                name="episode.mp4",
                path="x.mp4",
                sha256=uid(),
                media={},
                edit={},
            )
        )
        db.flush()
        for n in range(3):
            variant = uid()
            db.add(
                Variant(
                    id=variant,
                    asset_id=keys["asset"],
                    account_id=keys["account"],
                    fingerprint=uid(),
                    output="x.mp4",
                    config={"title": f"Шахта {n}", "caption": ""},
                )
            )
            db.flush()
            db.add(Job(id=f"job{n}-" + keys["asset"][:6], variant_id=variant, status="rendering"))
    monkeypatch.setattr(notify, "_pause_until", 0.0)
    yield keys
    with Session.begin() as db:
        db.execute(delete(Job).where(Job.id.like("job%-" + keys["asset"][:6])))
        db.execute(delete(Variant).where(Variant.asset_id == keys["asset"]))
        db.execute(delete(Asset).where(Asset.id == keys["asset"]))
        db.execute(delete(Account).where(Account.id == keys["account"]))


def set_status(asset, values):
    with Session.begin() as db:
        for n, status in enumerate(values):
            job = db.get(Job, f"job{n}-" + asset[:6])
            job.status = status
            if status == "published":
                job.url = "https://www.tiktok.com/@labsclode/video/1"


def test_each_outcome_is_sent_once_and_auth_is_grouped(jobs, monkeypatch):
    monkeypatch.setattr(
        notify, "connection", lambda: {"url": "https://example/api/ext/frameflow", "secret": "x" * 30}
    )
    sent = []

    def send(conn, payload):
        sent.append(payload["event"])
        return True

    set_status(jobs["asset"], ["published", "waiting_auth", "waiting_auth"])
    with Session() as db:
        assert notify.flush(db, send) == 2
    kinds = sorted(e["type"] for e in sent)
    assert kinds == ["published", "waiting_auth"]
    published = next(e for e in sent if e["type"] == "published")
    assert published["url"].startswith("https://www.tiktok.com/") and published["title"] == "Шахта 0"
    assert next(e for e in sent if e["type"] == "waiting_auth")["count"] == 2
    with Session() as db:
        assert notify.flush(db, send) == 0  # nothing new
    # A job that leaves an outcome and comes back is reported again.
    set_status(jobs["asset"], ["published", "scheduled", "waiting_auth"])
    with Session() as db:
        notify.flush(db, send)
    set_status(jobs["asset"], ["published", "failed", "waiting_auth"])
    with Session() as db:
        assert notify.flush(db, send) == 1
    assert sent[-1]["type"] == "failed"


def test_network_error_keeps_events_and_pauses(jobs, monkeypatch):
    monkeypatch.setattr(notify, "connection", lambda: {"url": "https://example", "secret": "x" * 30})
    set_status(jobs["asset"], ["published", "rendering", "rendering"])
    clock = [1000.0]

    def broken(conn, payload):
        raise OSError("offline")

    with Session() as db:
        assert notify.flush(db, broken, lambda: clock[0]) == 0
    sent = []
    with Session() as db:
        assert (
            notify.flush(db, lambda c, p: sent.append(p) or True, lambda: clock[0] + 10) == 0
        )  # still paused
    with Session() as db:
        assert (
            notify.flush(db, lambda c, p: sent.append(p) or True, lambda: clock[0] + notify.RETRY_AFTER + 1)
            == 1
        )
    assert sent[0]["event"]["type"] == "published"


def test_without_connection_outcomes_are_marked_silently(jobs, monkeypatch):
    monkeypatch.setattr(notify, "connection", lambda: None)
    set_status(jobs["asset"], ["published", "failed", "rendering"])
    with Session() as db:
        assert notify.flush(db, lambda c, p: pytest.fail("must not send")) == 0
    monkeypatch.setattr(notify, "connection", lambda: {"url": "https://example", "secret": "x" * 30})
    with Session() as db:
        assert notify.flush(db, lambda c, p: pytest.fail("no backlog after connecting")) == 0


def test_code_parsing():
    conn = notify.parse(" https://bot.example.org/#" + "k" * 32 + " ")
    assert conn == {"url": "https://bot.example.org/api/ext/frameflow", "secret": "k" * 32}
    for bad in ("", "http://bot.example.org#" + "k" * 32, "https://bot.example.org", "https://x#short"):
        with pytest.raises(ValueError):
            notify.parse(bad)


def test_migration_marks_existing_outcomes_as_already_reported(jobs):
    from sqlalchemy import text

    from backend.db import engine, migrate

    set_status(jobs["asset"], ["published", "failed", "rendering"])
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET notified_status = NULL"))
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 7"))
    migrate()
    with Session() as db:
        statuses = {db.get(Job, f"job{n}-" + jobs["asset"][:6]).notified_status for n in range(3)}
    assert statuses == {"published", "failed", "rendering"}

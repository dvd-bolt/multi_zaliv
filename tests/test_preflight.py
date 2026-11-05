from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend import platforms, preflight
from backend.config import DATA
from backend.db import Account, Asset, Job, Session, Setting, Variant, uid


@pytest.fixture
def due():
    keys = [uid() for _ in range(4)]
    path = DATA / f"{keys[0]}.mp4"
    path.write_bytes(b"synthetic-test-placeholder")
    soon = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    with Session.begin() as db:
        db.add(Account(id=keys[0], platform="youtube", name="T", external_id="UC_T", connected=True))
        db.add(Asset(id=keys[1], kind="video", name="t.mp4", path=str(path), sha256=uid(), media={}, edit={}))
        db.flush()
        db.add(
            Variant(
                id=keys[2],
                asset_id=keys[1],
                account_id=keys[0],
                fingerprint=uid(),
                output=str(path),
                config={"title": "T", "caption": "", "visibility": "private"},
            )
        )
        db.flush()
        db.add(Job(id=keys[3], variant_id=keys[2], status="scheduled", approved=True, scheduled=soon))
    yield keys
    with Session.begin() as db:
        db.execute(delete(Setting).where(Setting.key == "preflight_" + keys[0]))
        db.execute(delete(Job).where(Job.id == keys[3]))
        db.execute(delete(Variant).where(Variant.id == keys[2]))
        db.execute(delete(Asset).where(Asset.id == keys[1]))
        db.execute(delete(Account).where(Account.id == keys[0]))
    path.unlink()


def checks(status):
    calls = []

    def run(account):
        calls.append(account.id)
        return status, "detail"

    return {"youtube": run, "tiktok": run}, calls


def test_failed_check_holds_jobs_without_disconnecting(due):
    fake, calls = checks("auth")
    preflight.scheduled_checks(fake)
    with Session() as db:
        job, account = db.get(Job, due[3]), db.get(Account, due[0])
        assert calls == [due[0]]
        assert job.status == "waiting_auth" and job.approved and job.error.startswith("Проверка перед")
        assert account.connected  # reconnecting TikTok would cost a new verification upload


def test_held_job_never_reaches_upload(due, monkeypatch):
    fake, _ = checks("auth")
    preflight.scheduled_checks(fake)

    def forbidden(_):
        raise AssertionError("upload must not start")

    monkeypatch.setattr(platforms, "youtube_service", forbidden)
    with Session.begin() as db:
        db.get(Job, due[3]).scheduled = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with Session() as db:
        platforms.dispatch_one(db)
        db.commit()
    with Session() as db:
        assert db.get(Job, due[3]).status == "waiting_auth"
        assert db.get(Job, due[3]).upload_started_at is None


def test_passing_check_releases_held_jobs(due):
    preflight.scheduled_checks(checks("auth")[0])
    with Session.begin() as db:
        value = preflight.stored(db, due[0])
        value["checked"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        preflight.remember(db, due[0], dict(value))
    fake, calls = checks("ok")
    preflight.scheduled_checks(fake)
    with Session() as db:
        assert calls == [due[0]]
        assert db.get(Job, due[3]).status == "scheduled"
        assert not preflight.blocked(db, db.get(Account, due[0]))


def test_network_error_changes_nothing(due):
    preflight.scheduled_checks(checks("error")[0])
    with Session() as db:
        assert db.get(Job, due[3]).status == "scheduled"
        assert not preflight.blocked(db, db.get(Account, due[0]))


def test_fresh_result_is_not_rechecked_and_far_jobs_are_ignored(due):
    preflight.scheduled_checks(checks("ok")[0])
    fake, calls = checks("ok")
    preflight.scheduled_checks(fake)
    assert calls == []
    with Session.begin() as db:
        db.delete(db.get(Setting, "preflight_" + due[0]))
        db.get(Job, due[3]).scheduled = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    preflight.scheduled_checks(fake)
    assert calls == []


def test_reconnect_forgets_failed_check(due):
    preflight.scheduled_checks(checks("auth")[0])
    with Session.begin() as db:
        preflight.forget(db, due[0])
    with Session() as db:
        assert not preflight.blocked(db, db.get(Account, due[0]))


def test_manual_check_reports_each_account(due):
    fake, _ = checks("ok")
    with Session() as db:
        result = preflight.check_accounts(db, [due[0], due[0], "missing"], fake)
    assert list(result) == [due[0]] and result[due[0]]["status"] == "ok"

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend import payouts
from backend.db import Account, Asset, BubaSubmission, Job, Metric, Session, Setting, Variant, uid
from backend.queue import enqueue
from backend.schemas import Enqueue


@pytest.fixture
def video():
    ids = {"asset": uid(), "tt": uid(), "yt": uid(), "jobs": []}
    started = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with Session.begin() as db:
        db.add(
            Account(
                id=ids["tt"], platform="tiktok", name="@labsclode", external_id="labsclode", connected=True
            )
        )
        db.add(
            Account(id=ids["yt"], platform="youtube", name="labs clode", external_id="UC1", connected=True)
        )
        db.add(
            Asset(id=ids["asset"], kind="video", name="shaft.mp4", path="x", sha256=uid(), media={}, edit={})
        )
        db.flush()
        for account, views in ((ids["tt"], 12_500), (ids["yt"], 900)):
            variant, job = uid(), uid()
            db.add(
                Variant(
                    id=variant,
                    asset_id=ids["asset"],
                    account_id=account,
                    fingerprint=uid(),
                    output="x",
                    config={"buba_mode": True, "visibility": "public", "title": "Шахта"},
                )
            )
            db.flush()
            db.add(
                Job(
                    id=job,
                    variant_id=variant,
                    status="published",
                    external_id="E" + job[:6],
                    url="https://example.org/" + job[:6],
                    upload_started_at=started,
                )
            )
            db.flush()
            db.add(Metric(job_id=job, values={"views": views}, source="test"))
            ids["jobs"].append(job)
    yield ids
    with Session.begin() as db:
        for job in ids["jobs"]:
            db.execute(delete(BubaSubmission).where(BubaSubmission.job_id == job))
            db.execute(delete(Metric).where(Metric.job_id == job))
            db.execute(delete(Job).where(Job.id == job))
        db.execute(delete(Variant).where(Variant.asset_id == ids["asset"]))
        db.execute(delete(Asset).where(Asset.id == ids["asset"]))
        db.execute(delete(Account).where(Account.id.in_([ids["tt"], ids["yt"]])))
        db.execute(delete(Setting).where(Setting.key == "payout_notified"))


def row(db, asset):
    return next(r for r in payouts.overview(db) if r["asset_id"] == asset)


def test_choose_publication_progress_and_claim(video):
    tiktok, youtube = video["jobs"]
    with Session() as db:
        first = row(db, video["asset"])
        assert first["recommended"] == tiktok and first["chosen"] is None and first["phase"] == "ready"
    with Session.begin() as db:
        payouts.choose(db, video["asset"], youtube)
    with Session() as db:
        chosen = row(db, video["asset"])
        assert chosen["chosen"] == youtube and chosen["phase"] == "collecting" and chosen["progress"] == 0.09
    with Session.begin() as db:
        payouts.choose(db, video["asset"], tiktok)  # switching keeps a single choice
    with Session() as db:
        assert row(db, video["asset"])["chosen"] == tiktok
        assert db.get(BubaSubmission, youtube) is None
    with Session.begin() as db:
        payouts.mark(db, tiktok, "submitted")
    with Session.begin() as db:
        with pytest.raises(ValueError):
            payouts.choose(db, video["asset"], youtube)  # already claimed from TikTok
    with Session.begin() as db:
        payouts.mark(db, tiktok, "paid")
    with Session() as db:
        assert row(db, video["asset"])["phase"] == "paid"


def test_reminders_once_and_expiry(video):
    tiktok, _ = video["jobs"]
    with Session.begin() as db:
        payouts.choose(db, video["asset"], tiktok)
    with Session.begin() as db:
        kinds = [k for _, k, _ in payouts.events(db)]
        assert kinds == ["ready"]
        payouts.remember(db, tiktok, "ready")
    with Session.begin() as db:
        assert payouts.events(db) == []
        last_day = datetime.fromisoformat(row(db, video["asset"])["deadline"]) - timedelta(hours=2)
        found = payouts.events(db, last_day)
        assert [k for _, k, e in found] == ["deadline"] and found[0][2]["type"] == "payout_deadline"
        late = datetime.fromisoformat(row(db, video["asset"])["deadline"]) + timedelta(minutes=1)
        assert (
            next(r for r in payouts.overview(db, late) if r["asset_id"] == video["asset"])["phase"]
            == "expired"
        )


def test_one_account_per_platform(video):
    with Session.begin() as db:
        second = uid()
        db.add(Account(id=second, platform="tiktok", name="@other", external_id="other", connected=True))
        db.get(Asset, video["asset"]).media = {"duration": 10}
        db.get(Setting, "application").value = {
            **db.get(Setting, "application").value,
            "one_per_platform": True,
        }
    try:
        with Session.begin() as db:
            with pytest.raises(ValueError, match="один TikTok"):
                enqueue(db, Enqueue(asset_ids=[video["asset"]], account_ids=[video["tt"], second]))
    finally:
        with Session.begin() as db:
            db.get(Setting, "application").value = {
                **db.get(Setting, "application").value,
                "one_per_platform": False,
            }
            db.execute(delete(Account).where(Account.id == second))

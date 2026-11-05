from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from backend.db import Account, Asset, Job, Session, Variant, uid
from backend.queue import enqueue
from backend.schemas import Enqueue


@pytest.fixture
def pair():
    ids = {"asset": uid(), "tt": uid(), "yt": uid()}
    with Session.begin() as db:
        db.add(Account(id=ids["tt"], platform="tiktok", name="@t", external_id="t", connected=True))
        db.add(Account(id=ids["yt"], platform="youtube", name="y", external_id="UC", connected=True))
        db.add(
            Asset(
                id=ids["asset"],
                kind="video",
                name="v.mp4",
                path="x",
                sha256=uid(),
                media={"duration": 30.0, "width": 1080, "height": 1920, "has_audio": False},
                edit={"title": "Пельмень"},
            )
        )
    yield ids
    with Session.begin() as db:
        variants = db.scalars(select(Variant.id).where(Variant.asset_id == ids["asset"])).all()
        db.execute(delete(Job).where(Job.variant_id.in_(variants)))
        db.execute(delete(Variant).where(Variant.asset_id == ids["asset"]))
        db.execute(delete(Asset).where(Asset.id == ids["asset"]))
        db.execute(delete(Account).where(Account.id.in_([ids["tt"], ids["yt"]])))


def jobs(db, ids):
    rows = db.scalars(select(Job).join(Variant).where(Variant.asset_id == ids["asset"])).all()
    return {db.get(Variant, j.variant_id).account_id: j for j in rows}


def test_per_channel_time_and_options_stay_outside_the_fingerprint(pair):
    soon = datetime.now(UTC) + timedelta(hours=2)
    later = soon + timedelta(hours=3)
    request = Enqueue(
        asset_ids=[pair["asset"]],
        account_ids=[pair["tt"], pair["yt"]],
        scheduled=soon.isoformat(),
        schedules={pair["yt"]: later.isoformat()},
        comment="Как думаете, кто съел {название}?",
        tiktok_sound="Ночной холодильник",
    )
    with Session.begin() as db:
        enqueue(db, request)
    with Session() as db:
        found = jobs(db, pair)
        fingerprints = {k: db.get(Variant, j.variant_id).fingerprint for k, j in found.items()}
        assert datetime.fromisoformat(found[pair["tt"]].scheduled) == soon
        assert datetime.fromisoformat(found[pair["yt"]].scheduled) == later
        assert found[pair["tt"]].extras["comment"] == {
            "text": "Как думаете, кто съел {название}?",
            "pin": True,
            "status": "pending",
        }
        assert found[pair["tt"]].extras["sound"] == {"name": "Ночной холодильник"}
        assert "sound" not in found[pair["yt"]].extras  # TikTok only
    with Session.begin() as db:
        result = enqueue(db, request.model_copy(update={"comment": "Другой текст", "tiktok_sound": None}))
    assert not result["created"]  # the same render is reused, not duplicated
    with Session() as db:
        found = jobs(db, pair)
        assert {k: db.get(Variant, j.variant_id).fingerprint for k, j in found.items()} == fingerprints


def test_past_channel_time_is_rejected(pair):
    with Session.begin() as db, pytest.raises(ValueError, match="будущее"):
        enqueue(
            db,
            Enqueue(
                asset_ids=[pair["asset"]],
                account_ids=[pair["yt"]],
                schedules={pair["yt"]: (datetime.now(UTC) - timedelta(minutes=5)).isoformat()},
            ),
        )

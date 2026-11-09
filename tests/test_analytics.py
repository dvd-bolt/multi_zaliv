from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import analytics
from backend.app import app
from backend.db import Account, ChannelVideo, PublicationStats, Session, uid


@pytest.fixture
def channel():
    key = uid()
    with Session.begin() as db:
        db.add(Account(id=key, platform="youtube", name="Analytics test"))
    yield key
    with Session.begin() as db:
        db.execute(delete(PublicationStats).where(PublicationStats.account_id == key))
        db.execute(delete(ChannelVideo).where(ChannelVideo.account_id == key))
        db.execute(delete(Account).where(Account.id == key))


def observation(at, views=100, likes=10, comments=2):
    return {"captured": at.isoformat(), "values": {"views": views, "likes": likes, "comments": comments},
            "source": "controlled observation"}


def test_nearby_only_zero_and_missing_are_distinct(channel):
    pub = datetime.now(UTC) - timedelta(days=3)
    with Session.begin() as db:
        p = analytics.record(db, channel, "video", pub.isoformat(), "platform", [
            observation(pub + timedelta(minutes=2), 500),  # Distant: not a first-hour measurement.
            observation(pub + timedelta(minutes=51), 0, 0, None),
            observation(pub + timedelta(hours=22), 2000),  # Distant: not a day measurement.
        ])
        assert p.milestones["hour"]["values"]["views"] == 0
        assert p.milestones["hour"]["values"]["comments"] is None
        assert p.milestones["hour"]["quality"] == "approximate"
        assert "day" not in p.milestones
        assert analytics.engagement(p.milestones["hour"]["values"]) is None


def test_closest_observation_survives_pruning_and_bad_dates(channel):
    pub = datetime.now(UTC) - timedelta(days=3)
    with Session.begin() as db:
        p = analytics.record(db, channel, "video", pub.isoformat(), "platform", [
            observation(pub + timedelta(minutes=65), 120),
            observation(pub + timedelta(minutes=61), 110),
        ])
        analytics.record(db, channel, "video", None, None, [observation(pub + timedelta(days=2), 9000)])
        assert p.milestones["hour"]["values"]["views"] == 110
        assert p.latest["values"]["views"] == 9000
        analytics.record(db, channel, "video", "2020-01-01", "invalid", [])
        assert p.published_at == pub.isoformat()
        assert "hour" in p.milestones


def test_unknown_timestamp_has_no_milestones(channel):
    with Session.begin() as db:
        p = analytics.record(db, channel, "video", None, None,
                             [observation(datetime.now(UTC) - timedelta(hours=1))])
        assert not p.milestones and p.published_at is None


def test_mean_er_is_not_ratio_of_sums_and_has_own_n(channel):
    pub = datetime.now(UTC) - timedelta(days=3)
    with Session.begin() as db:
        for i, values in enumerate([(100, 10, 0), (1000, 20, 0), (400, 10, None)]):
            db.add(ChannelVideo(account_id=channel, external_id=str(i), present=True, history=[],
                                details={"title": str(i), "published_at": pub.isoformat(),
                                         "visibility": "public"}))
            analytics.record(db, channel, str(i), pub.isoformat(), "YouTube snippet.publishedAt",
                             [observation(pub + timedelta(hours=24), *values)])
    with Session.begin() as db:
        report = analytics.report(db, "youtube", channel)
        assert report["cells"]["day"]["er"]["mean"] == 6  # mean(10%, 2%), not 30/1100.
        assert report["cells"]["day"]["er"]["n"] == 2
        assert report["cells"]["day"]["views"]["n"] == 3
        assert report["cells"]["day"]["views"]["mean"] == 500
        assert report["cells"]["day"]["views"]["median"] == 400


def test_young_video_does_not_qualify_before_target(channel):
    pub = datetime.now(UTC) - timedelta(minutes=55)
    with Session.begin() as db:
        db.add(ChannelVideo(account_id=channel, external_id="young", history=[], present=True,
                            details={"title": "young", "published_at": pub.isoformat(),
                                     "visibility": "public"}))
        analytics.record(db, channel, "young", pub.isoformat(), "platform",
                         [observation(pub + timedelta(minutes=54))])
        db.flush()
        row = next(r for r in analytics.video_rows(db) if r["account_id"] == channel)
        assert row["hour"] is None


def test_private_upload_is_not_confirmed_publication(channel):
    pub = datetime.now(UTC) - timedelta(days=2)
    with Session.begin() as db:
        db.add(ChannelVideo(account_id=channel, external_id="private", present=True,
                            details={"title": "private", "published_at": pub.isoformat(),
                                     "visibility": "private"},
                            history=[observation(pub + timedelta(hours=1))]))
    client = TestClient(app)
    result = client.get(f"/api/analytics/averages?platform=youtube&account_id={channel}").json()
    assert result["count"] == 0 and result["unknown_publication_time"] == 1
    result = client.get(f"/api/analytics/averages?platform=youtube&account_id={channel}&days=0").json()
    assert result["count"] == 1 and result["cells"]["current"]["views"]["n"] == 1
    assert result["cells"]["hour"]["views"]["n"] == 0
    with Session() as db:
        assert db.scalar(select(PublicationStats).where(PublicationStats.account_id == channel)).published_at is None


def test_invalid_queries_do_not_mix_platforms():
    client = TestClient(app)
    assert client.get('/api/analytics/averages?platform=all').status_code == 400
    assert client.get('/api/analytics/averages?days=-1').status_code == 400

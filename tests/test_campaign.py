"""Campaign policy and read-only monitoring checks. All remote data is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete

from backend.app import app
from backend.buba import REVIEW, report, with_hashtag
from backend.config import DATA, binary
from backend.db import (
    Account,
    Asset,
    BubaSubmission,
    Comment,
    CommentScan,
    Job,
    Metric,
    Session,
    Setting,
    Variant,
    settings,
    uid,
)
from backend.media import command, render
from backend.schemas import Edit

client = TestClient(app)
SOURCE = {"duration": 20, "has_audio": True}
BANNER = {"width": 1374, "height": 552, "duration": 5, "image": False, "has_audio": True}


def valid(**overrides):
    return Edit(
        buba_mode=True,
        banner_audio=True,
        banner_height=26,
        width=100,
        x=50,
        y=50,
        duration=5,
        visibility="public",
        buba_review={k: True for k in REVIEW},
        **overrides,
    ).effective(20)


def test_hashtag_is_automatic_idempotent_and_bounded():
    assert Edit(buba_mode=True, caption="Мультики").caption == "Мультики\n#бубавпн"
    assert with_hashtag(with_hashtag("hello")) == "hello\n#бубавпн"
    assert with_hashtag("#БУБАВПН") == "#БУБАВПН"
    assert with_hashtag("#бубавпн_чужой").endswith("\n#бубавпн")
    with pytest.raises(ValueError):
        Edit(buba_mode=True, caption="a" * 2200)
    assert Edit(buba_mode=False, caption="unchanged").caption == "unchanged"


def test_area_and_manual_checks_are_not_false_guarantees():
    config = valid()
    assert report(SOURCE, BANNER, config)["ready"]
    original = {**config, "banner_height": None}
    r = report(SOURCE, BANNER, original)
    assert 22 < r["area_percent"] < 23 and not r["ready"]
    assert any(i["code"] == "manual_limits" and i["level"] == "warning" for i in r["issues"])
    config["buba_review"] = {}
    assert len([i for i in report(SOURCE, BANNER, config)["issues"] if i["code"].startswith("review_")]) == 6


@pytest.mark.parametrize(
    "change,code",
    [
        ({"visibility": "private"}, "public"),
        ({"x": 30}, "center"),
        ({"timing": "manual", "start": 2}, "middle"),
        ({"duration": 2}, "banner_cut"),
        ({"banner_audio": False}, "banner_audio"),
    ],
)
def test_send_conditions(change, code):
    r = report(SOURCE, BANNER, {**valid(), **change})
    assert not r["ready"] and any(i["code"] == code for i in r["issues"])


def test_long_and_static_are_blocked():
    assert any(
        i["code"] == "long_timing" for i in report({**SOURCE, "duration": 130}, BANNER, valid())["issues"]
    )
    assert any(
        i["code"] == "animated"
        for i in report(SOURCE, {**BANNER, "image": True, "animated": False}, valid())["issues"]
    )


@pytest.fixture
def pair():
    aid, bid = uid(), uid()
    keys = []
    with Session.begin() as db:
        db.add(
            Asset(
                id=aid, kind="video", name="policy", path="unused", sha256=uid(), media=SOURCE, edit=valid()
            )
        )
        db.add(Asset(id=bid, kind="banner", name="ad", path="unused", sha256=uid(), media=BANNER, edit={}))
        db.flush()
        for platform in ["youtube", "tiktok"]:
            account = Account(
                platform=platform,
                name="local test",
                external_id="local.test",
                connected=True,
                method="oauth" if platform == "youtube" else "browser_login",
            )
            db.add(account)
            db.flush()
            cfg = {**valid(), "banner_id": bid}
            variant = Variant(
                asset_id=aid,
                banner_id=bid,
                account_id=account.id,
                fingerprint=uid(),
                config=cfg,
                output=str(DATA / "exports" / "synthetic.mp4"),
            )
            db.add(variant)
            db.flush()
            job = Job(variant_id=variant.id, status="ready", external_id="synthetic_video")
            db.add(job)
            db.flush()
            keys.append((job.id, variant.id, account.id))
    yield aid, bid, keys
    with Session.begin() as db:
        for jid, vid, key in keys:
            for model in (Comment, CommentScan, BubaSubmission, Metric):
                db.execute(delete(model).where(model.job_id == jid))
            db.delete(db.get(Job, jid))
            db.flush()
            db.delete(db.get(Variant, vid))
            db.flush()
            db.delete(db.get(Account, key))
        db.flush()
        db.delete(db.get(Asset, aid))
        db.delete(db.get(Asset, bid))


def test_approval_checks_buba_and_legacy_guard(pair):
    aid, bid, keys = pair
    jid, vid, _ = keys[0]
    with Session.begin() as db:
        v = db.get(Variant, vid)
        v.config = {**v.config, "y": 77}
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "approve"}).status_code == 400
    with Session.begin() as db:
        v = db.get(Variant, vid)
        v.config = {**v.config, "y": 50, "buba_mode": False}
        original = settings(db)
        db.get(Setting, "application").value = {**original, "buba_required": True}
    try:
        r = client.post(f"/api/jobs/{jid}/action", json={"action": "approve"})
        assert r.status_code == 400 and "старый вариант" in r.json()["detail"]
    finally:
        with Session.begin() as db:
            db.get(Setting, "application").value = original


def test_claim_is_unique_across_platforms_and_irreversible(pair):
    _, _, keys = pair
    date = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with Session.begin() as db:
        for jid, _, _ in keys:
            db.add(Metric(job_id=jid, values={"views": 10000}, source="synthetic"))
    payload = {"published_at": date, "public_confirmed": True, "state": "submitted"}
    first = client.post(f"/api/jobs/{keys[0][0]}/buba", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["eligible_by_local_data"]
    duplicate = client.post(f"/api/jobs/{keys[1][0]}/buba", json=payload)
    assert duplicate.status_code == 400 and "другого аккаунта" in duplicate.json()["detail"]
    assert (
        client.post(f"/api/jobs/{keys[0][0]}/buba", json={**payload, "state": "tracking"}).status_code == 400
    )
    assert client.post(f"/api/jobs/{keys[0][0]}/buba", json={**payload, "state": "paid"}).status_code == 200
    assert client.post(f"/api/jobs/{keys[0][0]}/buba", json=payload).status_code == 400


def test_claim_threshold_calendar_and_timezone(pair):
    jid = pair[2][0][0]
    with Session.begin() as db:
        db.add(Metric(job_id=jid, values={"views": 9999}, source="synthetic"))
    payload = {
        "published_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "public_confirmed": True,
        "state": "submitted",
    }
    assert client.post(f"/api/jobs/{jid}/buba", json=payload).status_code == 400
    with Session.begin() as db:
        db.add(Metric(job_id=jid, values={"views": 20000}, source="synthetic"))
    assert (
        client.post(
            f"/api/jobs/{jid}/buba",
            json={**payload, "published_at": (datetime.now(UTC) - timedelta(days=8)).isoformat()},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/jobs/{jid}/buba", json={**payload, "published_at": datetime.now().isoformat()}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/jobs/{jid}/buba",
            json={**payload, "published_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        ).status_code
        == 400
    )


def test_comments_are_idempotent_review_is_local_and_failure_is_not_clean(pair, monkeypatch):
    import backend.comments as monitor

    jid = pair[2][0][0]
    rows = [
        {"id": "a", "author": "a", "text": "Интересное у меня в профиле t.me/spam"},
        {"id": "b", "author": "b", "text": "Спасибо, смешно!"},
    ]
    monkeypatch.setattr(monitor, "youtube_comments", lambda *_: (rows, "ok", "synthetic"))
    a = client.post(f"/api/jobs/{jid}/comments/scan").json()
    assert a["flagged"] == 1 and len(a["comments"]) == 2 and not a["remote_deletion_supported"]
    key = next(c["id"] for c in a["comments"] if c["reason"])
    assert client.patch(f"/api/comments/{key}", json={"reviewed": True}).json()["remote_deleted"] is False
    b = client.post(f"/api/jobs/{jid}/comments/scan").json()
    assert b["flagged"] == 0 and len(b["comments"]) == 2
    rows[0]["text"] = "Заработал +200$ за сегодня"
    assert client.post(f"/api/jobs/{jid}/comments/scan").json()["flagged"] == 1

    def fail(*_):
        raise RuntimeError("SECRET should never appear")

    monkeypatch.setattr(monitor, "youtube_comments", fail)
    c = client.post(f"/api/jobs/{jid}/comments/scan")
    assert c.json()["status"] == "unavailable" and c.json()["flagged"] == 1 and "SECRET" not in c.text


def test_banner_export_really_occupies_26_percent(tmp_path):
    source = tmp_path / "source.mp4"
    banner = tmp_path / "banner.png"
    out = tmp_path / "stretched.mp4"
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=90x160:rate=10:duration=2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    Image.new("RGB", (1374, 552), "red").save(banner)
    cfg = Edit(format="720", duration=0.5, width=100, banner_height=26, x=50, y=50).effective(2)
    render(
        source,
        banner,
        {"image": True, "width": 1374, "height": 552},
        out,
        cfg,
        2,
        lambda _: None,
        lambda: False,
    )
    raw = command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-ss",
            "1.2",
            "-i",
            str(out),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1280",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    red = sum(raw[i] > 180 and raw[i + 2] < 70 for i in range(0, len(raw), 3))
    assert 25 < 100 * red / 1280 < 27


def test_youtube_comments_include_paginated_replies_without_remote_writes(monkeypatch):
    import backend.comments as monitor
    import backend.platforms as platforms

    calls = []

    class Reply:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Service:
        def commentThreads(self):
            self.kind = "threads"
            return self

        def comments(self):
            self.kind = "replies"
            return self

        def list(self, **kw):
            calls.append((self.kind, kw))
            if self.kind == "threads":
                if kw.get("pageToken") == "next":
                    return Reply({"items": []})
                return Reply(
                    {
                        "items": [
                            {
                                "snippet": {
                                    "topLevelComment": {
                                        "id": "top",
                                        "snippet": {"textOriginal": "Привет", "authorDisplayName": "A"},
                                    },
                                    "totalReplyCount": 2,
                                }
                            }
                        ],
                        "nextPageToken": "next",
                    }
                )
            if kw.get("pageToken") == "reply-next":
                return Reply({"items": [{"id": "r2", "snippet": {"textOriginal": "Спасибо"}}]})
            return Reply(
                {
                    "items": [{"id": "r1", "snippet": {"textOriginal": "у меня в профиле"}}],
                    "nextPageToken": "reply-next",
                }
            )

    monkeypatch.setattr(platforms, "youtube_service", lambda _: Service())
    rows, status, _ = monitor.youtube_comments(None, "synthetic")
    assert status == "ok" and {r["id"] for r in rows} == {"top", "r1", "r2"}
    assert len(calls) == 4 and all(kind in ("threads", "replies") for kind, _ in calls)


def test_tiktok_visible_collection_is_explicitly_partial(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    import backend.comments as monitor
    import backend.tiktok_upload as adapter
    import backend.vault as vault

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": "local-fixture",
                    "domain": ".tiktok.com",
                    "path": "/",
                    "secure": True,
                    "expires": datetime.now(UTC).timestamp() + 3600,
                }
            ]
        )
        page = context.new_page()
        home = '<a data-e2e="nav-profile" href="/@own.test">Profile</a>'

        def route(r):
            content = home
            if "/video/" in r.request.url:
                content += (
                    '<div data-e2e="comment-item" data-comment-id="c1">Интересное у меня в профиле</div>'
                )
            r.fulfill(body=content, content_type="text/html")

        context.route("**/*", route)
        page.goto("https://www.tiktok.com/")

        @contextmanager
        def fake_browser(*_, **__):
            yield page

        monkeypatch.setattr(adapter, "browser_page", fake_browser)
        monkeypatch.setattr(vault, "get", lambda _: None)
        try:
            rows, status, detail = monitor.tiktok_comments(
                SimpleNamespace(id="local", external_id="own.test"), "7456123456789012345"
            )
            assert len(rows) == 1 and rows[0]["id"] == "c1" and status == "partial"
            assert "только видимые" in detail
        finally:
            browser.close()

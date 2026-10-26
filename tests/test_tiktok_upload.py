"""Local-only real Chromium fixtures: no TikTok requests or real publication."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from backend import platforms, vault
from backend.app import app
from backend.config import DATA
from backend.db import Account, Asset, Job, Session, Variant, uid
from backend.schemas import Edit
from backend.tiktok_upload import (
    BeforePublishError,
    PublicationUncertain,
    UploadResult,
    publication_id,
    upload_on_page,
)

client = TestClient(app)
COOKIE = {
    "name": "sessionid",
    "value": "synthetic",
    "domain": ".tiktok.com",
    "path": "/",
    "expires": time.time() + 3600,
    "secure": True,
}
VIDEO_ID = "7456123456789012345"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.mark.parametrize(
    "mode",
    [
        "public",
        "private",
        "wrong_account",
        "privacy_missing",
        "no_id",
        "lost_response",
        "button_profile",
        "studio_fallback",
    ],
)
def test_browser_upload_is_single_and_verified(browser, tmp_path, mode, monkeypatch):
    context = browser.new_context()
    context.add_cookies([COOKIE])
    page = context.new_page()
    posts = []
    home = '<a data-e2e="nav-profile" href="/@cartoon.test">Own profile</a>'
    if mode == "button_profile":
        home = (
            '<button data-e2e="nav-profile" onclick="location.href=\'/@cartoon.test\'">Own profile</button>'
        )
    upload = """<input type="file" accept="video/mp4"><div contenteditable="true"></div>
    <div data-e2e="video_visibility_container"><button role="combobox" onclick="document.querySelector('#options').hidden=false">Everyone</button></div>
    <div id="options" hidden><div role="option" onclick="choose('Everyone')">Everyone</div><div role="option" id="private" onclick="choose('Only you')">Only you</div></div>
    <button data-e2e="post_video_button" onclick="fetch('/api/v1/web/project/post/',{method:'POST'}).catch(()=>{})">Post</button>
    <script>function choose(text){document.querySelector('[role=combobox]').textContent=text;document.querySelector('#options').hidden=true}</script>"""
    if mode == "privacy_missing":
        upload = upload.replace('role="combobox"', 'role="presentation"')
    if mode == "studio_fallback":
        import backend.media as media

        monkeypatch.setattr(media, "probe", lambda _: {"duration": 1.5})
        upload = upload.replace(".catch(()=>{})", ".then(()=>location.href='/tiktokstudio/content')")

    def route(r):
        path = r.request.url.split("tiktok.com", 1)[-1]
        if path.startswith("/tiktok/creator/manage/item_list/v1/"):
            r.fulfill(
                json={
                    "status_code": 0,
                    "item_list": [
                        {
                            "item_id": VIDEO_ID,
                            "desc": "Тест #cartoon",
                            "duration": 1500,
                            "create_time": str(int(time.time())),
                            "in_review": False,
                        }
                    ],
                }
            )
        elif path.startswith("/tiktokstudio/content"):
            r.fulfill(
                body='<script>fetch("/tiktok/creator/manage/item_list/v1/",{method:"POST"})</script>',
                content_type="text/html",
            )
        elif r.request.method == "POST":
            posts.append(path)
            if mode == "lost_response":
                r.abort()
            else:
                r.fulfill(
                    json={
                        "status_code": 0,
                        "data": {} if mode in ("no_id", "studio_fallback") else {"item_id": VIDEO_ID},
                    }
                )
        elif path.startswith("/tiktokstudio/upload"):
            r.fulfill(body=upload, content_type="text/html")
        elif path == "/@cartoon.test" and mode == "button_profile":
            r.fulfill(
                body='<button data-e2e="edit-profile-button">Edit profile</button>', content_type="text/html"
            )
        else:
            r.fulfill(body=home, content_type="text/html")

    context.route("**/*", route)
    source = tmp_path / "dummy.mp4"
    source.write_bytes(b"synthetic video input")
    try:
        page.goto("https://www.tiktok.com/")
        expected = "another.account" if mode == "wrong_account" else "cartoon.test"
        config = {"visibility": "private" if mode == "private" else "public", "caption": "Тест #cartoon"}
        checkpoints = []

        def checkpoint():
            checkpoints.append(True)
            assert page.locator("[contenteditable]").inner_text() == config["caption"]
            assert page.get_by_role("combobox").inner_text() == (
                "Only you" if mode == "private" else "Everyone"
            )

        if mode in ("wrong_account", "privacy_missing"):
            with pytest.raises(BeforePublishError):
                upload_on_page(page, source, config, expected, before_post=checkpoint, timeout=1)
            assert not posts and not checkpoints
        elif mode in ("no_id", "lost_response"):
            with pytest.raises(PublicationUncertain):
                upload_on_page(page, source, config, expected, before_post=checkpoint, timeout=1)
            assert len(posts) == len(checkpoints) == 1
        else:
            result = upload_on_page(page, source, config, expected, before_post=checkpoint, timeout=2)
            assert result.external_id == VIDEO_ID and result.status == "platform_processing"
            assert result.url == f"https://www.tiktok.com/@cartoon.test/video/{VIDEO_ID}"
            assert len(posts) == len(checkpoints) == 1
    finally:
        context.close()


@pytest.mark.parametrize(
    "value",
    [
        True,
        {},
        {"status_code": 1, "data": {"item_id": VIDEO_ID}},
        {"status_code": 0, "data": {"id": VIDEO_ID}},
        {"status_code": 0, "data": {"item_id": VIDEO_ID, "aweme_id": "7456123456789012346"}},
    ],
)
def test_no_false_confirmation(value):
    assert publication_id(value) is None


@pytest.fixture
def job():
    key, aid, vid, jid = [uid() for _ in range(4)]
    path = DATA / "exports" / f"{vid}.mp4"
    path.write_bytes(b"local synthetic export")
    with Session.begin() as db:
        db.add(
            Account(
                id=key,
                platform="tiktok",
                name="@cartoon.test",
                external_id="cartoon.test",
                method="browser_login",
                connected=True,
            )
        )
        db.add(
            Asset(
                id=aid,
                kind="video",
                name="fixture.mp4",
                path="unused",
                sha256=uid(),
                media={"duration": 5},
                edit={},
            )
        )
        db.flush()
        db.add(
            Variant(
                id=vid,
                asset_id=aid,
                account_id=key,
                fingerprint=uid(),
                output=str(path),
                config=Edit(visibility="private").model_dump(),
            )
        )
        db.flush()
        db.add(Job(id=jid, variant_id=vid, status="ready"))
    vault.put(key, {"cookies": [COOKIE]})
    yield jid, key
    with Session.begin() as db:
        db.delete(db.get(Job, jid))
        db.flush()
        db.delete(db.get(Variant, vid))
        db.flush()
        db.delete(db.get(Asset, aid))
        db.delete(db.get(Account, key))
    vault.remove(key)
    path.unlink(missing_ok=True)


@pytest.mark.parametrize("outcome", ["accepted", "auth", "prepare", "uncertain"])
def test_worker_requires_explicit_trial_and_never_retries(job, monkeypatch, outcome):
    import backend.tiktok_upload as adapter

    jid, key = job
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "approve"}).status_code == 400
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "test_upload"}).status_code == 200
    calls = []

    def upload(token, source, config, expected, *, before_post):
        calls.append(True)
        assert expected == "cartoon.test" and Path(source).is_file()
        if outcome in ("auth", "prepare"):
            raise BeforePublishError("Synthetic failure", auth=outcome == "auth")
        before_post()
        if outcome == "uncertain":
            raise PublicationUncertain("Synthetic response loss")
        return UploadResult(VIDEO_ID, f"https://www.tiktok.com/@cartoon.test/video/{VIDEO_ID}")

    monkeypatch.setattr(adapter, "upload", upload)
    with Session() as db:
        platforms.dispatch_one(db)
    with Session() as db:
        saved = db.get(Job, jid)
        if outcome == "accepted":
            assert saved.external_id == VIDEO_ID and saved.status == "platform_processing"
            assert db.get(Account, key).method == "browser_verified"
        elif outcome == "uncertain":
            assert saved.status == "unknown" and saved.upload_started_at and not saved.external_id
        else:
            assert saved.status == ("waiting_auth" if outcome == "auth" else "ready")
            assert not saved.approved and not saved.upload_started_at
            assert db.get(Account, key).method == "browser_login"
    if outcome == "uncertain":
        assert client.post(f"/api/jobs/{jid}/action", json={"action": "test_upload"}).status_code == 400
        with Session() as db:
            platforms.dispatch_one(db)
        assert len(calls) == 1


@pytest.mark.parametrize("mode", ["formatting", "controlled", "changed", "slow_editor"])
def test_rich_caption_editor_commits_or_stops(browser, mode):
    from backend.tiktok_upload import normalized_caption, set_caption

    context = browser.new_context()
    page = context.new_page()
    page.set_content('<div id="editor" contenteditable="true"></div><button>Unrelated</button>')
    page.evaluate(
        """mode=>{window.inputs=0;window.rejected=0;const e=document.querySelector('#editor');e.addEventListener('input',event=>{window.inputs++;if(mode==='formatting')e.textContent='\\ufeff'+e.textContent.replaceAll(' ','\\u00a0')+'\\u200b';if((mode==='controlled'||mode==='slow_editor')&&event.data&&event.data.length>1){window.rejected++;e.textContent='';}if(mode==='slow_editor'&&event.data&&event.data.length===1){if(window.lastKey&&performance.now()-window.lastKey<50)e.textContent=e.textContent.slice(0,-1);window.lastKey=performance.now();}if(mode==='changed')e.textContent='foreign description';});}""",
        mode,
    )
    try:
        expected = "hello #бубавпн"
        if mode == "changed":
            with pytest.raises(BeforePublishError):
                set_caption(page, page.locator("#editor"), expected)
        else:
            set_caption(page, page.locator("#editor"), expected)
            assert normalized_caption(page.locator("#editor").inner_text()) == expected
            if mode in ("controlled", "slow_editor"):
                assert page.evaluate("window.rejected") >= 1 and page.evaluate("window.inputs") > 5
    finally:
        context.close()


def test_caption_normalization_preserves_meaning():
    from backend.tiktok_upload import normalized_caption

    assert normalized_caption("\ufeff#бубавпн\u200b\u00a0") == "#бубавпн"
    assert normalized_caption("hello\u00a0world") == "hello world"
    assert normalized_caption("hello world") != normalized_caption("helloworld")
    assert normalized_caption("ABC") != normalized_caption("abc")
    assert normalized_caption("👨\u200d👩\u200d👦") == "👨\u200d👩\u200d👦"


@pytest.mark.parametrize(
    "change", [{}, {"desc": "different"}, {"duration": 5000}, {"create_time": "1"}, {"item_id": "wrong"}]
)
def test_studio_receipt_requires_caption_duration_time_and_id(change):
    from backend.tiktok_upload import studio_candidates

    row = {
        "item_id": VIDEO_ID,
        "desc": "#бубавпн",
        "duration": 109718,
        "create_time": "1793031338",
        "in_review": False,
    }
    row.update(change)
    found = studio_candidates(
        {"status_code": 0, "item_list": [row]}, "#бубавпн", 109.718, 1793031337, 1793031600
    )
    assert bool(found) == (not change)
    assert not studio_candidates(
        {"status_code": 1, "item_list": [row]}, "#бубавпн", 109.718, 1793031337, 1793031600
    )


def test_read_only_studio_reconciliation_saves_id_without_upload(job, monkeypatch):
    from datetime import UTC, datetime

    import backend.tiktok_public as public
    import backend.tiktok_upload as adapter

    monkeypatch.setattr(public, "available", lambda *_: False)

    jid, key = job
    with Session.begin() as db:
        current = db.get(Job, jid)
        current.status, current.approved, current.upload_started_at = (
            "unknown",
            True,
            datetime.now(UTC).isoformat(),
        )

    def must_not_upload(*_, **__):
        raise AssertionError("A check must never publish again")

    monkeypatch.setattr(adapter, "upload", must_not_upload)
    monkeypatch.setattr(
        adapter,
        "check_result",
        lambda *_: UploadResult(VIDEO_ID, f"https://www.tiktok.com/@cartoon.test/video/{VIDEO_ID}"),
    )
    result = client.post(f"/api/jobs/{jid}/tiktok-result")
    assert result.status_code == 200, result.text
    assert result.json()["external_id"] == VIDEO_ID and result.json()["status"] == "platform_processing"
    with Session() as db:
        assert db.get(Account, key).method == "browser_verified"
    monkeypatch.setattr(public, "available", lambda *_: True)
    with Session.begin() as db:
        db.get(Job, jid).updated = "2000-01-01T00:00:00+00:00"
    with Session() as db:
        platforms.dispatch_one(db)
        assert db.get(Job, jid).status == "published"


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"privateItem": True},
        {"id": "7456123456789012346"},
        {"author": {"uniqueId": "other"}},
        {"video": {"playAddr": ""}},
    ],
)
def test_public_visibility_requires_exact_logged_out_item(change):
    import json

    from backend.tiktok_public import public_item

    item = {
        "id": VIDEO_ID,
        "author": {"uniqueId": "cartoon.test"},
        "privateItem": False,
        "video": {"playAddr": "https://example.invalid/video"},
    }
    item.update(change)
    body = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 0, "itemInfo": {"itemStruct": item}}}}
    html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">' + json.dumps(body) + "</script>"
    assert public_item(html, "cartoon.test", VIDEO_ID) == (not change)
    assert not public_item("<html>No usable data</html>", "cartoon.test", VIDEO_ID)


@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0), ("1234", 1234), (None, None), (-1, None), (True, None), (1.5, None), ("1.2K", None)],
)
def test_public_counters_keep_missing_distinct_from_zero(raw, expected):
    from backend.tiktok_public import item_metrics

    values = item_metrics({"stats": {"playCount": raw, "diggCount": 7}})
    assert values == {"views": expected, "likes": 7, "comments": None, "shares": None}
    with pytest.raises(ValueError):
        item_metrics({"stats": {}})


def test_browser_account_metrics_save_public_snapshot_and_preserve_on_failure(job, monkeypatch):
    from sqlalchemy import select

    import backend.tiktok_public as public
    from backend.db import Metric

    jid, key = job
    with Session.begin() as db:
        db.get(Job, jid).external_id = VIDEO_ID
    monkeypatch.setattr(vault, "get", lambda _: None)
    monkeypatch.setattr(
        public,
        "fetch_item",
        lambda handle, vid: {"stats": {"playCount": "42", "diggCount": 0}} if vid == VIDEO_ID else None,
    )
    with Session.begin() as db:
        platforms.refresh_metrics(db)
    with Session() as db:
        rows = db.scalars(select(Metric).where(Metric.job_id == jid)).all()
        assert len(rows) == 1 and rows[0].values["views"] == 42
        assert rows[0].values["likes"] == 0 and rows[0].values["comments"] is None
        assert rows[0].source == "TikTok public page"
    monkeypatch.setattr(public, "fetch_item", lambda *_: None)
    with Session.begin() as db:
        platforms.refresh_metrics(db)
    with Session() as db:
        rows = db.scalars(select(Metric).where(Metric.job_id == jid)).all()
        assert len(rows) == 1 and rows[0].values["views"] == 42
    with Session.begin() as db:
        for row in db.scalars(select(Metric).where(Metric.job_id == jid)).all():
            db.delete(row)

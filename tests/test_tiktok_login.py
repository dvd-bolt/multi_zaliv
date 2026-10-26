"""Interactive sign-in checks. All cookies and TikTok pages here are synthetic."""

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend import platforms, vault
from backend import tiktok_login as login
from backend.app import app
from backend.config import DATA
from backend.db import Account, Asset, Job, Session, Variant, engine, uid
from backend.schemas import Edit

client = TestClient(app)
COOKIE = {
    "name": "sessionid",
    "value": "synthetic-session-never-a-real-token",
    "domain": ".tiktok.com",
    "path": "/",
    "expires": -1,
    "secure": True,
    "httpOnly": True,
    "sameSite": "Lax",
}


@pytest.fixture
def account():
    response = client.post("/api/accounts", json={"platform": "tiktok", "name": "Login check"})
    assert response.status_code == 200
    key = response.json()["id"]
    yield key
    with platforms.cancelling_auth(key):
        vault.remove(key)
        with Session.begin() as db:
            db.delete(db.get(Account, key))
    platforms.AUTH.pop(key, None)


def wait_for(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("Login did not reach expected state")


def state(key):
    return next(a for a in client.get("/api/state").json()["accounts"] if a["id"] == key)


def prepare_attempt(key, handle="cartoon.test"):
    attempt = login.LoginAttempt()
    attempt.handle = handle
    platforms.AUTH_TICKETS[key] = attempt
    platforms.AUTH[key] = "confirm:@" + handle
    return attempt


def fake_site_browser(playwright):
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    original = browser.new_context

    def context(**kwargs):
        ctx = original(**kwargs)
        ctx.add_cookies([COOKIE])
        ctx.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html",
                body='<a data-e2e="nav-profile" href="https://www.tiktok.com/@cartoon.test">Profile</a>',
            ),
        )
        return ctx

    browser.new_context = context
    return browser


def test_identity_requires_session_and_own_profile_navigation():
    page = SimpleNamespace(
        url="https://www.tiktok.com/", evaluate=lambda _: ["https://www.tiktok.com/@cartoon.test"]
    )
    assert login.read_identity(page, []) is None
    assert login.read_identity(page, [COOKIE]) == "cartoon.test"
    page.url = "https://fake-tiktok.com/"
    assert login.read_identity(page, [COOKIE]) is None
    page.url = "https://www.tiktok.com/@public.person"
    page.evaluate = lambda _: []
    assert login.read_identity(page, [COOKIE]) is None
    page.evaluate = lambda _: ["https://www.tiktok.com/@one", "https://www.tiktok.com/@two"]
    assert login.read_identity(page, [COOKIE]) is None
    assert not login.has_session([{**COOKIE, "domain": ".not-tiktok.com"}])
    assert not login.has_session([{**COOKIE, "expires": time.time() - 5}])
    assert not login.has_session([{**COOKIE, "value": ""}])
    assert login.profile_handle("https://www.tiktok.com/@one/video/123") is None


def test_google_rejection_detection_is_limited_to_google_denial_page():
    assert login.google_rejected("https://accounts.google.com/v3/signin/rejected?app_domain=example")
    assert login.google_rejected("https://accounts.google.com/signin/rejected")
    assert not login.google_rejected("https://accounts.google.com/v3/signin/identifier")
    assert not login.google_rejected("https://accounts.google.com.evil.example/v3/signin/rejected")
    assert not login.google_rejected("https://www.tiktok.com/login")


def test_google_denial_can_return_to_tiktok_without_saving_or_bypassing(account, monkeypatch):
    def browser_factory(playwright):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        original = browser.new_context

        def new_context(**kwargs):
            ctx = original(**kwargs)
            visits = 0

            def serve(route):
                nonlocal visits
                if route.request.url.startswith(login.LOGIN_URL):
                    visits += 1
                    if visits == 1:
                        route.fulfill(
                            content_type="text/html",
                            body="<script>setTimeout(()=>location.href='https://accounts.google.com/v3/signin/rejected',100)</script>",
                        )
                    else:
                        ctx.add_cookies([COOKIE])
                        route.fulfill(
                            content_type="text/html",
                            body='<a data-e2e="nav-profile" href="/@cartoon.test">Profile</a>',
                        )
                else:
                    route.fulfill(content_type="text/html", body="Google sign-in rejected")

            ctx.route("**/*", serve)
            return ctx

        browser.new_context = new_context
        return browser

    monkeypatch.setattr(login, "open_browser", browser_factory)
    thread = login.begin_tiktok(account)
    try:
        wait_for(lambda: state(account).get("auth") == "action:google")
        assert login.pending(state(account)["auth"])
        assert vault.get(account) is None and not state(account)["connected"]
        assert client.post(f"/api/accounts/{account}/connect").status_code == 400
        assert client.post(f"/api/accounts/{account}/connect/return-to-tiktok").status_code == 200
        wait_for(lambda: state(account).get("auth") == "confirm:@cartoon.test")
        assert vault.get(account) is None  # Returning does not confirm a login.
    finally:
        client.post(f"/api/accounts/{account}/connect/cancel")
        thread.join(timeout=15)
        assert not thread.is_alive()


def test_interactive_browser_login_requires_confirmation_and_encrypts(account, monkeypatch):
    monkeypatch.setattr(login, "open_browser", fake_site_browser)
    thread = login.begin_tiktok(account)
    try:
        wait_for(lambda: state(account).get("auth") == "confirm:@cartoon.test")
        assert vault.get(account) is None
        assert not state(account)["connected"]
        assert (
            client.post(f"/api/accounts/{account}/connect/confirm", json={"handle": "wrong"}).status_code
            == 400
        )
        assert client.post(f"/api/accounts/{account}/connect").status_code == 400
        assert (
            client.post(
                f"/api/accounts/{account}/connect/confirm", json={"handle": "cartoon.test"}
            ).status_code
            == 200
        )
        thread.join(timeout=15)
        assert not thread.is_alive()
        connected = state(account)
        assert connected["connected"] and connected["method"] == "browser_login"
        assert connected["external_id"] == "cartoon.test"
        token = vault.get(account)
        assert token["cookies"][0]["value"] == COOKIE["value"]
        assert token["auth_method"] == "interactive_profile_confirmed"
        assert COOKIE["value"].encode() not in vault.secret_path(account).read_bytes()
        assert COOKIE["value"] not in client.get("/api/state").text
        assert not (DATA / "state.json").exists()
    finally:
        client.post(f"/api/accounts/{account}/connect/cancel")
        thread.join(timeout=15)


def test_cancel_closes_login_and_rejects_late_confirmation(account, monkeypatch):
    monkeypatch.setattr(login, "open_browser", fake_site_browser)
    thread = login.begin_tiktok(account)
    try:
        wait_for(lambda: state(account).get("auth") == "confirm:@cartoon.test")
        assert client.post(f"/api/accounts/{account}/connect/cancel").status_code == 200
        assert (
            client.post(
                f"/api/accounts/{account}/connect/confirm", json={"handle": "cartoon.test"}
            ).status_code
            == 400
        )
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert not state(account)["connected"] and vault.get(account) is None
    finally:
        client.post(f"/api/accounts/{account}/connect/cancel")
        thread.join(timeout=15)


def test_duplicate_handle_is_rejected_without_changing_session(account):
    with Session.begin() as db:
        other = Account(platform="tiktok", name="Existing", external_id="CARTOON.TEST")
        db.add(other)
        db.flush()
        other_id = other.id
    attempt = prepare_attempt(account)
    try:
        with pytest.raises(platforms.AccountIdentityError):
            login.save_login(account, attempt, "cartoon.test", {"cookies": [COOKIE]})
        assert vault.get(account) is None and not state(account)["connected"]
    finally:
        with Session.begin() as db:
            db.delete(db.get(Account, other_id))


def test_browser_start_failure_does_not_expose_remote_or_secret_details(account, monkeypatch):
    def fail(_):
        raise RuntimeError("Sensitive browser error: " + COOKIE["value"])

    monkeypatch.setattr(login, "open_browser", fail)
    thread = login.begin_tiktok(account)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert state(account)["auth"].startswith("error:")
    assert COOKIE["value"] not in client.get("/api/state").text
    assert account not in platforms.AUTH_TICKETS


def test_login_timeout_releases_slot_without_saving(account, monkeypatch):
    monkeypatch.setattr(login, "open_browser", fake_site_browser)
    monkeypatch.setattr(login, "LOGIN_TIMEOUT", 0.01)
    thread = login.begin_tiktok(account)
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert "истекло" in state(account)["auth"]
    assert vault.get(account) is None and account not in platforms.AUTH_TICKETS


def test_cancelled_attempt_cannot_save_or_override_existing_session(account):
    attempt = prepare_attempt(account)
    vault.put(account, {"old": True})
    client.post(f"/api/accounts/{account}/connect/cancel")
    assert not login.save_login(account, attempt, "cartoon.test", {"cookies": [COOKIE]})
    assert vault.get(account) == {"old": True}


def test_failed_database_write_restores_previous_encrypted_session(account):
    attempt = prepare_attempt(account)
    vault.put(account, {"old": True})

    def fail_write(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE accounts"):
            raise RuntimeError("Synthetic database write failure")

    event.listen(engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(RuntimeError):
            login.save_login(account, attempt, "cartoon.test", {"cookies": [COOKIE]})
    finally:
        event.remove(engine, "before_cursor_execute", fail_write)
    assert vault.get(account) == {"old": True}
    assert not state(account)["connected"]


def test_saved_login_does_not_enable_unverified_publication(account, monkeypatch):
    attempt = prepare_attempt(account)
    assert login.save_login(
        account,
        attempt,
        "cartoon.test",
        {"cookies": [COOKIE], "origins": [{"origin": "https://evil.example", "localStorage": []}]},
    )
    assert vault.get(account)["storage_state"]["origins"] == []
    with Session.begin() as db:
        asset = Asset(
            kind="video",
            name="synthetic",
            path="unused",
            sha256=uid(),
            media={"duration": 5},
            edit=Edit().model_dump(),
        )
        db.add(asset)
        db.flush()
        variant = Variant(
            asset_id=asset.id, account_id=account, fingerprint=uid(), config={}, output="unused"
        )
        db.add(variant)
        db.flush()
        job = Job(variant_id=variant.id, status="ready")
        db.add(job)
        db.flush()
        job_id, variant_id, asset_id = job.id, variant.id, asset.id
    try:
        assert client.post(f"/api/jobs/{job_id}/action", json={"action": "approve"}).status_code == 400
        with Session.begin() as db:
            job = db.get(Job, job_id)
            job.status, job.approved = "scheduled", True
        with Session.begin() as db:
            platforms.dispatch_one(db)
        with Session() as db:
            job = db.get(Job, job_id)
            assert job.status == "waiting_auth" and not job.upload_started_at
            assert "Автопубликация" in job.error
        with pytest.raises(platforms.AccountIdentityError):
            login.save_login(account, attempt, "another.person", {"cookies": [COOKIE]})
    finally:
        with Session.begin() as db:
            db.delete(db.get(Job, job_id))
            db.flush()
            db.delete(db.get(Variant, variant_id))
            db.flush()
            db.delete(db.get(Asset, asset_id))

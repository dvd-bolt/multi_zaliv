import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import browser_pairing as bridge
from backend import platforms, vault
from backend.app import app
from backend.db import Account, Session

client = TestClient(app)
ORIGIN = "chrome-extension://" + "a" * 32
HEADERS = {"Origin": ORIGIN}
COOKIE = {
    "name": "sessionid",
    "value": "synthetic-extension-session",
    "domain": ".tiktok.com",
    "path": "/",
    "expires": -1,
    "secure": True,
    "httpOnly": True,
    "sameSite": "Lax",
}


@pytest.fixture
def pairing(monkeypatch):
    monkeypatch.setattr(bridge, "open_regular_chrome", lambda: None)
    key = client.post("/api/accounts", json={"platform": "tiktok", "name": "Chrome check"}).json()["id"]
    assert client.post(f"/api/accounts/{key}/connect").json()["status"] == "browser_waiting"
    details = client.get(f"/api/accounts/{key}/browser-pairing").json()
    yield key, details["code"]
    with platforms.cancelling_auth(key):
        vault.remove(key)
        with Session.begin() as db:
            db.delete(db.get(Account, key))
    platforms.AUTH.pop(key, None)


def handshake(code):
    code_id = hashlib.sha256(code.encode()).hexdigest()
    nonce = "b" * 32
    checked = client.post(
        "/api/browser-pairing/check", headers=HEADERS, json={"code_id": code_id, "client_nonce": nonce}
    )
    assert checked.status_code == 200
    data = checked.json()
    assert data["server_proof"] == bridge.mac(code, f"server|{nonce}|{data['server_nonce']}")
    assert code not in checked.text
    return {"code_id": code_id, "client_nonce": nonce, "server_nonce": data["server_nonce"]}


def signed(code, data, *, cookies=None):
    raw = json.dumps(
        {"handle": "cartoon.chrome", "cookies": [COOKIE] if cookies is None else cookies}, ensure_ascii=False
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return {
        **data,
        "payload": raw,
        "proof": bridge.mac(code, f"client|{data['client_nonce']}|{data['server_nonce']}|{digest}"),
    }


def test_browser_bridge_requires_both_proof_and_user_confirmation(pairing):
    key, code = pairing
    body = signed(code, handshake(code))
    received = client.post("/api/browser-pairing/submit", headers=HEADERS, json=body)
    assert received.status_code == 200
    assert received.json() == {"status": "confirm_required", "handle": "cartoon.chrome"}
    assert vault.get(key) is None
    assert client.post("/api/browser-pairing/submit", headers=HEADERS, json=body).status_code == 400
    assert client.post(f"/api/accounts/{key}/connect/confirm", json={"handle": "other"}).status_code == 400
    assert (
        client.post(f"/api/accounts/{key}/connect/confirm", json={"handle": "cartoon.chrome"}).status_code
        == 200
    )
    token = vault.get(key)
    assert token["cookies"][0]["value"] == COOKIE["value"]
    assert token["auth_method"] == "browser_extension_confirmed"
    assert COOKIE["value"].encode() not in vault.secret_path(key).read_bytes()
    assert key not in platforms.AUTH_TICKETS
    assert client.get(f"/api/accounts/{key}/browser-pairing").status_code == 400
    with Session() as db:
        assert db.get(Account, key).method == "browser_login"


def test_extension_cors_does_not_open_other_api_routes(pairing):
    key, code = pairing
    options = client.options(
        "/api/browser-pairing/check",
        headers={
            **HEADERS,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert options.status_code == 204
    assert options.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in client.get("/api/state", headers=HEADERS).headers
    assert client.post(f"/api/accounts/{key}/disconnect", headers=HEADERS).status_code == 403
    assert (
        client.post(
            "/api/browser-pairing/check", headers={"Origin": "https://evil.example"}, json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/browser-pairing/check",
            headers=HEADERS,
            json={"code_id": "f" * 64, "client_nonce": "a" * 32},
        ).status_code
        == 400
    )


def test_payload_tampering_and_unrelated_site_cookies_are_rejected(pairing):
    key, code = pairing
    data = handshake(code)
    body = signed(code, data)
    body["payload"] = body["payload"].replace("cartoon.chrome", "another.person")
    assert client.post("/api/browser-pairing/submit", headers=HEADERS, json=body).status_code == 400
    body = signed(code, data, cookies=[COOKIE, {**COOKIE, "domain": ".google.com"}])
    assert client.post("/api/browser-pairing/submit", headers=HEADERS, json=body).status_code == 400
    body = signed(code, data)
    body["proof"] = "не подпись"
    assert client.post("/api/browser-pairing/submit", headers=HEADERS, json=body).status_code == 400
    assert vault.get(key) is None


def test_cancel_and_expiry_invalidate_pairing_and_leave_previous_session(pairing):
    key, code = pairing
    data = handshake(code)
    attempt = platforms.AUTH_TICKETS[key]
    attempt.expires = time.time() - 1
    assert (
        client.post("/api/browser-pairing/submit", headers=HEADERS, json=signed(code, data)).status_code
        == 400
    )
    vault.put(key, {"old": True})
    client.post(f"/api/accounts/{key}/connect/cancel")
    assert attempt.code == "" and attempt.payload is None
    assert vault.get(key) == {"old": True}
    assert client.post("/api/browser-pairing/check", headers=HEADERS, json={**data}).status_code == 400


def test_regular_chrome_launch_has_no_automation_or_profile_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.Path, "is_file", lambda _: True)
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda args, **kwargs: calls.append(args))
    bridge.open_regular_chrome()
    assert calls[0][1:] == ["--new-tab", "https://www.tiktok.com/"]

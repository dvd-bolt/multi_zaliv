"""Explicit session transfer from the user's Chrome extension to local SurvivalFlow."""

import hashlib
import hmac
import json
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from .db import Account, Session
from .tiktok_login import pending, save_login, session_cookies

BRIDGE_PATHS = {"/api/browser-pairing/check", "/api/browser-pairing/submit"}
TIKTOK_URL = "https://www.tiktok.com/"


def extension_origin(origin):
    return bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin or ""))


class BrowserPairing:
    def __init__(self, label):
        self.code = secrets.token_urlsafe(24)
        self.code_id = hashlib.sha256(self.code.encode()).hexdigest()
        self.expires = time.time() + 600
        self.label = label
        self.client_nonce = None
        self.server_nonce = None
        self.payload = None
        self.timer = None

    def dispose(self):
        self.code, self.payload = "", None
        if self.timer:
            self.timer.cancel()


def mac(code, message):
    return hmac.new(code.encode(), message.encode(), hashlib.sha256).hexdigest()


def open_regular_chrome():
    # No profile override or debugging flags: Chrome opens a normal tab in the
    # running user's profile. The extension runs in the profile the user selects.
    for directory in (
        Path("C:/Program Files/Google/Chrome/Application"),
        Path("C:/Program Files (x86)/Google/Chrome/Application"),
    ):
        executable = directory / "chrome.exe"
        if executable.is_file():
            subprocess.Popen(
                [str(executable), "--new-tab", TIKTOK_URL], creationflags=subprocess.CREATE_NO_WINDOW
            )
            return
    if not webbrowser.open_new_tab(TIKTOK_URL):
        raise RuntimeError("Не удалось открыть браузер. Откройте tiktok.com в своём Chrome вручную.")


def begin_browser(key):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK, Session() as db:
        if any(pending(status) for status in AUTH.values()):
            raise ValueError("Сначала завершите или отмените текущий вход")
        account = db.get(Account, key)
        if not account or account.platform != "tiktok":
            raise ValueError("Выберите TikTok-аккаунт")
        attempt = BrowserPairing(account.name)
        AUTH_TICKETS[key], AUTH[key] = attempt, "browser_waiting"

    def expire():
        with AUTH_LOCK:
            if AUTH_TICKETS.get(key) is attempt:
                AUTH_TICKETS.pop(key, None)
                AUTH[key] = "error: Код подключения истёк. Откройте подключение заново."
                attempt.dispose()

    timer = threading.Timer(600, expire)
    timer.daemon = True
    attempt.timer = timer
    timer.start()
    try:
        open_regular_chrome()
    except Exception:
        expire()
        raise


def pairing_details(key):
    from .platforms import AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK:
        attempt = AUTH_TICKETS.get(key)
        if not isinstance(attempt, BrowserPairing) or attempt.expires <= time.time():
            raise ValueError("Нет действующего подключения через расширение")
        return {"code": attempt.code, "expires_at": attempt.expires}


def find_pairing(code_id):
    from .platforms import AUTH, AUTH_TICKETS

    if not isinstance(code_id, str) or not re.fullmatch(r"[a-f0-9]{64}", code_id):
        raise ValueError("Неверный или истёкший код подключения")
    for key, attempt in AUTH_TICKETS.items():
        if isinstance(attempt, BrowserPairing) and hmac.compare_digest(attempt.code_id, code_id):
            if attempt.expires > time.time() and AUTH.get(key) == "browser_waiting":
                return key, attempt
    raise ValueError("Неверный или истёкший код подключения")


def check_pairing(payload):
    from .platforms import AUTH_LOCK

    nonce = payload.get("client_nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
        raise ValueError("Некорректный запрос расширения")
    with AUTH_LOCK:
        _, attempt = find_pairing(payload.get("code_id"))
        attempt.client_nonce, attempt.server_nonce = nonce, secrets.token_urlsafe(24)
        message = f"server|{nonce}|{attempt.server_nonce}"
        return {
            "server_nonce": attempt.server_nonce,
            "server_proof": mac(attempt.code, message),
            "account_label": attempt.label,
        }


def submit_pairing(payload):
    from .platforms import AUTH, AUTH_LOCK

    raw, proof = payload.get("payload"), payload.get("proof")
    if (
        not isinstance(raw, str)
        or len(raw.encode()) > 131072
        or not isinstance(proof, str)
        or not re.fullmatch(r"[a-f0-9]{64}", proof)
    ):
        raise ValueError("Некорректные данные расширения")
    with AUTH_LOCK:
        key, attempt = find_pairing(payload.get("code_id"))
        if (
            not attempt.server_nonce
            or payload.get("client_nonce") != attempt.client_nonce
            or payload.get("server_nonce") != attempt.server_nonce
        ):
            raise ValueError("Подключение изменилось. Повторите действие в расширении.")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        message = f"client|{attempt.client_nonce}|{attempt.server_nonce}|{digest}"
        if not hmac.compare_digest(mac(attempt.code, message), proof):
            raise ValueError("Подпись расширения не совпала с кодом подключения")
        try:
            data = json.loads(raw)
            handle, cookies = data["handle"], data["cookies"]
            if not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", handle):
                raise ValueError()
            if not isinstance(cookies, list) or not 1 <= len(cookies) <= 200:
                raise ValueError()
            for cookie in cookies:
                if not isinstance(cookie, dict) or not isinstance(cookie.get("expires", -1), (int, float)):
                    raise ValueError()
                if not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str):
                    raise ValueError()
            cleaned = session_cookies(cookies)
            if len(cleaned) != len(cookies) or not any(
                c["name"] == "sessionid" and c["value"] for c in cleaned
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError(
                "Расширение должно передать действующую сессию и собственный профиль TikTok"
            ) from None
        attempt.payload = {"handle": handle, "cookies": cleaned}
        AUTH[key] = "confirm:@" + handle
        return {"status": "confirm_required", "handle": handle}


def is_browser_pairing(key):
    from .platforms import AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK:
        return isinstance(AUTH_TICKETS.get(key), BrowserPairing)


def confirm_browser(key, handle):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS, AccountIdentityError, auth_result

    with AUTH_LOCK:
        attempt = AUTH_TICKETS.get(key)
        if (
            not isinstance(attempt, BrowserPairing)
            or attempt.expires <= time.time()
            or not attempt.payload
            or AUTH.get(key) != "confirm:@" + handle
            or attempt.payload["handle"] != handle
        ):
            raise ValueError("Аккаунт или код подключения изменился. Повторите подключение.")
        AUTH[key] = "verifying"
        state = {"cookies": attempt.payload["cookies"]}
    try:
        if not save_login(key, attempt, handle, state, auth_method="browser_extension_confirmed"):
            raise ValueError("Подключение было отменено")
    except (AccountIdentityError, ValueError) as error:
        auth_result(key, attempt, "error: " + str(error))
        raise
    except Exception:
        auth_result(key, attempt, "error: Не удалось сохранить подключение. Повторите попытку.")
        raise RuntimeError("Не удалось сохранить подключение") from None
    finally:
        with AUTH_LOCK:
            if AUTH_TICKETS.get(key) is attempt:
                AUTH_TICKETS.pop(key, None)
            attempt.dispose()

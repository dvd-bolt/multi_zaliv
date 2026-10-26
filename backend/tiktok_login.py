"""Interactive TikTok sign-in; credentials are entered only on TikTok's own page."""

import re
import threading
import time
from urllib.parse import unquote, urlsplit

from sqlalchemy import func, select, text

from . import vault
from .db import Account, Session, now

LOGIN_URL = "https://www.tiktok.com/login"
LOGIN_TIMEOUT = 600
PROFILE_LINKS = """() => {
    const links = [...document.querySelectorAll('[data-e2e="nav-profile"], [data-e2e="nav-profile-link"]')]
        .map(node => (node.closest('a') || node.querySelector('a'))?.href).filter(Boolean);
    if (document.querySelector('[data-e2e="edit-profile-entrance"], [data-e2e="edit-profile-button"]'))
        links.push(location.href);
    return links;
}"""


class LoginAttempt:
    def __init__(self):
        self.confirmed = threading.Event()
        self.return_to_tiktok = threading.Event()
        self.handle = None


def profile_handle(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.tiktok.com", "tiktok.com"):
        return None
    match = re.fullmatch(r"/@([A-Za-z0-9_.]{2,24})/?", unquote(parsed.path))
    return match[1] if match else None


def session_cookies(cookies):
    allowed = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
    return [
        {key: value for key, value in cookie.items() if key in allowed}
        for cookie in cookies
        if str(cookie.get("domain", "")).lstrip(".") in ("tiktok.com", "www.tiktok.com")
        and (cookie.get("expires", -1) == -1 or cookie.get("expires", 0) > time.time())
    ]


def has_session(cookies):
    return any(c.get("name") == "sessionid" and c.get("value") for c in session_cookies(cookies))


def read_identity(page, cookies):
    # Only use the signed-in profile navigation on TikTok, never arbitrary links
    # in the feed, a visited public profile, an imported handle or an offsite page.
    parsed = urlsplit(page.url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.tiktok.com", "tiktok.com"):
        return None
    if not has_session(cookies):
        return None
    handles = {handle for url in page.evaluate(PROFILE_LINKS) if (handle := profile_handle(url))}
    return handles.pop() if len(handles) == 1 else None


def pending(status):
    return status in ("waiting", "verifying", "action:google", "browser_waiting") or status.startswith(
        "confirm:"
    )


def google_rejected(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "accounts.google.com"
        and parsed.path in ("/v3/signin/rejected", "/signin/rejected")
    )


def return_to_tiktok(key):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK:
        attempt = AUTH_TICKETS.get(key)
        if not isinstance(attempt, LoginAttempt) or AUTH.get(key) != "action:google":
            raise ValueError("Окно входа изменилось. Проверьте текущее состояние подключения.")
        attempt.return_to_tiktok.set()


def confirm_login(key, handle):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK:
        attempt = AUTH_TICKETS.get(key)
        if not isinstance(attempt, LoginAttempt) or AUTH.get(key) != "confirm:@" + handle:
            raise ValueError("Вход изменился. Дождитесь определения аккаунта и подтвердите его заново.")
        if attempt.handle != handle:
            raise ValueError("Подтвердите именно аккаунт, найденный в браузере")
        AUTH[key] = "verifying"
        attempt.confirmed.set()


def save_login(key, attempt, handle, state, auth_method="interactive_profile_confirmed"):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS, AccountIdentityError, check_rebinding

    cookies = session_cookies(state["cookies"])
    if not has_session(cookies):
        raise ValueError("TikTok не вернул действующую сессию")
    origins = [
        origin for origin in state.get("origins", []) if origin.get("origin") == "https://www.tiktok.com"
    ]
    with AUTH_LOCK:
        if AUTH_TICKETS.get(key) is not attempt:
            return False
        previous = vault.get(key)
        wrote_secret = False
        try:
            with Session.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                account = db.get(Account, key)
                if not account or account.platform != "tiktok":
                    raise AccountIdentityError("Запись TikTok-аккаунта недоступна")
                check_rebinding(db, account, handle)
                duplicate = db.scalar(
                    select(Account.id).where(
                        Account.platform == "tiktok",
                        func.lower(Account.external_id) == handle.casefold(),
                        Account.id != key,
                    )
                )
                if duplicate:
                    raise AccountIdentityError("Этот TikTok-аккаунт уже подключён в другой записи")
                vault.put(
                    key,
                    {
                        "handle": handle,
                        "cookies": cookies,
                        "storage_state": {"cookies": cookies, "origins": origins},
                        "verified_at": now(),
                        "auth_method": auth_method,
                    },
                )
                wrote_secret = True
                account.external_id, account.name = handle, "@" + handle
                account.connected, account.method = True, "browser_login"
        except Exception:
            if wrote_secret:
                if previous is None:
                    vault.remove(key)
                else:
                    vault.put(key, previous)
            raise
        AUTH[key] = "connected"
    return True


def run_browser(key, attempt):
    from playwright.sync_api import Error, sync_playwright

    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS, AccountIdentityError, auth_result

    try:
        with sync_playwright() as playwright:
            browser = open_browser(playwright)
            try:
                context = browser.new_context(viewport={"width": 1120, "height": 800}, locale="ru-RU")
                context.set_default_timeout(2000)
                page = context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                deadline = time.monotonic() + LOGIN_TIMEOUT
                while time.monotonic() < deadline:
                    with AUTH_LOCK:
                        if AUTH_TICKETS.get(key) is not attempt:
                            return
                    if not context.pages or not browser.is_connected():
                        auth_result(
                            key, attempt, "error: Окно входа закрыто. Нажмите «Войти в TikTok» ещё раз."
                        )
                        return
                    try:
                        if attempt.return_to_tiktok.is_set():
                            # Navigate to TikTok's own supported alternative sign-in.
                            # Do not alter Google requests or browser security settings.
                            attempt.return_to_tiktok.clear()
                            for popup in context.pages:
                                if popup is not page and google_rejected(popup.url):
                                    popup.close()
                            if page.is_closed():
                                page = context.new_page()
                            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                            auth_result(key, attempt, "waiting")
                        if page.is_closed():
                            page = context.pages[0]
                        cookies = context.cookies()
                        handle = read_identity(page, cookies)
                        if not handle and any(google_rejected(tab.url) for tab in context.pages):
                            auth_result(key, attempt, "action:google")
                            page.wait_for_timeout(750)
                            continue
                        with AUTH_LOCK:
                            if AUTH_TICKETS.get(key) is attempt and AUTH.get(key) == "action:google":
                                AUTH[key] = "waiting"
                        if attempt.confirmed.is_set():
                            if not handle or handle.casefold() != (attempt.handle or "").casefold():
                                auth_result(
                                    key, attempt, "error: Аккаунт в браузере изменился. Повторите вход."
                                )
                                return
                            state = context.storage_state()
                            before = {
                                (c["domain"], c["value"])
                                for c in session_cookies(cookies)
                                if c["name"] == "sessionid"
                            }
                            captured = {
                                (c["domain"], c["value"])
                                for c in session_cookies(state["cookies"])
                                if c["name"] == "sessionid"
                            }
                            if before != captured:
                                auth_result(
                                    key,
                                    attempt,
                                    "error: Сессия изменилась во время подтверждения. Повторите вход.",
                                )
                                return
                            save_login(key, attempt, handle, state)
                            return
                        if handle:
                            with AUTH_LOCK:
                                if AUTH_TICKETS.get(key) is attempt and not attempt.confirmed.is_set():
                                    attempt.handle = handle
                                    AUTH[key] = "confirm:@" + handle
                    except Error:
                        # A normal login redirect briefly replaces the execution context.
                        pass
                    page.wait_for_timeout(750)
                auth_result(key, attempt, "error: Время входа истекло (10 минут). Начните вход заново.")
            finally:
                browser.close()
    except AccountIdentityError as error:
        auth_result(key, attempt, "error: " + str(error))
    except Exception:
        # Browser/network errors may contain URLs or session details; never relay them.
        auth_result(
            key,
            attempt,
            "error: Не удалось завершить вход. Проверьте Chrome и доступ к TikTok, затем повторите.",
        )
    finally:
        with AUTH_LOCK:
            if AUTH_TICKETS.get(key) is attempt:
                AUTH_TICKETS.pop(key, None)


def open_browser(playwright):
    return playwright.chromium.launch(channel="chrome", headless=False)


def begin_tiktok(key):
    from .platforms import AUTH, AUTH_LOCK, AUTH_TICKETS

    with AUTH_LOCK:
        if any(pending(status) for status in AUTH.values()):
            raise ValueError("Сначала завершите или отмените текущий вход в браузере")
        attempt = LoginAttempt()
        AUTH_TICKETS[key], AUTH[key] = attempt, "waiting"
    thread = threading.Thread(target=run_browser, args=(key, attempt), daemon=True)
    thread.start()
    return thread

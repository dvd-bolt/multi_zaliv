"""TikTok web upload: verified identity/privacy, one post click, no retries.

DOM selector references: wkaisertexas/tiktok-uploader (MIT), 1.2.0.
We do not use its stealth browser, swallowed privacy errors or fallback clicks.
"""

import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .tiktok_login import has_session, read_identity, session_cookies

HOME = "https://www.tiktok.com/"
UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
PRIVACY = {
    "public": re.compile(r"^(Everyone|Public|Все|Для всех|Общедоступное)$", re.I),
    "private": re.compile(r"^(Only you|Private|Только я|Только вы)$", re.I),
}


class BeforePublishError(ValueError):
    """No publish control was clicked; user may correct the problem and approve again."""

    def __init__(self, message, *, auth=False):
        super().__init__(message)
        self.auth = auth


class PublicationUncertain(RuntimeError):
    """A publish control may have been clicked. Never repeat automatically."""


@dataclass(frozen=True)
class UploadResult:
    external_id: str
    url: str
    status: str = "platform_processing"
    review_pending: bool | None = None


def studio_candidates(payload, caption, duration, started, ended):
    """Match the authenticated owner's Studio list, never just the newest arbitrary video."""
    if not isinstance(payload, dict) or payload.get("status_code") != 0:
        return []
    matches = []
    for item in payload.get("item_list", []):
        try:
            ident = str(item["item_id"])
            if not re.fullmatch(r"[0-9]{15,25}", ident):
                continue
            if normalized_caption(item["desc"]) != normalized_caption(caption):
                continue
            if abs(float(item["duration"]) / 1000 - duration) > 1:
                continue
            created = float(item["create_time"])
            if not started - 10 <= created <= min(ended + 30, started + 300):
                continue
            pending = item.get("in_review")
            matches.append((ident, pending if isinstance(pending, bool) else None))
        except (KeyError, ValueError, TypeError):
            continue
    return matches


def studio_result(page, expected, caption, duration, started):
    matches = {}
    signature = session_signature(page.context)

    def collect(response):
        if (
            tiktok_url(response.url)
            and urlsplit(response.url).path == "/tiktok/creator/manage/item_list/v1/"
            and response.status == 200
            and response.request.method in ("GET", "POST")
        ):
            try:
                for ident, pending in studio_candidates(
                    response.json(), caption, duration, started, time.time()
                ):
                    matches[ident] = pending
            except Exception:
                pass

    page.on("response", collect)
    try:
        page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
    finally:
        page.remove_listener("response", collect)
    if len(matches) != 1 or not signature or session_signature(page.context) != signature:
        return None
    ident, pending = next(iter(matches.items()))
    return UploadResult(ident, f"https://www.tiktok.com/@{expected}/video/{ident}", review_pending=pending)


def tiktok_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in ("www.tiktok.com", "tiktok.com")


def publication_id(payload):
    """Accept only a positive publish response with a concrete video id, not a boolean."""
    if not isinstance(payload, dict):
        return None
    status = payload.get("status_code", payload.get("statusCode"))
    if isinstance(status, bool) or status not in (0, "0"):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    ids = set()
    for container in (data, data.get("item", {}), data.get("aweme", {})):
        if not isinstance(container, dict):
            continue
        for key in ("item_id", "aweme_id", "itemId"):
            value = container.get(key)
            if isinstance(value, (str, int)) and re.fullmatch(r"[0-9]{15,25}", str(value)):
                ids.add(str(value))
    return ids.pop() if len(ids) == 1 else None


def publish_response(response):
    path = urlsplit(response.url).path.rstrip("/")
    return (
        tiktok_url(response.url)
        and response.request.method == "POST"
        and path
        in (
            "/api/v1/web/project/post",
            "/api/post/publish",
            "/api/post/item_commit",
        )
    )


def identity(page, expected):
    if not tiktok_url(page.url) or not expected or not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", expected):
        raise BeforePublishError("Не удалось подтвердить адрес TikTok или имя аккаунта", auth=True)
    found = read_identity(page, page.context.cookies())
    if not found and has_session(page.context.cookies()):
        navigation = page.locator('[data-e2e="nav-profile"], [data-e2e="nav-profile-link"]')
        if navigation.count() == 1 and navigation.is_visible():
            navigation.click()
            try:
                page.locator(
                    '[data-e2e="edit-profile-entrance"], [data-e2e="edit-profile-button"]'
                ).first.wait_for(timeout=5000)
            except Exception:
                pass
            found = read_identity(page, page.context.cookies())
    if not found or found.casefold() != expected.casefold():
        raise BeforePublishError(
            "TikTok не подтвердил нужный аккаунт. Переподключите его через расширение Chrome.", auth=True
        )
    return found


def session_signature(context):
    return {(c["domain"], c["value"]) for c in session_cookies(context.cookies()) if c["name"] == "sessionid"}


def upload_frame(page):
    for frame in page.frames:
        if tiktok_url(frame.url) and frame.locator("input[type=file]").count():
            return frame
    return None


def set_privacy(frame, visibility):
    matcher = PRIVACY.get(visibility)
    if matcher is None:
        raise BeforePublishError("В TikTok нельзя отправить ролик с видимостью «По ссылке»")
    dropdown = frame.locator('[data-e2e="video_visibility_container"] [role="combobox"]')
    if dropdown.count() != 1:
        raise BeforePublishError("TikTok изменил поле видимости. Отправка остановлена до публикации.")
    dropdown.click()
    options = frame.get_by_role("option", name=matcher)
    if options.count() != 1:
        raise BeforePublishError("TikTok не предлагает выбранную видимость. Публикация не нажималась.")
    options.click()
    if not matcher.fullmatch(dropdown.inner_text().strip()):
        raise BeforePublishError("TikTok не подтвердил выбранную видимость. Публикация не нажималась.")


def normalized_caption(value):
    """Ignore rich-editor formatting, preserving meaningful letters, case and emoji joiners."""
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    for character in ("\u200b", "\ufeff", "\u2060"):
        value = value.replace(character, "")
    value = value.replace("\u00a0", " ")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def set_caption(page, description, caption):
    expected = normalized_caption(caption)

    def committed():
        until = time.monotonic() + 1.5
        stable = None
        while time.monotonic() < until:
            if normalized_caption(description.inner_text()) == expected:
                stable = stable or time.monotonic()
                if time.monotonic() - stable >= 0.2:
                    return True
            else:
                stable = None
            page.wait_for_timeout(50)
        return False

    description.fill(caption)
    description.press("Escape")
    description.press("Tab")
    if committed():
        return
    # DraftJS/Slate editors may discard insertText of the entire string. Use real
    # per-character input events once, before any publish click, then verify again.
    description.click()
    description.press("ControlOrMeta+A")
    description.press("Backspace")
    if caption:
        description.press_sequentially(caption, delay=80)
    description.press("Escape")
    description.press("Tab")
    if not committed():
        raw = description.inner_text()
        logging.getLogger(__name__).warning(
            "TikTok caption mismatch %s",
            json.dumps(
                {
                    "expected_length": len(caption),
                    "actual_length": len(raw),
                    "normalized_length": len(normalized_caption(raw)),
                    "format_characters": sorted(
                        {f"U+{ord(c):04X}" for c in raw if unicodedata.category(c) in ("Cf", "Zs", "Cc")}
                    ),
                    "tag": description.evaluate("(e)=>e.tagName"),
                    "child_tags": description.evaluate("(e)=>[...e.children].map(x=>x.tagName).slice(0,8)"),
                }
            ),
        )
        raise BeforePublishError("Редактор TikTok не сохранил нужное описание. Публикация не нажималась.")


def upload_on_page(page, source, config, expected, *, before_post=lambda: None, timeout=180):
    """Operates on an authenticated Playwright page; injection seam is for local fixtures only."""
    clicked = False
    responses = []
    try:
        identity(page, expected)
        signature = session_signature(page.context)
        page.goto(UPLOAD, wait_until="domcontentloaded", timeout=45000)
        deadline = time.monotonic() + timeout + min(200, len(config.get("caption", "")) * 0.09)
        frame = None
        while time.monotonic() < deadline:
            if not tiktok_url(page.url) or "/login" in urlsplit(page.url).path:
                raise BeforePublishError(
                    "TikTok просит войти заново. Подключите текущую сессию Chrome.", auth=True
                )
            frame = upload_frame(page)
            if frame:
                break
            page.wait_for_timeout(500)
        if frame is None:
            raise BeforePublishError(
                "TikTok не открыл форму загрузки. Проверьте доступ к TikTok Studio и вход в Chrome.",
                auth=True,
            )
        inputs = frame.locator("input[type=file]")
        # Cover and video file inputs may coexist. Only choose a single video input.
        video_inputs = frame.locator('input[type=file]:not([accept*="image"])')
        if video_inputs.count() == 1:
            inputs = video_inputs
        if inputs.count() != 1:
            raise BeforePublishError("Не удалось однозначно найти загрузку видео в TikTok")
        inputs.set_input_files(str(source))
        description = frame.locator('[contenteditable="true"]')
        description.first.wait_for(state="visible", timeout=timeout * 1000)
        if description.count() != 1:
            raise BeforePublishError("Не удалось однозначно найти описание TikTok")
        caption = config.get("caption", "")
        set_caption(page, description, caption)
        set_privacy(frame, config.get("visibility"))
        if config.get("synthetic"):
            aigc = frame.locator('[data-e2e="aigc_container"] input[type=checkbox]')
            if aigc.count() != 1:
                raise BeforePublishError(
                    "Не удалось включить пометку ИИ-контента в TikTok. Публикация не нажималась."
                )
            aigc.check()
            if not aigc.is_checked():
                raise BeforePublishError("TikTok не подтвердил пометку ИИ-контента")
        post = frame.locator('button[data-e2e="post_video_button"]')
        post.wait_for(state="visible", timeout=timeout * 1000)
        while time.monotonic() < deadline:
            if post.is_enabled() and post.get_attribute("data-disabled") != "true":
                break
            page.wait_for_timeout(500)
        else:
            raise BeforePublishError("TikTok не завершил обработку файла. Публикация не нажималась.")
        # Abort on any session switch since checking own navigation. No user profile is reused.
        if not signature or session_signature(page.context) != signature:
            raise BeforePublishError(
                "Во время подготовки изменился аккаунт TikTok. Подключите его заново.", auth=True
            )
        if normalized_caption(description.inner_text()) != normalized_caption(caption):
            raise BeforePublishError("Описание изменилось после подготовки файла. Публикация не нажималась.")
        privacy = frame.locator('[data-e2e="video_visibility_container"] [role="combobox"]')
        if not PRIVACY[config["visibility"]].fullmatch(privacy.inner_text().strip()):
            raise BeforePublishError("Видимость изменилась после подготовки файла. Публикация не нажималась.")

        def capture(response):
            if publish_response(response):
                try:
                    if response.ok:
                        value = publication_id(response.json())
                        if value:
                            responses.append(value)
                except Exception:
                    pass

        page.on("response", capture)
        before_post()  # Durable checkpoint immediately before the only publish click.
        post_started = time.time()
        clicked = True
        post.click(timeout=10000)
        # Only the exact post-now confirmation is supported; unrelated warnings stop the flow.
        end = time.monotonic() + min(timeout, 90)
        confirmed = False
        while time.monotonic() < end and not responses:
            if tiktok_url(page.url) and urlsplit(page.url).path.startswith("/tiktokstudio/content"):
                break  # TikTok navigated to the owner's list; reconcile there instead of waiting.
            confirmation = frame.locator('[role="dialog"]').get_by_role(
                "button", name=re.compile(r"^(Post now|Publish now|Опубликовать сейчас)$", re.I)
            )
            if not confirmed and confirmation.count() == 1 and confirmation.is_visible():
                before_post()
                confirmed = True
                confirmation.click(timeout=10000)
            page.wait_for_timeout(250)
        if len(set(responses)) != 1:
            try:
                from .media import probe

                if session_signature(page.context) == signature:
                    found = studio_result(
                        page, expected, caption, probe(Path(source))["duration"], post_started
                    )
                    if found:
                        return found
            except Exception:
                pass
            raise PublicationUncertain(
                "TikTok не вернул однозначный ID. Проверьте профиль перед повтором; автоматического повтора нет."
            )
        external_id = responses[0]
        return UploadResult(external_id, f"https://www.tiktok.com/@{expected}/video/{external_id}")
    except (BeforePublishError, PublicationUncertain):
        raise
    except Exception:
        if clicked:
            raise PublicationUncertain(
                "Ответ TikTok потерян после публикации. Проверьте профиль; повторная отправка запрещена."
            ) from None
        raise BeforePublishError(
            "Не удалось подготовить публикацию TikTok. Проверьте вход, сеть и доступность TikTok Studio."
        ) from None


@contextmanager
def browser_page(token, *, headless=False):
    from playwright.sync_api import sync_playwright

    cookies = session_cookies((token or {}).get("cookies", []))
    if not has_session(cookies):
        raise BeforePublishError(
            "Сессия TikTok истекла. Подключите аккаунт через расширение Chrome.", auth=True
        )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=headless)
        try:
            origins = (token or {}).get("storage_state", {}).get("origins", [])
            origins = [o for o in origins if o.get("origin") == "https://www.tiktok.com"]
            context = browser.new_context(
                storage_state={"cookies": cookies, "origins": origins}, locale="en-US"
            )
            context.set_default_timeout(15000)
            page = context.new_page()
            page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
            # Own-profile navigation may appear after hydration.
            page.locator('[data-e2e="nav-profile"], [data-e2e="nav-profile-link"]').first.wait_for(
                timeout=15000
            )
            yield page
        finally:
            try:
                browser.close()
            except Exception:
                pass  # Cleanup cannot turn an acknowledged post into a safe retry.


def upload(token, source, config, expected, *, before_post=lambda: None):
    if not Path(source).is_file():
        raise BeforePublishError("Готовый MP4 отсутствует; повторите обработку")
    if config.get("visibility") not in PRIVACY:
        raise BeforePublishError("TikTok поддерживает «Открытый доступ» и «Только я»")
    try:
        with browser_page(token) as page:
            return upload_on_page(page, source, config, expected, before_post=before_post)
    except (BeforePublishError, PublicationUncertain):
        raise
    except Exception as error:
        for code, message in {
            "ERR_CONNECTION_RESET": "TikTok разорвал соединение. Проверьте доступность сайта в Chrome; публикация не нажималась.",
            "ERR_TUNNEL_CONNECTION_FAILED": "Chrome не смог подключиться через настроенное соединение. Публикация не нажималась.",
            "ERR_NAME_NOT_RESOLVED": "Не удалось найти адрес TikTok. Проверьте сеть; публикация не нажималась.",
            "ERR_TIMED_OUT": "TikTok не ответил вовремя. Проверьте сеть; публикация не нажималась.",
        }.items():
            if code in str(error):
                raise BeforePublishError(message) from None
        raise BeforePublishError(
            "Не удалось открыть Chrome или TikTok. Проверьте сеть и подключение аккаунта.", auth=True
        ) from None


def check_result(token, source, caption, expected, started):
    """Read-only reconciliation. This function never opens an upload form."""
    from .media import probe

    with browser_page(token) as page:
        identity(page, expected)
        return studio_result(page, expected, caption, probe(Path(source))["duration"], started)

"""Author comment after publication (idea: Repurpose.io / Upload-Post "first comment").

Job.extras["comment"] = {text, pin, status, detail, ...}. Statuses:
  pending   waits until the video is published (and public)
  posted    comment exists; pinned=True when pinning was confirmed
  pin_manual  comment exists, pin it by hand: YouTube Data API has no pin method
  skipped   nothing to do (private video, made for kids, no permission)
  failed    a single attempt failed; never retried automatically, the comment may be added by hand

Every attempt first looks for the same text from the channel itself, so a restart never doubles it.
"""

import re

from sqlalchemy import select

from .db import Account, Job, Session, Variant, now

# No \b: Playwright evaluates the pattern in JavaScript, where \b ignores Cyrillic letters.
PIN = re.compile(r"^\s*(Pin|Закрепить)(?![A-Za-zА-Яа-яЁё])", re.I)


def render_text(template, config):
    title = (config.get("title") or "").strip() or (config.get("caption") or "").split("\n")[0].strip()
    return template.replace("{название}", title).replace("{title}", title).strip()[:500]


def same(a, b):
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def youtube_post(account, video_id, text):
    from . import vault
    from .platforms import COMMENT_SCOPE, granted_scopes, youtube_service

    if COMMENT_SCOPE not in granted_scopes(vault.get(account.id)):
        return {
            "status": "skipped",
            "detail": "Нет разрешения на комментарии. Переподключите YouTube-канал и оставьте галочку "
            "«Управление комментариями».",
        }
    service = youtube_service(account)
    threads = (
        service.commentThreads()
        .list(part="snippet", videoId=video_id, searchTerms=text[:100], maxResults=50, textFormat="plainText")
        .execute()
        .get("items", [])
    )
    for thread in threads:
        snippet = thread["snippet"]["topLevelComment"]["snippet"]
        author = snippet.get("authorChannelId", {}).get("value")
        if author == account.external_id and same(snippet.get("textOriginal", ""), text):
            found = thread["snippet"]["topLevelComment"]["id"]
            break
    else:
        found = (
            service.commentThreads()
            .insert(
                part="snippet",
                body={
                    "snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}
                },
            )
            .execute()["snippet"]["topLevelComment"]["id"]
        )
    return {
        "status": "pin_manual",
        "id": found,
        # lc= opens the video with this comment on top: "⋮ → Закрепить" is two clicks.
        "pin_url": f"https://www.youtube.com/watch?v={video_id}&lc={found}",
        "detail": "Комментарий опубликован. YouTube не даёт закреплять через API: откройте ссылку, "
        "«⋮» у комментария → «Закрепить».",
    }


def tiktok_comment_items(page):
    items = page.locator('[data-e2e="comment-level-1"]')
    return items if items.count() else page.locator('[data-e2e="comment-item"]')


def tiktok_find(page, handle, text):
    items = tiktok_comment_items(page)
    for index in range(min(items.count(), 50)):
        item = items.nth(index)
        try:
            body = item.inner_text(timeout=3000)
        except Exception:
            continue
        if same(text, body) or " ".join(text.split()).casefold() in " ".join(body.split()).casefold():
            container = item.locator("xpath=ancestor-or-self::*[.//a[contains(@href, '/@')]][1]")
            links = container.locator(f'a[href*="/@{handle}"]')
            if links.count():
                return container
    return None


def tiktok_pin(page, container):
    container.hover()
    page.wait_for_timeout(500)
    more = container.locator(
        '[data-e2e="comment-more-icon"], [aria-label*="More"], [aria-label*="Ещё"], [aria-haspopup="true"]'
    )
    if not more.count():
        return False
    more.first.click(timeout=5000)
    option = page.get_by_text(PIN).first
    try:
        option.wait_for(timeout=5000)
    except Exception:
        page.keyboard.press("Escape")
        return False
    option.click(timeout=5000)
    page.wait_for_timeout(1500)
    return True


def tiktok_post(account, url, text, pin):
    from . import vault
    from .tiktok_upload import browser_page, identity

    handle = account.external_id
    with browser_page(vault.get(account.id), headless=True) as page:
        identity(page, handle)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        container = tiktok_find(page, handle, text)
        if container is None:
            box = page.locator(
                '[data-e2e="comment-input"] [contenteditable="true"], '
                '[data-e2e="comment-text"] [contenteditable="true"], div[contenteditable="true"]'
            ).first
            box.click(timeout=10000)
            page.keyboard.insert_text(text)
            post = page.locator('[data-e2e="comment-post"]').first
            post.wait_for(state="visible", timeout=10000)
            post.click(timeout=10000)
            page.wait_for_timeout(4000)
            container = tiktok_find(page, handle, text)
            if container is None:
                return {
                    "status": "failed",
                    "detail": "TikTok не показал комментарий после отправки. Проверьте ролик: возможно, "
                    "комментарий на модерации. Повтор не делается, чтобы не задвоить.",
                }
        pinned = bool(pin and tiktok_pin(page, container))
        result = {"status": "posted", "pinned": pinned, "detail": "Комментарий опубликован."}
        if pin and not pinned:
            result["detail"] += (
                " Закрепить не удалось — сделайте это в TikTok: «…» у комментария → «Закрепить»."
            )
        if pinned:
            result["detail"] = "Комментарий опубликован и закреплён."
        return result


def stale(started, minutes=30):
    from datetime import UTC, datetime, timedelta

    return not started or datetime.now(UTC) - datetime.fromisoformat(started) > timedelta(minutes=minutes)


def eligible(job, variant, account):
    """None when the comment may be posted now, otherwise (status, detail) to store or None to wait."""
    if job.status != "published" or not job.external_id:
        return "wait"
    if variant.config.get("visibility") == "private":
        return ("skipped", "Ролик приватный — комментарий не публикуется.")
    if account.platform == "youtube" and variant.config.get("made_for_kids"):
        return ("skipped", "Ролик «для детей»: YouTube отключает в нём комментарии.")
    return None


def process(posters=None, limit=1):
    """Worker job: post at most `limit` pending author comments. One attempt per comment."""
    posters = posters or {"youtube": youtube_post, "tiktok": tiktok_post}
    done = 0
    with Session() as db:
        for job in db.scalars(select(Job).where(Job.status == "published").order_by(Job.updated)).all():
            comment = (job.extras or {}).get("comment")
            if comment and comment.get("status") == "posting" and stale(comment.get("started")):
                job.extras = {
                    **job.extras,
                    "comment": {
                        **comment,
                        "status": "failed",
                        "detail": "Отправка комментария прервалась. Проверьте ролик вручную; повтора нет.",
                    },
                }
                db.commit()
                continue
            if not comment or comment.get("status") != "pending":
                continue
            variant = db.get(Variant, job.variant_id)
            account = db.get(Account, variant.account_id)
            verdict = eligible(job, variant, account)
            if verdict == "wait":
                continue
            if verdict:
                job.extras = {
                    **job.extras,
                    "comment": {**comment, "status": verdict[0], "detail": verdict[1]},
                }
                db.commit()
                continue
            if not account.connected:
                continue
            text = render_text(comment["text"], variant.config)
            # Mark the attempt first: a crash in the middle never turns into an automatic second post.
            job.extras = {**job.extras, "comment": {**comment, "status": "posting", "started": now()}}
            db.commit()
            try:
                if account.platform == "youtube":
                    result = posters["youtube"](account, job.external_id, text)
                else:
                    result = posters["tiktok"](account, job.url, text, comment.get("pin", True))
            except Exception:
                result = {
                    "status": "failed",
                    "detail": "Не удалось оставить комментарий (сеть, вход или изменился сайт). "
                    "Автоповтора нет — проверьте ролик и при необходимости добавьте комментарий вручную.",
                }
            if result["status"] == "pin_manual" and not comment.get("pin", True):
                result = {**result, "status": "posted", "detail": "Комментарий опубликован."}
            job.extras = {
                **job.extras,
                "comment": {**comment, "text_sent": text, "finished": now(), **result},
            }
            db.commit()
            done += 1
            if done >= limit:
                break
    return done

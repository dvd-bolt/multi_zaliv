"""Read-only comment monitoring. Flags require human review; never deletes remotely."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .db import Account, Comment, CommentScan, Job, Session, Variant, now


def spam_reason(text):
    value = text.casefold()
    reasons = []
    if re.search(r"(https?://|www\.|t\.me/|telegram\.(me|dog)/)", value):
        reasons.append("ссылка — проверить рекламу")
    if re.search(
        r"(у меня в профиле|смотри(?:те)? (?:в )?профил|я красивая|заработ\w*|\+\s*\d+\s*[$€₽]|\d+\s*\$.*сегодня)",
        value,
    ):
        reasons.append("возможный рекламный спам или переход в профиль")
    return "; ".join(reasons)


def youtube_comments(account, video_id):
    from .platforms import youtube_service

    service = youtube_service(account)
    rows = []
    token = None
    incomplete = False
    requests = 0
    for _ in range(5):
        reply = (
            service.commentThreads()
            .list(
                part="snippet",
                videoId=video_id,
                maxResults=100,
                order="time",
                textFormat="plainText",
                **({"pageToken": token} if token else {}),
            )
            .execute()
        )
        requests += 1
        for thread in reply.get("items", []):
            top = thread["snippet"]["topLevelComment"]
            snippet = top["snippet"]
            rows.append(
                {
                    "id": top["id"],
                    "author": snippet.get("authorDisplayName", ""),
                    "text": snippet.get("textOriginal", snippet.get("textDisplay", "")),
                }
            )
            if thread["snippet"].get("totalReplyCount", 0):
                rt = None
                for _ in range(3):
                    if requests >= 10:
                        return (
                            rows,
                            "partial",
                            "Достигнут предел 10 запросов за проверку; часть ответов ещё не прочитана.",
                        )
                    replies = (
                        service.comments()
                        .list(
                            part="snippet",
                            parentId=top["id"],
                            maxResults=100,
                            textFormat="plainText",
                            **({"pageToken": rt} if rt else {}),
                        )
                        .execute()
                    )
                    requests += 1
                    for item in replies.get("items", []):
                        s = item["snippet"]
                        rows.append(
                            {
                                "id": item["id"],
                                "author": s.get("authorDisplayName", ""),
                                "text": s.get("textOriginal", s.get("textDisplay", "")),
                            }
                        )
                    rt = replies.get("nextPageToken")
                    if not rt:
                        break
                if rt:
                    incomplete = True
        token = reply.get("nextPageToken")
        if not token:
            break
        if requests >= 10:
            return rows, "partial", "Достигнут предел запросов; комментарии прочитаны частично."
    return (
        rows,
        "partial" if token or incomplete else "ok",
        "Чтение через YouTube API; подозрения требуют проверки человеком.",
    )


def tiktok_comments(account, video_id):
    from . import vault
    from .tiktok_upload import browser_page, identity

    if not re.fullmatch(r"[0-9]{15,25}", video_id):
        raise ValueError("Нет подтверждённого ID TikTok")
    with browser_page(vault.get(account.id), headless=True) as page:
        identity(page, account.external_id)
        page.goto(
            f"https://www.tiktok.com/@{account.external_id}/video/{video_id}",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(2000)
        items = page.locator('[data-e2e="comment-item"]')
        if not items.count():
            items = page.locator('[data-e2e="comment-level-1"]')
        if not items.count():
            raise ValueError(
                "TikTok не показал комментарии. Отсутствие доступа не означает отсутствие спама."
            )
        rows = []
        for item in items.all()[:100]:
            text = item.inner_text().strip()
            if not text:
                continue
            remote = (
                item.get_attribute("data-comment-id")
                or "visible:" + hashlib.sha256(text.encode()).hexdigest()
            )
            rows.append({"id": remote, "author": "", "text": text})
        return (
            rows,
            "partial",
            "TikTok: прочитаны только видимые комментарии. Все ответы и вся история не подтверждены; проверьте комментарии и фильтры в самом TikTok.",
        )


def scan(job_id):
    with Session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise ValueError("Задание не найдено")
        variant = db.get(Variant, job.variant_id)
        account = db.get(Account, variant.account_id)
        if not job.external_id or not account.connected:
            rows, status, detail = (
                [],
                "unavailable",
                "Нужны опубликованный ролик с ID и действующее подключение аккаунта.",
            )
        else:
            try:
                fetch = youtube_comments if account.platform == "youtube" else tiktok_comments
                rows, status, detail = fetch(account, job.external_id)
            except Exception:
                rows, status, detail = (
                    [],
                    "unavailable",
                    "Не удалось прочитать комментарии. Проверьте доступы, сеть и комментарии на платформе; это не результат «спама нет».",
                )
    with Session.begin() as db:
        for row in rows:
            remote = str(row["id"])[:200]
            key = hashlib.sha256((job_id + "|" + remote).encode()).hexdigest()
            item = db.get(Comment, key)
            content = str(row["text"])[:10000]
            if not item:
                item = Comment(
                    id=key,
                    job_id=job_id,
                    remote_id=remote,
                    author=str(row.get("author", ""))[:300],
                    text=content,
                    reason=spam_reason(content),
                )
                db.add(item)
            else:
                if item.text != content:
                    item.reviewed = False
                item.text, item.reason, item.seen = content, spam_reason(content), now()
        row = db.get(CommentScan, job_id)
        if not row:
            db.add(CommentScan(job_id=job_id, status=status, detail=detail))
        else:
            row.status, row.detail, row.checked = status, detail, now()
    return result(job_id)


def result(job_id):
    with Session() as db:
        checked = db.get(CommentScan, job_id)
        rows = db.scalars(select(Comment).where(Comment.job_id == job_id).order_by(Comment.seen.desc())).all()
        return {
            "status": checked.status if checked else "never",
            "detail": checked.detail if checked else "Ещё не проверялось",
            "checked": checked.checked if checked else None,
            "flagged": sum(bool(c.reason) and not c.reviewed for c in rows),
            "comments": [
                {"id": c.id, "author": c.author, "text": c.text, "reason": c.reason, "reviewed": c.reviewed}
                for c in rows
            ],
            "remote_deletion_supported": False,
        }


def scheduled_scans():
    with Session() as db:
        jobs = db.scalars(
            select(Job).where(Job.external_id.is_not(None)).order_by(Job.created.desc()).limit(30)
        ).all()
        keys = []
        for job in jobs:
            variant = db.get(Variant, job.variant_id)
            last = db.get(CommentScan, job.id)
            if variant.config.get("buba_mode") and (
                not last or datetime.now(UTC) - datetime.fromisoformat(last.checked) > timedelta(minutes=15)
            ):
                keys.append(job.id)
    for key in keys[:2]:
        scan(key)

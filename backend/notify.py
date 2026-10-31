"""Telegram notifications through the owner's Claude bot server.

The connection code from the bot (/frameflow) is "https://<server>#<key>". It is stored in the DPAPI vault;
the bot token never leaves the server. Job.notified_status remembers what was already reported, so every
outcome is announced once, survives restarts and is retried after network errors.
"""

import json
import time
import urllib.error
import urllib.request

from sqlalchemy import or_, select

from . import vault
from .db import Account, Asset, Job, Variant

KEY = "telegram_notify"
EVENTS = ("published", "failed", "rejected", "unknown", "waiting_auth")
RETRY_AFTER = 120
_pause_until = 0.0


def parse(code):
    url, sep, secret = (code or "").strip().partition("#")
    if not sep or not url.startswith("https://") or len(secret) < 24 or " " in code.strip():
        raise ValueError("Неверный код. Получите его в Telegram-боте командой /frameflow")
    return {"url": url.rstrip("/") + "/api/ext/frameflow", "secret": secret}


def connection():
    try:
        return vault.get(KEY)
    except Exception:
        return None


def post(conn, payload, timeout=10):
    request = urllib.request.Request(
        conn["url"],
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + conn["secret"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError("Сервер не принял код. Получите новый командой /frameflow") from None
        raise


def describe(db, job):
    variant = db.get(Variant, job.variant_id)
    account = db.get(Account, variant.account_id)
    asset = db.get(Asset, variant.asset_id)
    config = variant.config or {}
    title = (config.get("title") or "").strip() or (config.get("caption") or "").split("\n")[0].strip()
    return {
        "type": job.status,
        "job": job.id,
        "platform": account.platform,
        "account": account.name,
        "account_id": account.id,
        "title": (title or (asset.name if asset else ""))[:120],
        "url": job.url,
        "error": (job.error or "")[:500],
    }


def pending(db):
    """Jobs whose current outcome was not reported yet. Jobs that left an outcome are re-armed."""
    for job in db.scalars(
        select(Job).where(Job.status.not_in(EVENTS), Job.notified_status.is_not(None))
    ).all():
        job.notified_status = None
    return db.scalars(
        select(Job)
        .where(Job.status.in_(EVENTS), or_(Job.notified_status.is_(None), Job.notified_status != Job.status))
        .order_by(Job.updated)
    ).all()


def flush(db, send=post, clock=time.monotonic):
    """Report new outcomes. Without a configured connection they are marked silently (no backlog later)."""
    global _pause_until
    jobs = pending(db)
    if not jobs:
        db.commit()
        return 0
    conn = connection()
    if not conn:
        for job in jobs:
            job.notified_status = job.status
        db.commit()
        return 0
    if clock() < _pause_until:
        db.commit()
        return 0
    events, auth = [], {}
    for job in jobs:
        event = describe(db, job)
        if job.status == "waiting_auth":
            # One "sign in again" message per account, however many jobs wait.
            group = auth.setdefault(event["account_id"], {**event, "jobs": []})
            group["jobs"].append(job)
        else:
            events.append(({**event}, [job]))
    events += [
        ({k: v for k, v in group.items() if k != "jobs"} | {"count": len(group["jobs"])}, group["jobs"])
        for group in auth.values()
    ]
    sent = 0
    for event, owners in events:
        try:
            ok = send(conn, {"event": event})
        except Exception:
            ok = False
        if not ok:
            _pause_until = clock() + RETRY_AFTER
            break
        for job in owners:
            job.notified_status = job.status
        sent += 1
    db.commit()
    return sent

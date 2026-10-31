"""BUBA payouts: one claim per video. The owner picks which publication (TikTok or YouTube) is claimed.

Thresholds come from the BUBA rules page (10 000 views, claim within 7 calendar days, one claim per video).
This is local bookkeeping: claims are submitted in the BUBA bot by the owner.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import Account, Asset, BubaSubmission, Job, Metric, Setting, Variant, now

THRESHOLD = 10_000
MSK = ZoneInfo("Europe/Moscow")
FINAL = ("submitted", "paid", "rejected")


def published_at(job):
    return job.upload_started_at or job.updated


def deadline(published):
    day = datetime.fromisoformat(published).astimezone(MSK).date() + timedelta(days=7)
    return datetime.combine(day, datetime.min.time(), tzinfo=MSK)


def latest_views(db, job_id):
    for metric in db.scalars(select(Metric).where(Metric.job_id == job_id).order_by(Metric.captured.desc())):
        value = (metric.values or {}).get("views")
        if isinstance(value, (int, float)):
            return int(value), metric.captured
    return None, None


def overview(db, clock=None):
    """Videos made in BUBA mode with their publications, the chosen claim and its progress."""
    current = clock or datetime.now(UTC)
    rows = {}
    jobs = db.scalars(select(Job).where(Job.status == "published", Job.external_id.is_not(None))).all()
    for job in jobs:
        variant = db.get(Variant, job.variant_id)
        if not (variant.config or {}).get("buba_mode"):
            continue
        asset = db.get(Asset, variant.asset_id)
        account = db.get(Account, variant.account_id)
        views, captured = latest_views(db, job.id)
        record = db.get(BubaSubmission, job.id)
        row = rows.setdefault(
            asset.id,
            {
                "asset_id": asset.id,
                "name": asset.name,
                "title": variant.config.get("title") or "",
                "publications": [],
            },
        )
        row["publications"].append(
            {
                "job_id": job.id,
                "platform": account.platform,
                "account": account.name,
                "url": job.url,
                "views": views,
                "captured": captured,
                "published_at": published_at(job),
                "state": record.state if record else None,
            }
        )
    result = []
    for row in rows.values():
        pubs = sorted(row["publications"], key=lambda p: -(p["views"] or 0))
        chosen = next((p for p in pubs if p["state"]), None)
        best = pubs[0] if pubs and pubs[0]["views"] is not None else None
        target = chosen or best
        due = deadline(target["published_at"]) if target else None
        views = target["views"] if target else None
        state = (chosen or {}).get("state") or "choose"
        if state in FINAL:
            phase = state
        elif due and current > due:
            phase = "expired"
        elif views is not None and views >= THRESHOLD:
            phase = "ready"
        else:
            phase = "collecting"
        result.append(
            {
                **row,
                "publications": pubs,
                "chosen": chosen["job_id"] if chosen else None,
                "recommended": best["job_id"] if best else None,
                "deadline": due.isoformat() if due else None,
                "progress": min(1.0, (views or 0) / THRESHOLD),
                "phase": phase,
            }
        )
    order = {"ready": 0, "collecting": 1, "submitted": 2, "expired": 3, "paid": 4, "rejected": 5}
    return sorted(result, key=lambda r: (order.get(r["phase"], 9), r["deadline"] or ""))


def choose(db, asset_id, job_id):
    """Make this publication the claimed one; other unsubmitted choices of the same video are dropped."""
    job = db.get(Job, job_id)
    variant = db.get(Variant, job.variant_id) if job else None
    if not job or variant.asset_id != asset_id or job.status != "published" or not job.external_id:
        raise ValueError("Выберите опубликованный ролик этого исходника")
    if (variant.config or {}).get("visibility") != "public":
        raise ValueError("Для выплаты ролик должен быть публичным")
    siblings = db.scalars(select(Variant.id).where(Variant.asset_id == asset_id)).all()
    others = db.scalars(select(Job).where(Job.variant_id.in_(siblings), Job.id != job_id)).all()
    for other in others:
        record = db.get(BubaSubmission, other.id)
        if record and record.state in FINAL:
            raise ValueError("Заявка по этому ролику уже подана с другой площадки")
        if record:
            db.delete(record)
    record = db.get(BubaSubmission, job_id)
    if not record:
        db.add(
            BubaSubmission(job_id=job_id, source_sha=None, published_at=published_at(job), state="tracking")
        )
    record = record or db.get(BubaSubmission, job_id)
    return record


def mark(db, job_id, state):
    record = db.get(BubaSubmission, job_id)
    if not record:
        raise ValueError("Сначала выберите площадку для выплаты")
    allowed = {"tracking": ("submitted",), "submitted": ("paid", "rejected")}
    if state not in allowed.get(record.state, ()):
        raise ValueError("Так изменить состояние заявки нельзя")
    if state == "submitted":
        job = db.get(Job, job_id)
        views, _ = latest_views(db, job_id)
        if views is None or views < THRESHOLD:
            raise ValueError("Нужно не меньше 10 000 просмотров")
        if datetime.now(UTC) > deadline(record.published_at):
            raise ValueError("Срок подачи (7 дней) уже прошёл")
        variant = db.get(Variant, job.variant_id)
        record.source_sha = db.get(Asset, variant.asset_id).sha256
    record.state, record.updated = state, now()
    return record


def events(db, clock=None):
    """Telegram reminders: 'ready' once when the claim reaches 10 000, 'deadline' once on the last day."""
    current = clock or datetime.now(UTC)
    store = db.get(Setting, "payout_notified")
    sent = dict(store.value) if store else {}
    found = []
    for row in overview(db, current):
        job = row["chosen"]
        if not job or row["phase"] != "ready":
            continue
        pub = next(p for p in row["publications"] if p["job_id"] == job)
        base = {
            "platform": pub["platform"],
            "account": pub["account"],
            "title": row["title"] or row["name"],
            "url": pub["url"],
            "views": pub["views"],
            "deadline": row["deadline"],
        }
        if "ready" not in sent.get(job, []):
            found.append((job, "ready", {"type": "payout_ready", **base}))
        last_day = datetime.fromisoformat(row["deadline"]) - timedelta(days=1)
        if current >= last_day and "deadline" not in sent.get(job, []):
            found.append((job, "deadline", {"type": "payout_deadline", **base}))
    return found


def remember(db, job, kind):
    store = db.get(Setting, "payout_notified")
    if not store:
        store = Setting(key="payout_notified", value={})
        db.add(store)
    value = dict(store.value)
    value[job] = sorted(set(value.get(job, [])) | {kind})
    store.value = value

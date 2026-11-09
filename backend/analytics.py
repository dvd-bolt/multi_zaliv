"""Observed age milestones, never inferred from upload start or an old distant snapshot."""

import math
from datetime import UTC, datetime, timedelta
from statistics import mean, median

from sqlalchemy import select

from .db import Account, ChannelVideo, Job, Metric, PublicationStats, Variant, now

WINDOWS = {"hour": (3600, 600), "day": (86400, 3600)}
COUNTERS = ("views", "likes", "comments")


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def counter(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def record(db, account_id, external_id, published_at, publication_source, observations):
    key = (account_id, external_id)
    row = db.get(PublicationStats, key)
    if row is None:
        row = PublicationStats(account_id=account_id, external_id=external_id, milestones={}, latest={})
        db.add(row)
        db.flush()
    published = timestamp(published_at)
    if published and published <= datetime.now(UTC):
        normalized = published.isoformat()
        if row.published_at != normalized:
            row.published_at, row.publication_source, row.milestones = normalized, publication_source, {}
    published = timestamp(row.published_at)
    milestones = dict(row.milestones)
    for observation in observations:
        captured = timestamp(observation.get("captured"))
        if not captured or captured > datetime.now(UTC):
            continue
        sample = {
            "captured": captured.isoformat(),
            "values": {k: counter(observation.get("values", {}).get(k)) for k in COUNTERS},
            "source": observation.get("source", "Неизвестный источник"),
        }
        previous = timestamp(row.latest.get("captured"))
        if not previous or captured >= previous:
            row.latest = sample
        if not published or captured < published:
            continue
        age = (captured - published).total_seconds()
        for name, (target, tolerance) in WINDOWS.items():
            offset = age - target
            if abs(offset) > tolerance:
                continue
            old = milestones.get(name)
            if old and abs(old["offset_seconds"]) <= abs(offset):
                continue
            milestones[name] = {
                **sample,
                "offset_seconds": offset,
                "quality": "exact" if offset == 0 else "approximate",
            }
    row.milestones = milestones
    return row


def backfill(db):
    """Only platform timestamps in known library metadata; source jobs are untouched."""
    linked = {}
    for job, variant in db.execute(select(Job, Variant).join(Variant)).all():
        if job.external_id:
            linked[(variant.account_id, job.external_id)] = job.id
    for video in db.scalars(select(ChannelVideo)).all():
        confirmed = video.details.get("visibility") in ("public", "unlisted")
        observations = [{**m, "source": "YouTube Data API"} for m in video.history]
        job_id = linked.get((video.account_id, video.external_id))
        if job_id:
            observations += [
                {"captured": m.captured, "values": m.values, "source": m.source}
                for m in db.scalars(select(Metric).where(Metric.job_id == job_id)).all()
            ]
        record(
            db, video.account_id, video.external_id,
            video.details.get("published_at") if confirmed else None,
            "YouTube snippet.publishedAt" if confirmed else None, observations,
        )
    for key, job_id in linked.items():
        observations = [
            {"captured": m.captured, "values": m.values, "source": m.source}
            for m in db.scalars(select(Metric).where(Metric.job_id == job_id)).all()
        ]
        record(db, *key, None, None, observations)


def video_rows(db):
    backfill(db)
    db.flush()
    accounts = {a.id: a for a in db.scalars(select(Account)).all()}
    profiles = {(r.account_id, r.external_id): r for r in db.scalars(select(PublicationStats)).all()}
    rows = {}
    for job, variant in db.execute(select(Job, Variant).join(Variant)).all():
        if job.external_id and job.status != "deleted":
            rows[(variant.account_id, job.external_id)] = {
                "id": job.id, "title": variant.config.get("title") or job.external_id,
                "url": job.url, "format": variant.config.get("content_format", "unknown"),
            }
    for v in db.scalars(select(ChannelVideo)).all():
        key = (v.account_id, v.external_id)
        if not v.present:
            rows.pop(key, None)
        elif key not in rows:
            rows[key] = {"id": f"channel:{v.account_id}:{v.external_id}",
                         "title": v.details.get("title", v.external_id),
                         "url": "https://www.youtube.com/watch?v=" + v.external_id, "format": "unknown"}
    result = []
    for key, item in rows.items():
        a, p = accounts.get(key[0]), profiles.get(key)
        if a is None or p is None:
            continue
        published = timestamp(p.published_at)
        milestones = {
            name: sample if published and datetime.now(UTC) >= published + timedelta(seconds=target) else None
            for name, (target, _) in WINDOWS.items()
            if (sample := p.milestones.get(name))
        }
        result.append({**item, "account_id": a.id, "account_name": a.name, "platform": a.platform,
                       "published_at": p.published_at, "publication_source": p.publication_source,
                       "hour": milestones.get("hour"), "day": milestones.get("day"), "current": p.latest})
    return result


def engagement(values):
    v, likes, comments = (counter(values.get(k)) for k in COUNTERS)
    return 100 * (likes + comments) / v if v and likes is not None and comments is not None else None


def report(db, platform, account_id=None, days=30, content_format="all"):
    all_rows = [r for r in video_rows(db) if r["platform"] == platform
                and (not account_id or r["account_id"] == account_id)
                and (content_format == "all" or r["format"] == content_format)]
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = [r for r in all_rows if not days or
            (timestamp(r["published_at"]) is not None and timestamp(r["published_at"]) >= cutoff)]
    cells = {}
    for stage in ("hour", "day", "current"):
        cells[stage] = {}
        for metric in (*COUNTERS, "er"):
            samples = []
            for row in rows:
                sample = row.get(stage)
                if sample:
                    v = engagement(sample["values"]) if metric == "er" else counter(sample["values"].get(metric))
                    if v is not None:
                        samples.append((v, sample))
            values = [s[0] for s in samples]
            cells[stage][metric] = {"mean": mean(values) if values else None,
                                    "median": median(values) if values else None, "n": len(values),
                                    "approximate": sum(s.get("quality") == "approximate" for _, s in samples)}
    return {"rows": rows, "cells": cells, "count": len(rows),
            "unknown_publication_time": sum(not r["published_at"] for r in all_rows),
            "generated_at": now(), "tolerances": {"hour": 600, "day": 3600}}


def publication_info(db, account_id, external_id):
    p = db.get(PublicationStats, (account_id, external_id)) if external_id else None
    return {"published_at": p.published_at if p else None,
            "publication_source": p.publication_source if p else None,
            "milestones": p.milestones if p else {}}


def due_accounts(db):
    """Targeted 5-minute polling only near milestones; no invented sample after sleep."""
    result = set()
    for p in db.scalars(select(PublicationStats)).all():
        pub = timestamp(p.published_at)
        if not pub:
            continue
        age = (datetime.now(UTC) - pub).total_seconds()
        for name, (target, tolerance) in WINDOWS.items():
            sample = p.milestones.get(name)
            if target - tolerance <= age <= target + tolerance:
                if not sample or abs(sample["offset_seconds"]) > 300:
                    last = timestamp(p.latest.get("captured"))
                    if not last or (datetime.now(UTC) - last).total_seconds() >= 240:
                        result.add(p.account_id)
    return result

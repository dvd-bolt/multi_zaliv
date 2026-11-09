"""Current statistics for the owner's server. No backlog, credentials or local paths."""

import json
import logging
import math
import subprocess
import tomllib
import urllib.error
import urllib.request
from functools import lru_cache

from sqlalchemy import func, select

from . import notify, payouts
from .config import ROOT
from .db import Account, Asset, BubaSubmission, Job, Metric, Setting, Variant, now

logger = logging.getLogger(__name__)
KEY = "stats_push"
MAX_BYTES = 1_000_000
COUNTERS = ("views", "likes", "comments", "shares")


class SnapshotTooLarge(ValueError):
    pass


@lru_cache(maxsize=1)
def build_version():
    manifest = ROOT / "frameflow-build.json"
    if manifest.is_file():
        try:
            release = json.loads(manifest.read_text(encoding="utf-8"))
            return release["version"] + "+" + release["source_sha256"][:12]
        except (OSError, ValueError, KeyError):
            pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        try:
            return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        except (OSError, ValueError, KeyError):
            return None


def status(db, connected=None):
    store = db.get(Setting, KEY)
    value = store.value if store else {}
    if connected is None:
        connected = bool(notify.connection())
    # Explicit opt-out survives disconnects, restarts and reconnects.
    return {
        "enabled": bool(connected and value.get("enabled", True)),
        **{key: value.get(key) for key in ("attempted_at", "last_success", "result", "http_status")},
    }


def configure(db, enabled):
    store = db.get(Setting, KEY)
    if not store:
        store = Setting(key=KEY, value={})
        db.add(store)
    store.value = {**store.value, "enabled": enabled}


def encode(snapshot):
    return json.dumps(
        {"snapshot": snapshot}, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def counters(metric):
    values = metric.values or {}
    return {
        key: int(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        for key in COUNTERS
        for value in [values.get(key)]
    }


def build(db):
    """Whitelist public fields. History is chronological, with at most 48 newest samples."""
    refresh = db.get(Setting, "metrics_refresh")
    snapshot = {
        "version": 1,
        "generated": now(),
        "app": {"product": "FrameFlow", "build": build_version()},
        "videos": [],
        "accounts": [
            {
                "id": a.id,
                "platform": a.platform,
                "name": a.name,
                "status": "connected" if a.connected else "waiting_auth",
            }
            for a in db.scalars(select(Account).order_by(Account.id))
        ],
        "queue": {key: 0 for key in ("scheduled", "failed", "unknown", "waiting_auth")},
        "refresh": {
            key: (refresh.value if refresh else {}).get(key, 0) for key in ("updated", "unavailable")
        },
    }
    for state, count in db.execute(select(Job.status, func.count()).group_by(Job.status)):
        if state in snapshot["queue"]:
            snapshot["queue"][state] = count
    # Content calendar on the server: what is still waiting for publication (time, channel, title only).
    snapshot["upcoming"] = [
        {"time": job.scheduled, "status": job.status, "platform": account.platform, "account": account.name,
         "title": str(notify.describe(db, job)["title"] or "")[:120]}
        for job, account in db.execute(
            select(Job, Account)
            .join(Variant, Job.variant_id == Variant.id)
            .join(Account, Variant.account_id == Account.id)
            .where(Job.status.in_(("queued", "rendering", "ready", "scheduled", "waiting_auth", "uploading")),
                   Job.scheduled.is_not(None))
            .order_by(Job.scheduled)
            .limit(100)
        )
    ]
    payout_rows = {row["asset_id"]: row for row in payouts.overview(db)}
    for job, variant, account, asset in db.execute(
        select(Job, Variant, Account, Asset)
        .join(Variant, Job.variant_id == Variant.id)
        .join(Account, Variant.account_id == Account.id)
        .join(Asset, Variant.asset_id == Asset.id)
        .where(Job.status == "published", Job.external_id.is_not(None), Job.external_id != "")
        .order_by(Job.id)
    ):
        history = db.scalars(
            select(Metric)
            .where(Metric.job_id == job.id)
            .order_by(Metric.captured.desc(), Metric.id.desc())
            .limit(48)
        ).all()
        latest = history[0] if history else None
        video = {
            "job_id": job.id,
            "platform": account.platform,
            "account": account.name,
            "account_id": account.id,
            "asset": asset.name,
            "title": notify.describe(db, job)["title"],
            "url": job.url,
            "external_id": job.external_id,
            "published_at": payouts.published_at(job),
            "latest": {"captured": latest.captured, "source": latest.source, **counters(latest)}
            if latest
            else None,
            "history": [{"captured": m.captured, **counters(m)} for m in reversed(history)],
        }
        if (variant.config or {}).get("buba_mode"):
            row = payout_rows[asset.id]
            record = db.get(BubaSubmission, job.id)
            views, _ = payouts.latest_views(db, job.id)
            video["payout"] = {
                "threshold": payouts.THRESHOLD,
                "deadline": payouts.deadline(payouts.published_at(job)).isoformat(),
                "state": record.state if record else "untracked",
                "views": views,
                "phase": row["phase"],
                "chosen": row["chosen"],
                "recommended": row["recommended"],
            }
        snapshot["videos"].append(video)
    while len(encode(snapshot)) >= MAX_BYTES:
        histories = [v["history"] for v in snapshot["videos"] if v["history"]]
        if not histories:
            # Never drop videos or send an oversized request if metadata alone exceeds the limit.
            raise SnapshotTooLarge("Statistics snapshot exceeds size limit")
        for history in histories:
            del history[: (len(history) + 1) // 2]
    return snapshot


def post(conn, payload, timeout=20):
    request = urllib.request.Request(
        conn["url"].rstrip("/") + "/stats",
        data=encode(payload["snapshot"]),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + conn["secret"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def push(db, send=None):
    conn = notify.connection()
    if not conn or not status(db, connected=True)["enabled"]:
        return None
    attempted = now()
    code = None
    try:
        snapshot = build(db)
    except SnapshotTooLarge:
        result = "too_large"
    else:
        # End the read transaction before waiting on the network.
        db.commit()
        try:
            code = (send or post)(conn, {"snapshot": snapshot}, timeout=20)
            result = (
                "ok"
                if code == 200
                else "auth_error"
                if code in (401, 403)
                else "not_ready"
                if code == 404
                else "server_error"
            )
        except Exception:
            # Remote responses and exception strings may contain secrets; do not log them.
            result = "network_error"
    store = db.get(Setting, KEY)
    if not store:
        store = Setting(key=KEY, value={})
        db.add(store)
    previous = store.value
    if result == "auth_error" and previous.get("result") != "auth_error":
        logger.warning("Статистика: сервер не принял код")
    store.value = {
        **previous,
        "attempted_at": attempted,
        "result": result,
        "http_status": code,
        "last_success": now() if result == "ok" else previous.get("last_success"),
    }
    db.commit()
    return status(db, connected=True)


def push_safely(db):
    try:
        return push(db)
    except Exception:
        db.rollback()
        logger.warning("Не удалось отправить снимок статистики; метрики сохранены")
        return None

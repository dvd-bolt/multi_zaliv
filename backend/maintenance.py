"""Local maintenance; original inputs are never deleted."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from .config import DATA
from .db import Job, Session, Variant, uid


def backup_database():
    name = "survivalflow-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uid()[:4] + ".sqlite3"
    path = DATA / "backups" / name
    with sqlite3.connect(DATA / "survivalflow.sqlite3") as source, sqlite3.connect(path) as target:
        source.backup(target)
    return path


def cleanup_exports(days):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    deleted = 0
    with Session.begin() as db:
        for job in db.scalars(select(Job).where(Job.status.in_(["published", "cancelled"]))).all():
            if datetime.fromisoformat(job.updated) >= cutoff:
                continue
            variant = db.get(Variant, job.variant_id)
            if not variant.output:
                continue
            path = Path(variant.output).resolve()
            # Only our own flat exports directory is eligible, even if database contents are tampered with.
            if path.parent != (DATA / "exports").resolve():
                continue
            if path.exists():
                path.unlink()
                deleted += 1
            variant.output = None
    return {"deleted": deleted}

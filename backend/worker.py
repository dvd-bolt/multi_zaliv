"""Independent, single-consumer durable worker. Run with python -m backend.worker."""

import atexit
import json
import logging
import msvcrt
import os
import threading
import time
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import case, select, update

from .config import DATA
from .db import Asset, Job, Session, Variant, now, settings
from .media import builtin_banner, render
from .queue import recover

HEARTBEAT_LOCK = threading.Lock()
RECOVERY_NEEDED = threading.Event()


def metrics():
    from . import stats_push
    from .platforms import refresh_metrics

    with Session.begin() as db:
        result = refresh_metrics(db)
    with Session() as db:
        try:
            stats_push.push(db)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).warning("Не удалось отправить снимок статистики; метрики сохранены")
    return result


def heartbeat():
    with HEARTBEAT_LOCK:
        try:
            marker = DATA / "worker.heartbeat"
            if marker.exists() and time.time() - marker.stat().st_mtime > 600:
                RECOVERY_NEEDED.set()
            # Use mtime: Windows readers/antivirus can briefly prevent replacing an open JSON file.
            # A diagnostic heartbeat must never turn a successful render into a failed job.
            (DATA / "worker.heartbeat").touch()
        except OSError:
            pass


def milestone_metrics():
    from .analytics import due_accounts
    from .platforms import refresh_metrics

    with Session.begin() as db:
        due = due_accounts(db)
        # Newly uploaded jobs need a first platform timestamp before their first hour.
        for job, variant in db.execute(select(Job, Variant).join(Variant)).all():
            if job.external_id and job.status in ("published", "platform_processing"):
                from datetime import UTC, datetime

                from .analytics import publication_info, timestamp

                uploaded = timestamp(job.upload_started_at)
                if uploaded and 0 <= (datetime.now(UTC) - uploaded).total_seconds() <= 4200:
                    if not publication_info(db, variant.account_id, job.external_id)["published_at"]:
                        due.add(variant.account_id)
        if due:
            refresh_metrics(db, account_ids=due)


def tick():
    heartbeat()
    with Session() as db:
        options = settings(db)
        if options["paused"]:
            return
        if RECOVERY_NEEDED.is_set():
            recover(db)
            db.commit()
            RECOVERY_NEEDED.clear()
        # Due publications take precedence over the next render in a large batch.
        from .platforms import dispatch_one

        dispatch_one(db)
        db.commit()
        try:
            from .releases import tick as releases_tick

            releases_tick()  # Continue a batch once its first TikTok publication is confirmed.
        except Exception:
            logging.getLogger(__name__).exception("Не удалось продолжить выпуск")
        try:
            from .notify import flush

            flush(db)
        except Exception:
            db.rollback()  # A notification problem must never stop publishing.
        from .stories import process_one

        if process_one():
            return
        from .clips import process_one as process_clip

        if process_clip():
            return
        job = db.scalar(select(Job).where(Job.status == "queued").order_by(Job.created))
        if not job:
            return
        job_id = job.id
        changed = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="rendering", attempts=Job.attempts + 1, updated=now())
        )
        if not changed.rowcount:
            return
        variant = db.get(Variant, job.variant_id)
        source = db.get(Asset, variant.asset_id)
        banner = db.get(Asset, variant.banner_id) if variant.banner_id else None
        source_path, length, config = Path(source.path), source.media["duration"], variant.config.copy()
        banner_path = Path(banner.path) if banner else DATA / "temporary" / f"{job_id}.png"
        banner_media = banner.media if banner else {"image": True}
        output = DATA / "exports" / f"{variant.id}.mp4"
        db.commit()

    def cancelled():
        heartbeat()
        with Session() as db:
            return db.get(Job, job_id).status == "cancelled"

    def progress(value):
        with Session.begin() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "rendering")
                .values(progress=value, updated=now())
            )

    try:
        if not banner:
            builtin_banner(config["banner_text"], banner_path)
        render(
            source_path,
            banner_path,
            banner_media,
            output,
            config,
            length,
            progress,
            cancelled,
            options["encoder"],
        )
        with Session.begin() as db:
            # Compare and update in the same statement. A SELECT followed by an ORM
            # flush can overwrite an acknowledged cancellation from another session.
            completed = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "rendering")
                .values(
                    status=case((Job.approved.is_(True), "scheduled"), else_="ready"),
                    progress=100,
                    error=None,
                    updated=now(),
                )
            ).rowcount
            if completed:
                db.get(Variant, variant.id).output = str(output)
        if not completed:
            output.unlink(missing_ok=True)
    except InterruptedError:
        pass
    except Exception as error:
        with Session.begin() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "rendering")
                .values(status="failed", error=str(error)[-1600:], updated=now())
            )
    finally:
        if not banner:
            banner_path.unlink(missing_ok=True)


def main():
    lock = (DATA / "worker.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("Рабочий процесс уже запущен") from None
    atexit.register(lock.close)
    (DATA / "worker.pid.json").write_text(json.dumps({"pid": os.getpid(), "recorded": now()}))
    with Session.begin() as db:
        recover(db)
    from .stories import recover as recover_stories

    recover_stories()
    from .clips import recover as recover_clips

    recover_clips()

    def pulse():
        while True:
            heartbeat()
            time.sleep(3)

    from .maintenance import backup_database

    backup_database()
    threading.Thread(target=pulse, daemon=True).start()
    scheduler = BlockingScheduler(
        timezone="UTC", executors={"default": ThreadPoolExecutor(1), "comments": ThreadPoolExecutor(1)}
    )
    from .comments import scheduled_scans

    scheduler.add_job(
        scheduled_scans, "interval", minutes=15, executor="comments", max_instances=1, coalesce=True
    )
    from .author_comment import process as author_comments
    from .preflight import scheduled_checks

    scheduler.add_job(
        author_comments, "interval", minutes=3, executor="comments", max_instances=1, coalesce=True
    )

    scheduler.add_job(
        scheduled_checks, "interval", minutes=5, executor="comments", max_instances=1, coalesce=True
    )
    scheduler.add_job(tick, "interval", seconds=2, max_instances=1, coalesce=True)
    scheduler.add_job(metrics, "interval", hours=1, max_instances=1, coalesce=True)
    scheduler.add_job(milestone_metrics, "interval", minutes=5, max_instances=1, coalesce=True)
    scheduler.add_job(backup_database, "interval", hours=24, max_instances=1, coalesce=True)
    scheduler.start()


if __name__ == "__main__":
    main()

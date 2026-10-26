import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from .db import Account, Asset, Job, Variant, now, settings
from .media import variation
from .schemas import Edit


def enqueue(db, request):
    scheduled = None
    if request.scheduled:
        date = datetime.fromisoformat(request.scheduled)
        if date.tzinfo is None or date <= datetime.now(UTC):
            raise ValueError("Укажите будущее время с часовым поясом")
        scheduled = date.astimezone(UTC).isoformat()
    accounts = [db.get(Account, key) for key in dict.fromkeys(request.account_ids)]
    if any(a is None or not a.enabled for a in accounts):
        raise ValueError("Выбранный аккаунт недоступен")
    if request.auto_publish:
        for account in accounts:
            if not account.connected or not account.external_id:
                raise ValueError(f"Подключите {account.name} перед автозаливом")
            if account.platform == "tiktok" and account.method != "browser_verified":
                raise ValueError(f"{account.name}: сначала завершите первую проверочную отправку TikTok")
    accounts.sort(key=lambda a: a.id)
    assets = [db.get(Asset, key) for key in dict.fromkeys(request.asset_ids)]
    if any(a is None or a.kind != "video" or a.archived for a in assets):
        raise ValueError("Исходник не найден")
    created, existing, activated = [], [], []
    for asset in assets:
        if request.auto_publish and request.buba_confirmed and asset.edit.get("buba_mode"):
            from .buba import REVIEW

            asset.edit = {**asset.edit, "buba_review": {key: True for key in REVIEW}}
        config = Edit.model_validate(asset.edit).effective(asset.media["duration"])
        banner = db.get(Asset, config["banner_id"]) if config["banner_id"] else None
        if config["banner_id"] and (banner is None or banner.kind != "banner"):
            raise ValueError("Баннер не найден")
        if request.auto_publish:
            from .buba import require_config_ready

            require_config_ready(db, asset.media, banner.media if banner else None, config)
            if config["visibility"] == "unlisted" and any(a.platform == "tiktok" for a in accounts):
                raise ValueError("Для TikTok выберите «Открытый доступ» или «Только я»")
        for slot, account in enumerate(accounts):
            variant_config = variation(config, account.id, slot, len(accounts))
            variant_config["quality"] = settings(db)["quality"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    [asset.sha256, banner.sha256 if banner else None, account.id, variant_config],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            variant = db.scalar(select(Variant).where(Variant.fingerprint == fingerprint))
            if variant:
                job = db.scalar(select(Job).where(Job.variant_id == variant.id))
                existing.append(job.id)
                if (
                    request.auto_publish
                    and not job.external_id
                    and not job.upload_started_at
                    and job.status in ("queued", "rendering", "ready", "waiting_auth", "missed", "scheduled")
                ):
                    # The queue click explicitly authorizes this same immutable variant.
                    # Never revive a possibly sent, cancelled or failed publication here.
                    if not job.approved or not job.auto_publish or job.scheduled != scheduled:
                        job.approved, job.scheduled, job.error = True, scheduled, None
                        job.auto_publish = True
                        if job.status not in ("queued", "rendering"):
                            job.status = (
                                "scheduled" if variant.output and Path(variant.output).is_file() else "queued"
                            )
                        job.updated = now()
                        activated.append(job.id)
                continue
            variant = Variant(
                asset_id=asset.id,
                account_id=account.id,
                banner_id=config["banner_id"],
                fingerprint=fingerprint,
                config=variant_config,
            )
            db.add(variant)
            db.flush()
            job = Job(
                variant_id=variant.id,
                scheduled=scheduled,
                approved=request.auto_publish,
                auto_publish=request.auto_publish,
            )
            db.add(job)
            db.flush()
            created.append(job.id)
    if request.auto_publish:
        from .db import Setting

        db.get(Setting, "application").value = {**settings(db), "paused": False}
    return {"created": created, "existing": existing, "activated": activated}


def recover(db):
    db.execute(
        update(Job).where(Job.status == "rendering").values(status="queued", progress=0, updated=now())
    )
    db.execute(
        update(Job)
        .where(Job.status == "uploading")
        .values(
            status="unknown",
            updated=now(),
            error="Рабочий процесс прерван во время отправки. Сначала проверьте результат на платформе.",
        )
    )
    db.execute(
        update(Job)
        .where(
            Job.auto_publish.is_(True),
            Job.approved.is_(True),
            Job.status.in_(["queued", "scheduled", "waiting_auth", "ready"]),
            Job.scheduled.is_not(None),
            Job.scheduled < (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        )
        .values(
            status="missed",
            error="Время прошло, пока программа была выключена или компьютер спал. Выберите новое время.",
            updated=now(),
        )
    )


def action(db, job, request):
    if request.action == "cancel":
        if job.status not in (
            "queued",
            "rendering",
            "ready",
            "waiting_auth",
            "scheduled",
            "missed",
            "failed",
            "cancelled",
        ):
            raise ValueError("Отправку уже нельзя безопасно отменить")
        job.status = "cancelled"
        job.approved = False
    elif request.action == "retry":
        if job.status not in ("failed", "cancelled") or job.external_id:
            raise ValueError("Повтор разрешён только после ошибки обработки или отмены")
        keep_auto = job.auto_publish and job.approved and job.status == "failed" and not job.upload_started_at
        job.status, job.progress, job.error, job.approved = "queued", 0, None, bool(keep_auto)
    elif request.action in ("approve", "resume", "test_upload"):
        if job.status not in ("ready", "waiting_auth", "missed", "scheduled"):
            raise ValueError("Сначала дождитесь готового видео")
        from .buba import require_ready

        require_ready(db, job)
        account = db.get(Account, db.get(Variant, job.variant_id).account_id)
        variant = db.get(Variant, job.variant_id)
        if request.action == "test_upload":
            if (
                account.platform != "tiktok"
                or not account.connected
                or not account.enabled
                or account.method not in ("browser_login", "browser_verified")
            ):
                raise ValueError("Сначала подключите и подтвердите TikTok-аккаунт")
            if job.upload_started_at or job.external_id:
                raise ValueError(
                    "Ранее уже выполнялась отправка; сначала проверьте результат, повтор запрещён"
                )
            if not variant.output or not Path(variant.output).is_file():
                raise ValueError("Сначала дождитесь готового MP4")
            if variant.config.get("visibility") not in ("public", "private"):
                raise ValueError("TikTok поддерживает только открытый доступ или «Только я»")
            job.validation_upload, job.scheduled = True, None
        elif account.platform == "tiktok" and account.method != "browser_verified":
            raise ValueError(
                "Вход в TikTok сохранён. Автопубликация ещё не подтверждена; готовый MP4 можно скачать."
            )
        job.approved = True
        job.error = None
        if request.action == "resume":
            job.scheduled = None
        job.status = "scheduled" if account.connected else "waiting_auth"
        if (
            request.action == "resume"
            and job.auto_publish
            and (not variant.output or not Path(variant.output).is_file())
        ):
            job.status = "queued"
    elif request.action == "reconcile":
        if job.status != "unknown" or not request.external_id:
            raise ValueError("Укажите ID ролика для задания с неизвестным результатом")
        variant = db.get(Variant, job.variant_id)
        account = db.get(Account, variant.account_id)
        if account.platform != "youtube":
            raise ValueError("Сверка TikTok требует отдельного проверенного доступа к API")
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", request.external_id):
            raise ValueError("Укажите 11-символьный ID YouTube-видео")
        from .platforms import youtube_service

        try:
            items = (
                youtube_service(account)
                .videos()
                .list(part="snippet,status", id=request.external_id)
                .execute()
                .get("items", [])
            )
        except Exception as error:
            raise ValueError("Не удалось сверить ролик через API. Проверьте подключение канала.") from error
        if len(items) != 1 or items[0]["snippet"]["channelId"] != account.external_id:
            raise ValueError("Ролик не найден в выбранном канале")
        if items[0]["snippet"]["title"] != (variant.config["title"] or "Видео"):
            raise ValueError("Название ролика отличается от отправленного материала; нужна ручная проверка")
        claimed = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "unknown")
            .values(
                external_id=request.external_id,
                url="https://www.youtube.com/watch?v=" + request.external_id,
                status="platform_processing",
                error=None,
                updated=now(),
            )
        )
        if not claimed.rowcount:
            raise ValueError("Состояние задания изменилось. Откройте его заново.")
    job.updated = now()

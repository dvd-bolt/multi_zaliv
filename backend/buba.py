"""Local campaign checks, not a promise of moderation or payment."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TAG = "#бубавпн"
REVIEW = {
    "profile_link": "В шапке есть t.me/bubabot, нет сторонних проектов и реферальных ссылок",
    "voice": "Озвучка разборчива, звук рекламы слышен",
    "rights": "Есть права на материал; чужой контент существенно переработан",
    "audience": "Ролик рассчитан на русскоговорящую аудиторию",
    "clean": "Нет хромакея, чужих водяных знаков, политики, 18+, оскорблений и шок-контента",
    "no_promotion": "Нет накрутки, платного продвижения и приманок на вовлечённость",
}


def with_hashtag(caption):
    if re.search(r"(?<!\w)#бубавпн(?!\w)", caption, re.I):
        return caption
    return caption.rstrip() + ("\n" if caption.strip() else "") + TAG


def banner_size(config, media):
    w, h = (1080, 1920) if config.get("format") == "1080" else (720, 1280)
    width = max(2, int(w * config.get("width", 84) / 100) // 2 * 2)
    if config.get("banner_height") is not None:
        height = max(2, int(h * config["banner_height"] / 100) // 2 * 2)
    else:
        mw, mh = media.get("width", 960), media.get("height", 180)
        ratio = min(width / mw, h / mh)
        width = max(2, int(mw * ratio) // 2 * 2)
        height = max(2, int(mh * ratio) // 2 * 2)
    return width, height, w, h


def report(media, banner, config):
    issues = []

    def issue(code, text, level="error"):
        issues.append({"code": code, "message": text, "level": level})

    length = float(media.get("duration", 0)) + (
        float(config.get("duration", 0)) if config.get("freeze_video") else 0
    )
    bw, bh, w, h = banner_size(config, banner or {})
    area = 100 * bw * bh / (w * h)
    if config.get("buba_mode"):
        if not 10 <= length <= 300:
            issue("length", "Итоговый ролик должен быть от 10 до 300 секунд")
        if length >= 120:
            issue(
                "long_timing",
                "От двух минут нужны несколько баннеров; неоднозначные тайминги правил ещё требуют уточнения. Автоотправка в этом режиме пока закрыта.",
            )
        if not banner or (banner.get("image") and not banner.get("animated")):
            issue("animated", "Нужен анимационный баннер: видео или анимированный GIF")
        if area < 25:
            issue("area", f"Баннер занимает {area:.1f}% площади кадра; нужно минимум 25%")
        if abs(config.get("x", 50) - 50) > 0.01 or abs(config.get("y", 50) - 50) > 0.01:
            issue("center", "Баннер должен быть ровно по центру: X=50%, Y=50%")
        start = media["duration"] / 2 if config.get("timing") == "middle" else config.get("start", 0)
        if abs(start - media["duration"] / 2) > 0.1:
            issue("middle", "До двух минут баннер должен быть посередине")
        if config.get("opacity", 100) < 100:
            issue("opacity", "Сделайте баннер полностью непрозрачным")
        if banner and banner.get("has_audio") and not config.get("banner_audio"):
            issue("banner_audio", "Включите звук рекламного баннера")
        if banner and banner.get("duration", 0) > config["duration"] + 0.15:
            issue("banner_cut", "Баннер обрезан по времени. Увеличьте длительность до полной длины рекламы.")
        if not media.get("has_audio") and not (
            banner and banner.get("has_audio") and config.get("banner_audio")
        ):
            issue("audio", "В исходнике и баннере нет включённого звука; нужна озвучка")
        if config.get("visibility") != "public":
            issue("public", "Для участия в выплатах ролик должен быть публичным")
        if with_hashtag(config.get("caption", "")) != config.get("caption", ""):
            issue("hashtag", "В описании нужен #бубавпн")
        for key, label in REVIEW.items():
            if not config.get("buba_review", {}).get(key):
                issue("review_" + key, label)
        issue(
            "manual_limits",
            "Проверка измеряет рамку баннера, а не видимую площадь его прозрачных пикселей. Содержание, права и итоговый вид требуют просмотра человеком.",
            "warning",
        )
    return {
        "enabled": bool(config.get("buba_mode")),
        "ready": not any(i["level"] == "error" for i in issues),
        "area_percent": round(area, 2),
        "duration": round(length, 3),
        "issues": issues,
        "review_labels": REVIEW,
        "rules_url": "https://mediabuba.ru/pravila",
    }


def job_report(db, job):
    from .db import Asset, Variant

    variant = db.get(Variant, job.variant_id)
    source = db.get(Asset, variant.asset_id)
    banner = db.get(Asset, variant.banner_id) if variant.banner_id else None
    return report(source.media, banner.media if banner else None, variant.config)


def require_ready(db, job):
    from .db import Variant, settings

    if not db.get(Variant, job.variant_id).config.get("buba_mode"):
        if settings(db).get("buba_required"):
            raise ValueError(
                "Это старый вариант без режима БУБА. Включите БУБА в монтажной, проверьте настройки и создайте новый MP4."
            )
        return
    checked = job_report(db, job)
    if checked["enabled"] and not checked["ready"]:
        errors = [i["message"] for i in checked["issues"] if i["level"] == "error"]
        raise ValueError(
            "Правила БУБА: "
            + "; ".join(errors[:3])
            + (f". Ещё {len(errors) - 3} условий: откройте БУБА → Проверки." if len(errors) > 3 else "")
        )


def payout_status(db, job):
    from sqlalchemy import select

    from .db import BubaSubmission, Metric

    record = db.get(BubaSubmission, job.id)
    snapshots = db.scalars(
        select(Metric).where(Metric.job_id == job.id).order_by(Metric.captured.desc())
    ).all()
    views = next((m.values.get("views") for m in snapshots if m.values.get("views") is not None), None)
    published = datetime.fromisoformat(record.published_at) if record else None
    deadline = (
        datetime.combine(
            published.astimezone(ZoneInfo("Europe/Moscow")).date() + timedelta(days=7),
            datetime.min.time(),
            tzinfo=ZoneInfo("Europe/Moscow"),
        )
        if published
        else None
    )
    return {
        "views": views,
        "published_at": record.published_at if record else None,
        "state": record.state if record else "untracked",
        "deadline": deadline.isoformat() if deadline else None,
        "eligible_by_local_data": bool(
            views is not None and views >= 10000 and deadline and datetime.now(UTC) <= deadline
        ),
        "note": "Время публикации подтверждается пользователем. Это учёт заявки, не отправка в бот и не гарантия выплаты.",
    }

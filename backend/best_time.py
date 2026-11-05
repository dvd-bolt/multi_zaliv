"""Best publication hour from the owner's own statistics (idea: Metricool "best time to post").

The core (views_at, hour_table, recommend) is plain Python without FrameFlow imports, so the server
statistics collector can use the same file. Views after 24 h are compared with the channel median:
a growing channel must not make late hours look better only because they are recent.
"""

from datetime import UTC, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

DAY = timedelta(hours=24)
WINDOW = range(8, 24)  # untested hours are explored only in the waking day
DEFAULT_HOUR = {"youtube": 18, "tiktok": 19}
MIN_VIDEOS = 8  # fewer videos than this: the table is shown, the advice is marked as a guess
MIN_MEDIAN = 20  # below this first-day median the hours are noise
EXPLORE_EVERY = 3  # every 3rd publication tries an hour with little data (~30 %)


def parse(value):
    date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return date if date.tzinfo else date.replace(tzinfo=UTC)


def views_at(published, points, after=DAY):
    """Views `after` publication by linear interpolation of (captured, views) points.

    A first point at most 6 h later than the mark is accepted as is (slight overestimate);
    without points around the mark the video is not used.
    """
    mark = parse(published) + after
    known = sorted((parse(t), v) for t, v in points if v is not None)
    before = [(t, v) for t, v in known if t <= mark]
    later = [(t, v) for t, v in known if t > mark]
    if before and later:
        (t0, v0), (t1, v1) = before[-1], later[0]
        return v0 + (v1 - v0) * (mark - t0) / (t1 - t0)
    if before and mark - before[-1][0] <= timedelta(hours=2):
        return before[-1][1]
    if later and later[0][0] - mark <= timedelta(hours=6) and not before:
        return later[0][1]
    return None


def hour_table(videos, timezone="Europe/Moscow"):
    """{hour: {"score", "n"}} for videos [{"published", "views24"}]; score 1.0 = channel median.

    Neighbouring hours count with half weight: with a few dozen videos a single hour is noise.
    """
    usable = [v for v in videos if v.get("views24") is not None]
    if not usable:
        return {}
    middle = median(v["views24"] for v in usable)
    if middle < MIN_MEDIAN:
        return {}  # 1–3 views a day (e.g. a TikTok with a foreign region) say nothing about the hour
    zone = ZoneInfo(timezone)
    raw = {}
    for video in usable:
        hour = parse(video["published"]).astimezone(zone).hour
        raw.setdefault(hour, []).append(video["views24"] / middle)
    table = {}
    for hour in range(24):
        total = weight = 0.0
        count = 0
        for shift, factor in ((0, 1.0), (-1, 0.5), (1, 0.5)):
            for score in raw.get((hour + shift) % 24, []):
                total, weight = total + score * factor, weight + factor
                count += shift == 0
        if weight:
            table[hour] = {"score": round(total / weight, 2), "n": count, "weight": weight}
    return table


def recommend(table, platform, published_count, total, current=None, timezone="Europe/Moscow", lead=40):
    """Next publication time for one channel: the best hour, or every 3rd time an untested hour."""
    zone = ZoneInfo(timezone)
    current = (current or datetime.now(UTC)).astimezone(zone)
    tested = {h: row for h, row in table.items() if row["weight"] >= 1.5 and row["n"]}
    explore = total >= 1 and published_count % EXPLORE_EVERY == EXPLORE_EVERY - 1
    if tested and not explore:
        hour = max(tested, key=lambda h: (tested[h]["score"], tested[h]["weight"]))
        reason = (
            f"лучший час: {tested[hour]['score']:.1f}× медианы канала за первые сутки "
            f"({tested[hour]['n']} ролик(ов) в этот час)"
        )
        mode = "best"
    elif total:
        hour = min(WINDOW, key=lambda h: (table.get(h, {}).get("weight", 0), abs(h - 19)))
        reason = "проба нового часа: здесь пока мало данных (каждый 3-й ролик — эксперимент)"
        mode = "explore"
    else:
        hour = DEFAULT_HOUR[platform]
        reason = "данных нет — время по умолчанию, вечер по Москве"
        mode = "default"
    date = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if date < current + timedelta(minutes=lead):
        date += timedelta(days=1)
    return {
        "time": date.astimezone(UTC).isoformat(),
        "hour": hour,
        "mode": mode,
        "reason": reason,
        "confident": mode == "best" and total >= MIN_VIDEOS,
    }


def channel_videos(db, account):
    """FrameFlow data: [{"published", "views24"}] of one connected channel."""
    from sqlalchemy import select

    from .db import ChannelVideo, Job, Metric, Variant

    rows = []
    if account.platform == "youtube":
        for video in db.scalars(select(ChannelVideo).where(ChannelVideo.account_id == account.id)).all():
            published = video.details.get("published_at")
            if not published or video.details.get("visibility") not in (None, "public"):
                continue
            points = [(h["captured"], h["values"].get("views")) for h in video.history]
            rows.append({"published": published, "views24": views_at(published, points)})
        return rows
    for job in db.scalars(
        select(Job).join(Variant).where(Variant.account_id == account.id, Job.external_id.is_not(None))
    ).all():
        published = job.upload_started_at or job.created
        points = [
            (m.captured, (m.values or {}).get("views"))
            for m in db.scalars(select(Metric).where(Metric.job_id == job.id)).all()
        ]
        rows.append({"published": published, "views24": views_at(published, points)})
    return rows


def advice(db, account_ids, current=None):
    """Per account: hour table and the next suggested publication time."""
    from .db import Account, settings

    zone = settings(db)["timezone"]
    result = {}
    for key in dict.fromkeys(account_ids):
        account = db.get(Account, key)
        if not account:
            continue
        videos = channel_videos(db, account)
        table = hour_table(videos, zone)
        total = sum(1 for v in videos if v["views24"] is not None)
        result[key] = {
            "videos": total,
            "hours": {str(h): {k: row[k] for k in ("score", "n")} for h, row in table.items()},
            **recommend(table, account.platform, len(videos), total, current, zone),
        }
    return result

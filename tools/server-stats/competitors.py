"""Competitor channels: what is growing in the niche right now (idea: Shortimize). Public data via yt-dlp.

config.json:
  "competitors": [{"platform": "youtube", "account": "ВЫЖИВИ", "url": "https://www.youtube.com/channel/<id>/shorts"}]
  "competitor_videos": 30   last videos per channel (newest 8 get exact counters and publish time)
  "niche_queries": ["..."]   optional YouTube searches: new channels with strong videos become candidates
Writes data/competitors/YYYY-MM-DD.jsonl (history), data/competitors.json and data/competitors.md.
Run: .venv/bin/python competitors.py   (every 3 h via frameflow-competitors.timer)
"""

import fcntl
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from urllib.parse import quote_plus

from collect import MSK, counters, fmt, upload_date, ytdlp

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STORE = DATA / "competitors"
EXACT = 8


def channel(source, limit):
    flat = ytdlp("--flat-playlist", "--playlist-end", str(limit), "-j", source["url"])
    rows = []
    for item in flat:
        rows.append({
            "id": str(item["id"]),
            "title": (item.get("title") or "")[:150],
            "url": item.get("webpage_url") or item.get("url"),
            "date": upload_date(item),
            "duration": item.get("duration"),
            **counters(item),
            "approx": source["platform"] == "youtube",
        })
    if source["platform"] == "youtube" and rows:
        newest = [r["id"] for r in rows[:EXACT]]
        full = {str(i["id"]): i for i in ytdlp("--skip-download", "-j",
                                               *[f"https://www.youtube.com/shorts/{v}" for v in newest])}
        for row in rows:
            item = full.get(row["id"])
            if item:
                row.update(counters(item), approx=False, date=upload_date(item) or row["date"],
                           duration=item.get("duration") or row["duration"])
    return rows


def past(now, hours=27):
    seen = {}
    day = (now - timedelta(hours=hours)).date()
    while day <= now.date():
        path = STORE / f"{day.isoformat()}.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                    seen.setdefault(row["id"], []).append((datetime.fromisoformat(row["t"]), row.get("v")))
                except (ValueError, KeyError):
                    pass
        day += timedelta(days=1)
    return seen


def gained(points, now, current, hours=24):
    """Views gained over ~hours from the newest record at least that old (None without history)."""
    old = [(t, v) for t, v in points if t <= now - timedelta(hours=hours) + timedelta(minutes=30) and v is not None]
    if not old or current is None:
        return None
    t, v = old[-1]
    span = (now - t).total_seconds() / 3600
    return round((current - v) * hours / span) if span > 0 else None


def search_candidates(queries, known, limit=20):
    found = {}
    for query in queries:
        try:
            # sp=EgIYAQ== is YouTube's "under 4 minutes" filter: plain search returns long videos first.
            items = ytdlp("--flat-playlist", "--playlist-end", str(limit), "-j",
                          "https://www.youtube.com/results?search_query=" + quote_plus(query)
                          + "&sp=EgIYAQ%253D%253D", timeout=180)
        except RuntimeError:
            continue
        for item in items:
            name = item.get("channel") or item.get("uploader")
            url = item.get("channel_url")
            views = item.get("view_count") or 0
            # Only short vertical-format videos: long talks about "shorts" are not competitors.
            if not name or not url or url in known or views < 10_000 or not (item.get("duration") or 999) <= 180:
                continue
            best = found.setdefault(url, {"channel": name, "url": url, "views": 0, "title": ""})
            if views > best["views"]:
                best.update(views=views, title=(item.get("title") or "")[:80])
    return sorted(found.values(), key=lambda c: c["views"], reverse=True)[:10]


def report(now, channels, errors, candidates):
    lines = [f"# Конкуренты — {now.astimezone(MSK).strftime('%d.%m.%Y %H:%M')} МСК", ""]
    if errors:
        lines += ["**Ошибки сбора:** " + "; ".join(f"{k}: {v}" for k, v in errors.items()), ""]
    videos = [v for c in channels for v in c["videos"]]
    rising = sorted(
        (v for v in videos if v["growth_24h"] is not None or v["per_hour"] is not None),
        key=lambda v: v["growth_24h"] if v["growth_24h"] is not None else v["per_hour"] * 24,
        reverse=True,
    )[:10]
    lines += ["## 🔥 Растёт сейчас", "",
              "| Канал | Ролик | Просм. | +24ч | ×медианы | Длит. | Выложен (МСК) |", "|---|---|---|---|---|---|---|"]
    for v in rising:
        grow = v["growth_24h"] if v["growth_24h"] is not None else (
            f"~{fmt(round(v['per_hour'] * 24))}" if v["per_hour"] is not None else None)
        when = datetime.fromisoformat(v["date"]).astimezone(MSK).strftime("%d.%m %H:%M") if v.get("date") and "T" in v["date"] else ""
        lines.append(f"| {v['channel']} | [{v['title'][:50].replace('|', '/')}]({v['url']}) | {fmt(v['views'])} | "
                     f"{grow if isinstance(grow, str) else ('+' + fmt(grow) if grow is not None else '')} | "
                     f"{v['ratio'] or ''} | {round(v['duration']) if v.get('duration') else ''} с | {when} |")
    lines += ["", "_+24ч — по истории сборов; «~» — оценка по скорости с момента публикации, пока истории нет._", ""]
    lines += ["## Каналы", "", "| Канал | Роликов | Медиана | Лучший | Часы публикаций хитов (МСК) |", "|---|---|---|---|---|"]
    for c in channels:
        hits = [v for v in c["videos"] if v["ratio"] and v["ratio"] >= 2 and v.get("date") and "T" in v["date"]]
        hours = sorted({datetime.fromisoformat(v["date"]).astimezone(MSK).hour for v in hits})
        top = max(c["videos"], key=lambda v: v["views"] or 0, default=None)
        lines.append(f"| [{c['account']}]({c['url']}) | {len(c['videos'])} | {fmt(c['median'])} | "
                     f"{fmt(top['views']) if top else ''} | {', '.join(f'{h}:00' for h in hours) or '—'} |")
    if candidates:
        lines += ["", "## Возможные новые конкуренты (поиск по нише, только предложение)", "",
                  "| Канал | Лучший ролик | Просм. |", "|---|---|---|"]
        for c in candidates:
            lines.append(f"| [{c['channel']}]({c['url']}) | {c['title'].replace('|', '/')} | {fmt(c['views'])} |")
    (DATA / "competitors.md").write_text("\n".join(lines) + "\n")


def main():
    config = json.loads((ROOT / "config.json").read_text())
    sources = config.get("competitors", [])
    STORE.mkdir(parents=True, exist_ok=True)
    lock = (DATA / ".competitors.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Сбор конкурентов уже идёт")
        return 1
    now = datetime.now(UTC).replace(microsecond=0)
    history = past(now)
    channels, errors, rows = [], {}, []
    for source in sources:
        try:
            found = channel(source, config.get("competitor_videos", 30))
        except Exception as error:  # one channel failing must not lose the others
            errors[source["account"]] = str(error)[:200]
            continue
        views = [v["v"] for v in found if v.get("v") is not None]
        middle = round(median(views)) if views else None
        videos = []
        for row in found:
            rows.append({"t": now.isoformat(), "a": source["account"], "id": row["id"], "v": row.get("v")})
            age = (now - datetime.fromisoformat(row["date"])).total_seconds() / 3600 if row.get("date") and "T" in row["date"] else None
            videos.append({
                "channel": source["account"], "id": row["id"], "title": row["title"], "url": row["url"],
                "date": row.get("date"), "duration": row.get("duration"), "views": row.get("v"),
                "approx": row["approx"],
                "growth_24h": gained(history.get(row["id"], []), now, row.get("v")),
                "per_hour": round(row["v"] / age, 1) if age and age >= 1 and row.get("v") is not None and age <= 72 else None,
                "ratio": round(row["v"] / middle, 1) if middle and row.get("v") is not None else None,
            })
        channels.append({**source, "median": middle, "videos": videos})
    with (STORE / f"{now.date().isoformat()}.jsonl").open("a") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    known = {s["url"].split("/shorts")[0] for s in sources}
    candidates = search_candidates(config.get("niche_queries", []), known) if config.get("niche_queries") else []
    tmp = DATA / "competitors.json.tmp"
    tmp.write_text(json.dumps({"collected": now.isoformat(), "errors": errors, "channels": channels,
                               "candidates": candidates}, ensure_ascii=False, indent=1))
    tmp.replace(DATA / "competitors.json")
    report(now, channels, errors, candidates)
    edge = (now - timedelta(days=config.get("keep_days", 90))).date().isoformat()
    for old in STORE.glob("*.jsonl"):
        if old.stem < edge:
            old.unlink()
    print(f"Конкуренты: каналов {len(channels)}, роликов {len(rows)}, ошибок {len(errors)}, кандидатов {len(candidates)}")
    return 0 if channels or not errors else 2


if __name__ == "__main__":
    sys.exit(main())

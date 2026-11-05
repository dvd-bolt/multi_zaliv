"""Collect public TikTok/YouTube counters of the owner's channels with yt-dlp (no keys, no browser).

Writes into data/:
  YYYY-MM-DD.jsonl  one compact line per video per run (history, UTC dates, kept keep_days)
  videos.json       titles, upload dates and links of known videos
  latest.json       last run: counters plus growth over ~1 h and ~24 h
  latest.md         the same as a readable table (Moscow time)
  comments.json     all public comments per video (fetched only when the comment count changes)
  comments.md       the same, newest first; 🆕 marks comments first seen within 24 h
Run: .venv/bin/python collect.py   (hourly via systemd timer frameflow-stats.timer)
"""

import fcntl
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")
MSK = ZoneInfo("Europe/Moscow")
KEYS = (("v", "view_count"), ("l", "like_count"), ("c", "comment_count"), ("s", "repost_count"))


def ytdlp(*args, timeout=300):
    run = subprocess.run(
        [YTDLP, "--no-warnings", "--ignore-errors", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    items = []
    for line in run.stdout.splitlines():
        try:
            items.append(json.loads(line))
        except ValueError:
            pass
    if not items and run.returncode:
        raise RuntimeError((run.stderr.strip().splitlines() or ["yt-dlp error"])[-1][:300])
    return items


def counters(item):
    return {short: item.get(name) for short, name in KEYS}


def upload_date(item):
    if item.get("timestamp"):
        return datetime.fromtimestamp(item["timestamp"], UTC).isoformat()
    if item.get("upload_date"):
        return datetime.strptime(item["upload_date"], "%Y%m%d").replace(tzinfo=UTC).date().isoformat()
    return None


def collect_source(source, videos, now, exact_days):
    """Rows for one channel; updates videos (metadata) in place."""
    try:
        flat = ytdlp("--flat-playlist", "-j", source["url"])
    except RuntimeError as error:
        if "does not have any videos" in str(error):
            return []
        raise
    rows = []
    exact = []
    for item in flat:
        vid = str(item["id"])
        meta = videos.setdefault(vid, {})
        meta.update(
            platform=source["platform"],
            account=source["account"],
            title=(item.get("title") or meta.get("title") or "")[:150],
            url=item.get("webpage_url") or item.get("url") or meta.get("url"),
        )
        if upload_date(item):
            meta["date"] = upload_date(item)
        row = {"id": vid, **counters(item)}
        if source["platform"] == "youtube":
            date = meta.get("date")
            recent = not date or datetime.fromisoformat(date).replace(tzinfo=UTC) > now - timedelta(days=exact_days)
            if recent:
                exact.append(vid)
            else:
                row["approx"] = True  # flat listing rounds YouTube views (1.4K)
        rows.append(row)
    if exact:
        full = {str(i["id"]): i for i in ytdlp("--skip-download", "-j",
                                               *[f"https://www.youtube.com/shorts/{v}" for v in exact])}
        for row in rows:
            item = full.get(row["id"])
            if item:
                row.update(counters(item))
                videos[row["id"]]["date"] = upload_date(item) or videos[row["id"]].get("date")
                videos[row["id"]]["title"] = (item.get("title") or videos[row["id"]]["title"])[:150]
            elif row["id"] in exact:
                row["approx"] = True
    return rows


def fetch_comments(video):
    item = (ytdlp("--skip-download", "--write-comments", "--extractor-args", "youtube:max_comments=300",
                  "-j", video["url"], timeout=180) or [{}])[0]
    return [{"id": str(c.get("id")), "author": (c.get("author") or "")[:60], "text": (c.get("text") or "")[:1000],
             "likes": c.get("like_count") or 0, "reply": c.get("parent") not in (None, "root"),
             "posted": datetime.fromtimestamp(c["timestamp"], UTC).isoformat() if c.get("timestamp") else None}
            for c in item.get("comments") or []]


def update_comments(report, now):
    """Refresh comments of videos whose public comment count changed. Returns (new comments, errors)."""
    path = DATA / "comments.json"
    store = json.loads(path.read_text()) if path.exists() else {}
    fresh, errors = 0, {}
    for video in report["videos"]:
        saved = store.setdefault(video["id"], {"count": None, "comments": {}})
        saved.update(title=video["title"], platform=video["platform"], account=video["account"], url=video["url"])
        if video["comments"] == saved["count"] or not video["url"]:
            continue
        if not video["comments"] and saved["count"] is not None:
            saved["count"] = video["comments"]
            continue
        try:
            for comment in fetch_comments(video):
                known = saved["comments"].get(comment["id"])
                if known:
                    known["likes"] = comment["likes"]
                else:
                    saved["comments"][comment["id"]] = {**comment, "seen": now.isoformat()}
                    fresh += 1
            saved["count"] = video["comments"]
        except Exception as error:
            errors[video["id"]] = str(error)[:200]
    tmp = DATA / "comments.json.tmp"
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1))
    tmp.replace(path)
    lines = [f"# Комментарии — {report['collected_msk']} МСК", "",
             "_🆕 — впервые замечен за последние 24 ч. Время написания у YouTube примерное._", ""]
    day_ago = now - timedelta(hours=24)
    for vid, saved in sorted(store.items(), key=lambda x: max([c["seen"] for c in x[1]["comments"].values()] or [""]),
                             reverse=True):
        if not saved["comments"]:
            continue
        platform = "TikTok" if saved.get("platform") == "tiktok" else "YouTube"
        lines += [f"## {saved.get('title', vid)[:80]} — {platform} {saved.get('account', '')} "
                  f"({len(saved['comments'])})", ""]
        for c in sorted(saved["comments"].values(), key=lambda c: c["seen"], reverse=True):
            new = "🆕 " if datetime.fromisoformat(c["seen"]) > day_ago else ""
            text = " ".join(c["text"].split())
            lines.append(f"- {new}{'↳ ' if c['reply'] else ''}**{c['author']}**"
                         f"{' ♥' + str(c['likes']) if c['likes'] else ''}: {text}")
        lines.append("")
    (DATA / "comments.md").write_text("\n".join(lines) + "\n")
    return fresh, errors


def history(now, hours):
    """{video id: [(time, row)]} from the jsonl files that cover the last `hours`."""
    seen = {}
    day = (now - timedelta(hours=hours)).date()
    while day <= now.date():
        path = DATA / f"{day.isoformat()}.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                    seen.setdefault(row["id"], []).append((datetime.fromisoformat(row["t"]), row))
                except (ValueError, KeyError):
                    pass
        day += timedelta(days=1)
    return seen


def growth(points, now, hours):
    """Views gained since the newest record at least `hours` old (approximate for gaps)."""
    older = [(t, r) for t, r in points if t <= now - timedelta(hours=hours) + timedelta(minutes=10)]
    if not older:
        return None
    return older[-1][1]


def fmt(n):
    return "—" if n is None else f"{n:,}".replace(",", " ")


def signed(x):
    return "" if x is None else (f"+{fmt(x)}" if x >= 0 else fmt(x))


def delta(cur, old):
    if old is None or cur.get("v") is None or old.get("v") is None:
        return None
    return cur["v"] - old["v"]


def write_md(report):
    lines = [f"# Статистика каналов — {report['collected_msk']} МСК", ""]
    if report["errors"]:
        lines += ["**Ошибки сбора:** " + "; ".join(f"{k}: {v}" for k, v in report["errors"].items()), ""]
    groups = {}
    for video in report["videos"]:
        groups.setdefault((video["platform"], video["account"]), []).append(video)
    for empty in report.get("empty", []):
        lines += [f"_{empty}: опубликованных видео нет._", ""]
    for (platform, account), items in groups.items():
        total = sum(v["views"] or 0 for v in items)
        known = [v["views_24h"] for v in items if v["views_24h"] is not None]
        day = f" (+{fmt(sum(known))} за 24 ч)" if known else ""
        lines += [f"## {'TikTok' if platform == 'tiktok' else 'YouTube'} {account} — {len(items)} видео, "
                  f"{fmt(total)} просмотров{day}", "",
                  "| Дата | Просм. | +1ч | +24ч | Лайки | Комм. | Видео |", "|---|---|---|---|---|---|---|"]
        for v in items:
            date = v["date"] or ""
            if "T" in date:
                date = datetime.fromisoformat(date).astimezone(MSK).strftime("%d.%m %H:%M")
            elif date:
                date = datetime.fromisoformat(date).strftime("%d.%m")
            lines.append(f"| {date} | {fmt(v['views'])}{'≈' if v.get('approx') else ''} | {signed(v['views_1h'])} | "
                         f"{signed(v['views_24h'])} | {fmt(v['likes'])} | {fmt(v['comments'])} | "
                         f"{v['title'][:60].replace('|', '/')} |")
        lines.append("")
    lines.append("_≈ — округлённые YouTube-просмотры старых роликов. Пустые +1ч/+24ч — ещё нет истории._")
    (DATA / "latest.md").write_text("\n".join(lines) + "\n")


def best_time_report(now, videos, keep_days):
    """data/best_time.md: publication hours (MSK) by first-day views relative to the channel median.

    Same formula as FrameFlow (best_time.py is a copy of backend/best_time.py from the multi_zaliv repo).
    """
    from best_time import MIN_VIDEOS, hour_table, views_at

    points = history(now, keep_days * 24)
    groups = {}
    for vid, meta in videos.items():
        date = meta.get("date") or ""
        if "T" not in date or vid not in points:
            continue
        series = [(t.isoformat(), row.get("v")) for t, row in points[vid] if not row.get("approx")]
        groups.setdefault((meta["platform"], meta["account"]), []).append(
            {"published": date, "views24": views_at(date, series)}
        )
    lines = [f"# Лучшее время публикации — {now.astimezone(MSK).strftime('%d.%m.%Y %H:%M')} МСК", "",
             "_Просмотры за первые сутки относительно медианы канала (1.0 = медиана). "
             "Соседние часы учитываются с половинным весом._", ""]
    for (platform, account), items in sorted(groups.items()):
        usable = [v for v in items if v["views24"] is not None]
        table = hour_table(items)
        lines += [f"## {'TikTok' if platform == 'tiktok' else 'YouTube'} {account} — роликов с данными: {len(usable)}", ""]
        if not table:
            lines += ["Пока нет роликов с историей за первые сутки." if not usable else
                      "Просмотров за первые сутки слишком мало (медиана < 20) — по часам выводов нет.", ""]
            continue
        ranked = sorted(((h, r) for h, r in table.items() if r["weight"] >= 1.5 and r["n"]),
                        key=lambda x: x[1]["score"], reverse=True)
        if ranked:
            best = ", ".join(f"{h}:00 ({r['score']}×, {r['n']} шт.)" for h, r in ranked[:3])
            lines.append(f"**Лучшие часы:** {best}" + ("" if len(usable) >= MIN_VIDEOS else " — данных мало, это догадка"))
        lines += ["", "| Час | Индекс | Роликов |", "|---|---|---|"]
        lines += [f"| {h}:00 | {r['score']} | {r['n']} |" for h, r in sorted(table.items()) if r["n"]]
        lines.append("")
    (DATA / "best_time.md").write_text("\n".join(lines) + "\n")


def main():
    config = json.loads((ROOT / "config.json").read_text())
    DATA.mkdir(exist_ok=True)
    lock = (DATA / ".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Сбор уже идёт, дождитесь его окончания")
        return 1
    now = datetime.now(UTC).replace(microsecond=0)
    videos_path = DATA / "videos.json"
    videos = json.loads(videos_path.read_text()) if videos_path.exists() else {}
    rows, errors, empty = [], {}, []
    for source in config["sources"]:
        name = f"{source['platform']} {source['account']}"
        try:
            found = [{"t": now.isoformat(), "p": source["platform"], "a": source["account"], **r}
                     for r in collect_source(source, videos, now, config.get("exact_youtube_days", 14))]
            rows += found
            if not found:
                empty.append(name)
        except Exception as error:  # one channel failing must not lose the others
            errors[name] = str(error)[:300]
    past = history(now, 26)
    with (DATA / f"{now.date().isoformat()}.jsonl").open("a") as out:
        for row in rows:
            out.write(json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False) + "\n")
    videos_path.write_text(json.dumps(videos, ensure_ascii=False, indent=1))
    report = {"collected": now.isoformat(),
              "collected_msk": now.astimezone(MSK).strftime("%d.%m.%Y %H:%M"),
              "errors": errors, "empty": empty, "videos": []}
    for row in rows:
        meta = videos[row["id"]]
        points = past.get(row["id"], [])
        report["videos"].append({
            "platform": row["p"], "account": row["a"], "id": row["id"], "title": meta.get("title", ""),
            "url": meta.get("url"), "date": meta.get("date"),
            "views": row.get("v"), "likes": row.get("l"), "comments": row.get("c"), "shares": row.get("s"),
            "approx": row.get("approx", False),
            "views_1h": delta(row, growth(points, now, 1)),
            "views_24h": delta(row, growth(points, now, 24)),
        })
    report["videos"].sort(key=lambda v: (v["platform"], v["account"], v["date"] or ""), reverse=True)
    tmp = DATA / "latest.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    tmp.replace(DATA / "latest.json")
    write_md(report)
    try:
        fresh, comment_errors = update_comments(report, now)
    except Exception as error:
        fresh, comment_errors = 0, {"all": str(error)[:200]}
    try:
        best_time_report(now, videos, min(config.get("keep_days", 90), 30))
    except Exception as error:  # an advice report must never break the statistics
        print(f"  лучшее время: {error}")
    edge = (now - timedelta(days=config.get("keep_days", 90))).date().isoformat()
    for old in DATA.glob("*.jsonl"):
        if old.stem < edge:
            old.unlink()
    print(f"{report['collected_msk']} МСК: видео {len(rows)}, ошибок {len(errors)}, новых комментариев {fresh}")
    for vid, error in comment_errors.items():
        print(f"  комментарии {vid}: {error}")
    for name, error in errors.items():
        print(f"  {name}: {error}")
    return 0 if rows or not errors else 2


if __name__ == "__main__":
    sys.exit(main())

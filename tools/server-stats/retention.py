#!/usr/bin/env python3
"""Where viewers leave: YouTube Analytics retention curve → seconds → scene/line of the episode → what to fix.

  retention.py [--days 30] [--send]       data/retention.md (+ json); --send: short summary to Telegram
  retention.py video VIDEO_ID [--send]    one video in detail

Episode timelines come from /opt/video-projects (data/delivery-timeline.json or timeline.json). The link
video → episode: data/retention_map.json (filled by hand for the first videos) and the server publication
queue (/opt/frameflow/publisher), which knows the uploaded file. Published files may contain the BUBA ad that
FrameFlow inserted on the laptop: its length = YouTube duration − master duration, placed at the ad pause
(or, unknown, at the middle — then the scene mapping after the middle is marked approximate).
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MAP = DATA / "retention_map.json"
PROJECTS = Path("/opt/video-projects/banner-videos/projects")
sys.path.insert(0, "/opt/frameflow/publisher")
import publisher  # noqa: E402 — channels, credentials and Telegram are shared with the publication queue

MIN_VIEWS = 100
SETTLE = timedelta(hours=36)  # the curve of a younger video still changes a lot


def token(channel):
    c = publisher.credentials(channel)
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen(c.get("token_uri") or "https://oauth2.googleapis.com/token", body, timeout=30) as r:
        return json.load(r)["access_token"]


def get(tok, url, **query):
    request = urllib.request.Request(url + "?" + urllib.parse.urlencode(query), headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(request, timeout=60) as r:
        return json.load(r)


def seconds(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0) if m else 0


def channel_videos(tok, limit=50):
    uploads = get(tok, "https://www.googleapis.com/youtube/v3/channels", part="contentDetails", mine="true")
    playlist = uploads["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = get(tok, "https://www.googleapis.com/youtube/v3/playlistItems", part="contentDetails", maxResults=limit,
                playlistId=playlist)["items"]
    ids = [i["contentDetails"]["videoId"] for i in items]
    if not ids:
        return []
    videos = get(tok, "https://www.googleapis.com/youtube/v3/videos", part="snippet,contentDetails,statistics,status",
                 id=",".join(ids))["items"]
    return [{"id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
             "duration": seconds(v["contentDetails"]["duration"]), "views": int(v["statistics"].get("viewCount", 0)),
             "privacy": v["status"]["privacyStatus"]} for v in videos]


def curve(tok, video):
    start = video["published"][:10]
    rows = get(tok, "https://youtubeanalytics.googleapis.com/v2/reports", ids="channel==MINE", startDate=start,
               endDate=datetime.now(UTC).date().isoformat(), metrics="audienceWatchRatio,relativeRetentionPerformance",
               dimensions="elapsedVideoTimeRatio", filters=f"video=={video['id']}").get("rows") or []
    return [{"t": r[0] * video["duration"], "watch": r[1], "relative": r[2]} for r in rows]


def episode_dirs():
    """{video id: episode folder} from the hand map and the publication queue."""
    mapping = json.loads(MAP.read_text()) if MAP.exists() else {}
    result = {vid: PROJECTS / folder for vid, folder in mapping.items()}
    queue = publisher.load()
    for job in queue["jobs"]:
        if job.get("video_id") and "/episodes/" in job["video"]:
            folder = Path(job["video"].split("/output/")[0])
            result.setdefault(job["video_id"], folder)
    return result


def timeline(folder):
    for name in ("delivery-timeline.json", "timeline.json"):
        path = folder / "data" / name
        if path.exists():
            return json.loads(path.read_text())
    return None


def locate(t, line, video):
    """(text describing the moment, approximate?) for second t of the published video."""
    if not line:
        return None, True
    master = line.get("master_duration") or line["duration"]
    ad = max(0.0, video["duration"] - master)
    pause = (line.get("ad_pause") or {}).get("insert_at")
    insert = pause if pause is not None else master / 2
    approx = pause is None and ad > 1 and t >= insert - 3  # before the estimated ad the mapping is exact
    if ad > 1 and insert <= t < insert + ad:
        return "реклама БУБА", approx
    if ad > 1 and t >= insert + ad:
        t -= ad
    for event in line.get("events", []):
        if event["start"] - 0.3 <= t <= event["end"] + 0.3:
            return f"сцена «{event.get('scene', '?')}», {event.get('role', '')}: «{event['text'][:70]}»", approx
    scene = next((s["id"] for s in line.get("scenes", []) if s["start"] <= t <= s["end"]), None)
    return (f"сцена «{scene}» (без реплики)" if scene else "вне сцен"), approx


def scene_of(where):
    if where == "реклама БУБА":
        return "реклама"
    m = re.search(r"сцена «([^»]+)»", where or "")
    return m[1] if m else None


def repeated(rows):
    """Scenes that are among the biggest drops in several episodes of the same series."""
    seen = {}
    for r in rows:
        if not r["episode"]:
            continue
        series = r["episode"].split("/")[0]
        for d in r["drops"]:
            if d["scene"] and d["t"] > 3:
                seen.setdefault((series, d["scene"]), []).append(d)
    total = {}
    for r in rows:
        if r["episode"]:
            total[r["episode"].split("/")[0]] = total.get(r["episode"].split("/")[0], 0) + 1
    out = []
    for (series, scene), drops in seen.items():
        if len(drops) >= 2 and len(drops) >= total[series] / 4:
            out.append({"series": series, "scene": scene, "count": len(drops), "of": total[series],
                        "lost": sum(d["lost"] for d in drops) / len(drops), "example": drops[0]["where"]})
    return sorted(out, key=lambda x: -x["count"] * x["lost"])


def analyse(video, points, line):
    """Key numbers and the biggest drops (share of the starting audience lost per ~1 s)."""
    if len(points) < 10:
        return None
    first = max(points[0]["watch"], 1e-6)

    def at(sec):
        return min(points, key=lambda p: abs(p["t"] - sec))["watch"] / first

    drops = []
    step = max(1, round(len(points) / video["duration"]))  # ≈ one second of the curve
    for i in range(step, len(points) - 1):
        lost = (points[i - step]["watch"] - points[i]["watch"]) / first
        drops.append((lost, points[i]["t"]))
    drops.sort(reverse=True)
    picked = []
    for lost, t in drops:
        if lost < 0.02 or len(picked) == 3:
            break
        if all(abs(t - p[1]) > 3 for p in picked):
            picked.append((lost, t))
    result = {"keep3": at(3), "keep10": at(10), "half": at(video["duration"] / 2), "end": at(video["duration"] - 1),
              "relative": sum(p["relative"] for p in points) / len(points), "drops": []}
    for lost, t in sorted(picked, key=lambda p: p[1]):
        where, approx = locate(t, line, video)
        result["drops"].append({"t": round(t, 1), "lost": round(lost, 3), "where": where, "approx": approx,
                                "scene": scene_of(where)})
    result["advice"] = advice(result)
    return result


def advice(r):
    tips = []
    if r["keep3"] < 0.75:
        tips.append(f"слабое начало: к 3-й секунде осталось {r['keep3']:.0%} — сильнее первый кадр/фраза")
    for d in r["drops"]:
        if d["where"] == "реклама БУБА" and d["lost"] >= 0.04:
            tips.append(f"на рекламе уходит {d['lost']:.0%} — короче вставка или позже")
        elif d["t"] > 3 and d["lost"] >= 0.05 and d["where"]:
            tips.append(f"{d['t']:.0f} с — {d['where']}: −{d['lost']:.0%}, сократить/ускорить")
    if r["end"] >= 0.6:
        tips.append("досматривают хорошо — формат держит, развивать")
    return tips


def report(days=30, only=None):
    cfg = publisher.config()
    episodes = episode_dirs()
    out = []
    for channel, conf in cfg["channels"].items():
        tok = token(channel)
        for video in channel_videos(tok):
            if only and video["id"] != only:
                continue
            published = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
            if not only and (video["privacy"] != "public" or video["views"] < MIN_VIEWS
                             or datetime.now(UTC) - published < SETTLE or datetime.now(UTC) - published > timedelta(days=days)):
                continue
            folder = episodes.get(video["id"])
            line = timeline(folder) if folder else None
            result = analyse(video, curve(tok, video), line)
            if result:
                out.append({**video, "channel": conf["title"], "episode": str(folder.relative_to(PROJECTS)) if folder else None,
                            **result})
    out.sort(key=lambda v: v["published"], reverse=True)
    return out


def markdown(rows):
    now = datetime.now(UTC).astimezone(publisher.MSK).strftime("%d.%m.%Y %H:%M")
    lines = [f"# Удержание — {now} МСК", "",
             "_Доли от зрителей первой секунды. «Отн.» — YouTube сравнивает с похожими роликами той же длины: 0,5 = средне._", ""]
    if rows:
        avg = lambda k: sum(r[k] for r in rows) / len(rows)  # noqa: E731
        lines += [f"**Среднее по {len(rows)} роликам:** к 3 с — {avg('keep3'):.0%}, к 10 с — {avg('keep10'):.0%}, "
                  f"середина — {avg('half'):.0%}, конец — {avg('end'):.0%}, отн. {avg('relative'):.2f}", ""]
        ad = [d["lost"] for r in rows for d in r["drops"] if d["where"] == "реклама БУБА"]
        if ad:
            lines += [f"**Реклама:** в {len(ad)} роликах — среди главных провалов, в среднем −{sum(ad) / len(ad):.0%}.", ""]
    rep = repeated(rows)
    if rep:
        lines += ["## Повторяющиеся провалы", ""]
        lines += [f"- **{x['series']}** · {'реклама' if x['scene'] == 'реклама' else 'сцена «' + x['scene'] + '»'} — "
                  f"в {x['count']} из {x['of']} серий, в среднем −{x['lost']:.0%}. Пример: {x['example']}" for x in rep]
        lines.append("")
    for r in rows:
        lines += [f"## {r['title'][:70]} — {r['channel']}", "",
                  f"{r['published'][:10]} · {r['views']} просм. · {r['duration']} с · серия: {r['episode'] or 'не связана'} · "
                  f"https://youtube.com/shorts/{r['id']}", "",
                  f"К 3 с: **{r['keep3']:.0%}** · к 10 с: {r['keep10']:.0%} · середина: {r['half']:.0%} · "
                  f"конец: {r['end']:.0%} · отн.: {r['relative']:.2f}", ""]
        for d in r["drops"]:
            lines.append(f"- {d['t']:.0f} с: −{d['lost']:.0%} — {d['where'] or 'серия не связана'}"
                         + (" (≈ место рекламы неизвестно)" if d["approx"] else ""))
        lines += [f"- 👉 {tip}" for tip in r["advice"]] + [""]
    return "\n".join(lines) + "\n"


def summary(rows):
    if not rows:
        return "📉 Удержание: роликов с данными нет (нужно ≥ 100 просмотров и ≥ 36 ч)."
    e = publisher.esc
    lines = [f"📉 <b>Удержание — {len(rows)} роликов</b>"]
    avg = lambda k: sum(r[k] for r in rows) / len(rows)  # noqa: E731
    lines.append(f"В среднем: к 3 с {avg('keep3'):.0%}, середина {avg('half'):.0%}, конец {avg('end'):.0%}")
    best = max(rows, key=lambda r: r["end"])
    worst = min(rows, key=lambda r: r["keep3"])
    lines.append(f"Лучше всех досматривают: «{e(best['title'][:50])}» ({best['end']:.0%})")
    lines.append(f"Хуже всех начало: «{e(worst['title'][:50])}» (к 3 с {worst['keep3']:.0%})")
    rep = repeated(rows)
    if rep:
        lines.append("\n<b>Повторяется из серии в серию</b>")
        lines += [f"• {e(x['series'])}: {'реклама' if x['scene'] == 'реклама' else 'сцена «' + e(x['scene']) + '»'} — "
                  f"{x['count']} из {x['of']} серий, −{x['lost']:.0%}" for x in rep[:6]]
    tips = {}
    for r in rows:
        for tip in r["advice"]:
            key = tip.split(":")[0] if "начало" in tip or "рекламе" in tip else tip
            tips.setdefault(key, tip)
    if tips:
        lines.append("\n<b>Что поправить</b>")
        lines += [f"• {e(t)}" for t in list(tips.values())[:8]]
    lines.append("\nПодробно: /opt/frameflow/stats/data/retention.md")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="report", choices=["report", "video"])
    p.add_argument("video", nargs="?")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--send", action="store_true")
    args = p.parse_args()
    rows = report(args.days, args.video if args.cmd == "video" else None)
    if args.cmd == "report":
        (DATA / "retention.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        (DATA / "retention.md").write_text(markdown(rows))
    text = summary(rows) if args.send else markdown(rows)
    if args.send:
        sys.path.insert(0, str(ROOT))
        from audience import send
        send(text)
    else:
        print(text)


if __name__ == "__main__":
    main()

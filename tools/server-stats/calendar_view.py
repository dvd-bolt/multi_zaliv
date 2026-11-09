#!/usr/bin/env python3
"""Content calendar: server queue (/opt/frameflow/publisher) + FrameFlow queue on the laptop + what came out.

  calendar_view.py [--days 7] [--send]   calendar for the coming days
  calendar_view.py remind                 Telegram reminder only when tomorrow is empty for a channel

The laptop part comes from the statistics snapshot FrameFlow pushes to the bot (field "upcoming"); when the
laptop is off or not connected, the calendar says how old that data is instead of pretending it is empty.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAPTOP = Path("/opt/cloude-bot/data/frameflow-stats/latest.json")
sys.path.insert(0, "/opt/frameflow/publisher")
import publisher  # noqa: E402

MSK = publisher.MSK
DAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ICON = {"new": "❔", "asked": "❔", "approved": "⏳", "uploading": "⬆️", "scheduled": "🕒", "published": "✅",
        "queued": "⏳", "rendering": "⚙️", "ready": "📦", "waiting_auth": "🔑", "unknown": "❓"}


def parse(value):
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def items(days_back=2):
    """[{time, channel, title, status, source}] around now."""
    out, since = [], datetime.now(UTC) - timedelta(days=days_back)
    cfg = publisher.config()
    for job in publisher.load()["jobs"]:
        if job["status"] in ("cancelled", "failed") or job.get("dry_run"):
            continue
        out.append({"time": parse(job["slot"]), "channel": f"YouTube {cfg['channels'][job['channel']]['title']}",
                    "title": job["title"], "status": job["status"], "source": "сервер"})
    laptop_age = None
    if LAPTOP.exists():
        record = json.loads(LAPTOP.read_text())
        laptop_age = datetime.now(UTC) - parse(record["received"])
        for row in record["snapshot"].get("upcoming") or []:
            if row.get("time"):
                platform = "TikTok" if row.get("platform") == "tiktok" else "YouTube"
                out.append({"time": parse(row["time"]), "channel": f"{platform} {row.get('account', '')}",
                            "title": row.get("title", ""), "status": row.get("status", ""), "source": "ПК"})
    known = {(i["channel"], i["title"][:30]) for i in out}
    videos = json.loads((ROOT / "data" / "videos.json").read_text())
    for meta in videos.values():
        if "T" not in (meta.get("date") or ""):
            continue
        t = parse(meta["date"])
        platform = "TikTok" if meta["platform"] == "tiktok" else "YouTube"
        channel = f"{platform} {meta['account']}"
        if t >= since and (channel, meta["title"][:30]) not in known:
            out.append({"time": t, "channel": channel, "title": meta["title"], "status": "published", "source": "канал"})
    return sorted((i for i in out if i["time"] >= since), key=lambda i: i["time"]), laptop_age


def render(days, html):
    rows, laptop_age = items()
    e = publisher.esc if html else str
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    today = datetime.now(MSK).date()
    lines = [f"📅 {b('Контент-календарь')}"]
    if laptop_age is None:
        lines.append("ПК: FrameFlow на ноутбуке не подключён к боту (/frameflow) — его очередь не видна.")
    elif laptop_age > timedelta(hours=3):
        lines.append(f"ПК: данные ноутбука {laptop_age.total_seconds() / 3600:.0f} ч назад — могли измениться.")
    for offset in range(-2, days):
        day = today + timedelta(days=offset)
        todays = [r for r in rows if r["time"].astimezone(MSK).date() == day]
        name = {0: "сегодня", 1: "завтра", -1: "вчера"}.get(offset, "")
        head = f"{day:%d.%m} {DAYS_RU[day.weekday()]}" + (f" — {name}" if name else "")
        if not todays and offset < 0:
            continue
        lines.append(f"\n{b(head)}")
        if not todays:
            lines.append("  пусто")
        for r in todays:
            lines.append(f"  {r['time'].astimezone(MSK):%H:%M} {ICON.get(r['status'], '•')} {e(r['channel'])} — "
                         f"{e(r['title'][:55])} ({r['source']})")
    return "\n".join(lines)


def empty_tomorrow():
    rows, _ = items(0)
    tomorrow = datetime.now(MSK).date() + timedelta(days=1)
    planned = {r["channel"] for r in rows if r["time"].astimezone(MSK).date() == tomorrow}
    channels = [f"YouTube {c['title']}" for c in publisher.config()["channels"].values()]
    return [c for c in channels if c not in planned]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="show", choices=["show", "remind"])
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--send", action="store_true")
    args = p.parse_args()
    from audience import send

    if args.cmd == "remind":
        empty = empty_tomorrow()
        if empty:
            send("📅 <b>На завтра пусто:</b> " + ", ".join(publisher.esc(c) for c in empty) +
                 "\nЕсть готовая серия — поставь в очередь (или скажи мне «залей …»).\n\n" + render(3, True))
        print("пусто:", empty or "нет")
        return
    if args.send:
        send(render(args.days, True))
    else:
        print(render(args.days, False))


if __name__ == "__main__":
    main()

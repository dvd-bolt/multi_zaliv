#!/usr/bin/env python3
"""Viewers' comments → votes and a daily digest (data from collect.py: data/comments.json).

  audience.py poll add VIDEO --question "Кого съесть?" --option "Лампочку=лампоч" --option "Сосиски=сосис,сосиск"
  audience.py poll list | poll show VIDEO [--send] | poll remove VIDEO
  audience.py words VIDEO              what viewers name most often (to set up options quickly)
  audience.py digest [--hours 24] [--send]   new questions, requests, ideas and running votes → Telegram

VIDEO is a video id or a link. Votes: one per author (the latest comment counts); an option matches when one of
its stems starts a word ("лампоч" → «лампочку», «лампочка»). A comment naming several options is listed as
disputed, not counted. Likes on a voting comment are shown separately as «поддержка».
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
POLLS = DATA / "polls.json"
MSK = ZoneInfo("Europe/Moscow")
WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это просто очень вообще давай давайте хочу который которые типо короче блин ещё щас тоже лол xd""".split())
QUESTION = re.compile(r"\?|^(как|почему|зачем|когда|где|кто|что|сколько|а\s+(что|как|где|когда))\b", re.I)
REQUEST = re.compile(r"(продолж|сделай|сделайте|сними|снимите|выпуст|втор(ая|ую) част|част[ьи] ?2|жду|ждём|ждем|хотим|"
                     r"хочу|добавь|добавьте|покажи|покажите|можно\s+(ещё|еще|про)|когда\s+(след|нов|продолж|выйдет))", re.I)
IDEA = re.compile(r"(а что если|а если|предлагаю|идея|было бы (круто|прикольно|смешно)|пусть|надо чтобы|сделайте чтобы)", re.I)


def load_comments():
    return json.loads((DATA / "comments.json").read_text())


def load_polls():
    return json.loads(POLLS.read_text()) if POLLS.exists() else {}


def save_polls(polls):
    tmp = POLLS.with_suffix(".tmp")
    tmp.write_text(json.dumps(polls, ensure_ascii=False, indent=1))
    tmp.replace(POLLS)


def video_id(value, comments):
    value = value.strip()
    for vid, saved in comments.items():
        if value == vid or vid in value or value == saved.get("url"):
            return vid
    raise SystemExit("видео не найдено в comments.json (сборщик видит только свои каналы): " + value)


def words(text):
    return [w.lower().replace("ё", "е") for w in WORD.findall(text)]


def matches(text, stems):
    tokens = words(text)
    return any(t.startswith(s) for s in stems for t in tokens)


def count(poll, saved):
    """Votes of one poll: {option: {"votes", "support"}}, disputed comments, total voters."""
    latest = {}
    for c in sorted(saved["comments"].values(), key=lambda c: c.get("posted") or c["seen"]):
        if c.get("reply"):
            continue
        hit = [name for name, stems in poll["options"].items() if matches(c["text"], stems)]
        if hit:
            latest[c["author"] or c["id"]] = (hit, c)
    result = {name: {"votes": 0, "support": 0} for name in poll["options"]}
    disputed = []
    for hit, c in latest.values():
        if len(hit) == 1:
            result[hit[0]]["votes"] += 1
            result[hit[0]]["support"] += c.get("likes") or 0
        else:
            disputed.append(c)
    return result, disputed, sum(1 for hit, _ in latest.values() if len(hit) == 1)


def poll_text(vid, poll, saved, html=True):
    b = (lambda s: f"<b>{esc(s)}</b>") if html else (lambda s: s)
    result, disputed, total = count(poll, saved)
    lines = [f"🗳 {b(poll['question'])}", f"«{esc(saved.get('title', vid)[:70])}» · {esc(saved.get('account', ''))}"]
    if not total:
        lines.append("Голосов пока нет.")
    for name, r in sorted(result.items(), key=lambda x: (-x[1]["votes"], -x[1]["support"])):
        share = round(100 * r["votes"] / total) if total else 0
        bar = "█" * round(share / 10) + "░" * (10 - round(share / 10))
        lines.append(f"{bar} {esc(name)} — {r['votes']} ({share}%), поддержка ♥{r['support']}")
    if total:
        leader = max(result.items(), key=lambda x: (x[1]["votes"], x[1]["support"]))
        lines.append(f"Лидер: {b(leader[0])} · голосовало {total}")
    if disputed:
        lines.append(f"Спорных (назвали несколько вариантов): {len(disputed)}")
        lines += [f"  · {esc(' '.join(c['text'].split())[:90])}" for c in disputed[:5]]
    return "\n".join(lines)


def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def frequent(saved, top=15):
    stems = Counter()
    for c in saved["comments"].values():
        if c.get("reply"):
            continue
        for w in set(words(c["text"])):
            if len(w) > 2 and w not in STOP and not w.isdigit():
                stems[w[:6]] += 1
    return stems.most_common(top)


def digest(hours, html=True):
    comments, polls = load_comments(), load_polls()
    since = datetime.now(UTC) - timedelta(hours=hours)
    fresh = [(vid, saved, c) for vid, saved in comments.items() for c in saved["comments"].values()
             if datetime.fromisoformat(c["seen"]) > since]
    b = (lambda s: f"<b>{esc(s)}</b>") if html else (lambda s: s)
    lines = [f"💬 {b('Комментарии за ' + str(hours) + ' ч')} — новых {len(fresh)}"]
    per = Counter(saved.get("account", "?") for _, saved, _ in fresh)
    if per:
        lines.append(" · ".join(f"{esc(a)}: {n}" for a, n in per.most_common()))

    def section(title, rows, limit=8):
        if rows:
            lines.append(f"\n{b(title)}")
            for vid, saved, c in sorted(rows, key=lambda r: -(r[2].get("likes") or 0))[:limit]:
                text = " ".join(c["text"].split())[:160]
                like = f" ♥{c['likes']}" if c.get("likes") else ""
                lines.append(f"• {esc(text)}{like} — <i>{esc(saved.get('title', vid)[:35])}</i>" if html
                             else f"• {text}{like} — {saved.get('title', vid)[:35]}")

    used = set()
    pick = (lambda rx: [r for r in fresh if rx.search(r[2]["text"]) and id(r) not in used and not used.add(id(r))])
    section("❓ Вопросы", pick(QUESTION))
    section("🙏 Просьбы", pick(REQUEST))
    section("💡 Идеи", pick(IDEA))
    top = [r for r in fresh if id(r) not in used and (r[2].get("likes") or 0) >= 3]
    section("🔥 Больше всего лайков", top, 5)
    for vid, poll in polls.items():
        if vid in comments and poll.get("active", True):
            lines.append("\n" + poll_text(vid, poll, comments[vid], html))
    if not fresh and not polls:
        lines.append("Новых комментариев нет.")
    return "\n".join(lines)


def send(text):
    sys.path.insert(0, "/opt/frameflow/publisher")
    import publisher  # noqa: E402 — same bot and chat as the publication cards

    chunks, chunk = [], ""
    for line in text.split("\n"):
        if len(chunk) + len(line) > 3800:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\n"
    chunks.append(chunk)
    for part in chunks:
        publisher.telegram("sendMessage", {"chat_id": publisher.config()["chat_id"], "text": part,
                                           "parse_mode": "HTML", "disable_web_page_preview": True})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    poll = sub.add_parser("poll")
    poll.add_argument("action", choices=["add", "list", "show", "remove", "close"])
    poll.add_argument("video", nargs="?")
    poll.add_argument("--question", default="Что выбрали зрители?")
    poll.add_argument("--option", action="append", default=[], help='"Название=основа1,основа2"')
    poll.add_argument("--send", action="store_true")
    w = sub.add_parser("words")
    w.add_argument("video")
    d = sub.add_parser("digest")
    d.add_argument("--hours", type=int, default=24)
    d.add_argument("--send", action="store_true")
    args = p.parse_args()

    comments, polls = load_comments(), load_polls()
    if args.cmd == "words":
        vid = video_id(args.video, comments)
        for stem, n in frequent(comments[vid]):
            print(f"{n:4} {stem}…")
    elif args.cmd == "digest":
        text = digest(args.hours, html=args.send)
        send(text) if args.send else print(text)
    elif args.action == "list":
        for vid, poll_ in polls.items():
            print(vid, "активно" if poll_.get("active", True) else "закрыто", poll_["question"], list(poll_["options"]))
    else:
        vid = video_id(args.video or "", comments)
        if args.action == "add":
            options = {}
            for raw in args.option:
                name, _, stems = raw.partition("=")
                stems = [s.strip().lower().replace("ё", "е") for s in (stems or name).split(",") if s.strip()]
                options[name.strip()] = stems
            if len(options) < 2:
                raise SystemExit("нужно минимум два --option")
            polls[vid] = {"question": args.question, "options": options, "active": True,
                          "created": datetime.now(UTC).isoformat()}
            save_polls(polls)
            print(poll_text(vid, polls[vid], comments[vid], html=False))
        elif args.action == "show":
            text = poll_text(vid, polls[vid], comments[vid], html=args.send)
            send(text) if args.send else print(text)
        elif args.action == "close":
            polls[vid]["active"] = False
            save_polls(polls)
        else:
            polls.pop(vid, None)
            save_polls(polls)


if __name__ == "__main__":
    main()

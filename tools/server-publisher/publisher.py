#!/usr/bin/env python3
"""FrameFlow server publisher: series made on this server → YouTube, only after the owner's button in Telegram.

  publisher.py add VIDEO --channel labs --title "…" [--description …] [--tags a,b] [--when best|now|2026-11-10T18:00]
  publisher.py list                     queue with statuses
  publisher.py approve|now|postpone|cancel ID   the same as the Telegram buttons
  publisher.py tick                     timer (every minute): send requests, apply button decisions, upload
  publisher.py auth start|finish CHANNEL [URL]   (re)connect a channel with the upload right

Nothing is uploaded without approval. An interrupted upload becomes "unknown" and is never repeated
automatically (a repeat could create a duplicate video). Secrets are read from files and never printed.
"""

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
QUEUE = DATA / "queue.json"
DECISIONS = DATA / "decisions"
CONFIG = ROOT / "config.json"
STATS = Path("/opt/frameflow/stats")
MSK = ZoneInfo("Europe/Moscow")
UTC = dt.timezone.utc
LEAD = dt.timedelta(minutes=30)  # an approved slot closer than this is published at once
TG_VIDEO_LIMIT = 49 * 1024 * 1024
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
FINAL = ("scheduled", "published", "cancelled", "failed", "unknown")
STATUS_RU = {
    "new": "🆕 ждёт отправки в Telegram", "asked": "❔ ждёт кнопки", "approved": "⏳ одобрено, загрузка",
    "uploading": "⬆️ загружается", "scheduled": "🕒 загружено, выйдет по расписанию", "published": "✅ опубликовано",
    "cancelled": "✖️ отменено", "failed": "⚠️ ошибка", "unknown": "❓ результат неизвестен",
}


def now():
    return dt.datetime.now(UTC)


def parse_time(value):
    t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def msk(value):
    return parse_time(value).astimezone(MSK).strftime("%d.%m %H:%M")


def config():
    return json.loads(CONFIG.read_text())


def write_private(path, data):
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tmp.replace(path)


def load():
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else {"jobs": []}


def save(queue):
    write_private(QUEUE, queue)


def find(queue, job_id):
    for job in queue["jobs"]:
        if job["id"] == job_id:
            return job
    raise SystemExit(f"нет задания {job_id}")


def log(job, text):
    job.setdefault("log", []).append(f"{now().astimezone(MSK):%d.%m %H:%M:%S} {text}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------- best time (same formula as FrameFlow and the statistics collector) ----------

def channel_history(account):
    """[{"published", "views24"}] of one YouTube channel from the collector history."""
    sys.path.insert(0, str(STATS))
    from best_time import views_at  # noqa: E402
    from collect import history  # noqa: E402

    videos = json.loads((STATS / "data" / "videos.json").read_text())
    points = history(now(), 30 * 24)
    rows = []
    for vid, meta in videos.items():
        if meta.get("platform") != "youtube" or meta.get("account") != account or "T" not in (meta.get("date") or ""):
            continue
        series = [(t.isoformat(), r.get("v")) for t, r in points.get(vid, []) if not r.get("approx")]
        rows.append({"published": meta["date"], "views24": views_at(meta["date"], series) if series else None})
    return rows


def pick_slot(queue, channel, after=None, skip=()):
    """(UTC datetime, reason): the best hour of the channel not closer than 3 h to its other queued videos."""
    sys.path.insert(0, str(STATS))
    from best_time import hour_table, recommend  # noqa: E402

    account = config()["channels"][channel]["stats_account"]
    try:
        videos = channel_history(account)
    except Exception as error:  # statistics are an advice, not a requirement
        videos, note = [], f" (статистика недоступна: {str(error)[:60]})"
    else:
        note = ""
    table = hour_table(videos)
    total = sum(1 for v in videos if v["views24"] is not None)
    start = max(after or now(), now() + LEAD)
    advice = recommend(table, "youtube", len(videos), total, current=start - dt.timedelta(minutes=40))
    ranked = [advice["hour"]] + [h for h, _ in sorted(
        ((h, r) for h, r in table.items() if r["weight"] >= 1.5 and r["n"]), key=lambda x: -x[1]["score"])]
    busy = [parse_time(j["slot"]) for j in queue["jobs"]
            if j["channel"] == channel and j.get("slot") and j["status"] not in ("cancelled", "failed")
            and j["id"] not in skip]
    for day in range(0, 14):
        for hour in dict.fromkeys(ranked):
            local = start.astimezone(MSK).replace(hour=hour, minute=0, second=0, microsecond=0) + dt.timedelta(days=day)
            slot = local.astimezone(UTC)
            if slot < start or any(abs(slot - b) < dt.timedelta(hours=3) for b in busy):
                continue
            reason = advice["reason"] if hour == advice["hour"] else "следующий хороший час канала"
            return slot, reason + note
    return start, "ближайшее время" + note


# ---------- YouTube ----------

def credentials(channel):
    return json.loads(Path(config()["channels"][channel]["credentials"]).read_text())


def access_token(channel):
    c = credentials(channel)
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(c.get("token_uri") or "https://oauth2.googleapis.com/token", body, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"нужно переподключить канал: Google не обновил доступ (HTTP {e.code})") from None
    if UPLOAD_SCOPE not in data.get("scope", "").split():
        raise RuntimeError("у канала нет права загрузки — переподключи: publisher.py auth start " + channel)
    return data["access_token"]


def api(token, url, method="GET", body=None, headers=None, timeout=60):
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Authorization": "Bearer " + token, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return response, (json.loads(raw) if raw else {})


def upload(job):
    """Resumable upload. Returns the video id. Private + publishAt when the slot is in the future."""
    channel = config()["channels"][job["channel"]]
    token = access_token(job["channel"])
    _, mine = api(token, "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true")
    if not mine.get("items") or mine["items"][0]["id"] != channel["channel_id"]:
        raise RuntimeError("доступ открыт не к тому каналу — переподключи")
    status = {"selfDeclaredMadeForKids": False, "embeddable": True}
    slot = parse_time(job["slot"])
    if job.get("immediate") or slot - now() < LEAD:
        status["privacyStatus"] = "public"
    else:
        status.update(privacyStatus="private", publishAt=slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    meta = {"snippet": {"title": job["title"][:100], "description": job.get("description", "")[:5000],
                        "tags": job.get("tags", [])[:30], "categoryId": str(job.get("category") or channel.get("category", "1")),
                        "defaultLanguage": "ru", "defaultAudioLanguage": "ru"},
            "status": status}
    size = os.path.getsize(job["video"])
    response, _ = api(token, "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      "POST", json.dumps(meta).encode(),
                      {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
                       "X-Upload-Content-Length": str(size)})
    location = response.headers["Location"]
    with open(job["video"], "rb") as f:
        _, video = api(token, location, "PUT", f.read(), {"Content-Type": "video/mp4"}, timeout=1800)
    return video["id"], status["privacyStatus"]


# ---------- Telegram (sent with the owner's bot; button presses are handled by cloude-bot) ----------

def bot_token():
    for line in Path(config()["telegram_env"]).read_text().splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("нет токена бота")


def telegram(method, fields, files=None):
    url = f"https://api.telegram.org/bot{bot_token()}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, path in files.items():
            name = urllib.parse.quote(Path(path).name)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                         f'Content-Type: {mimetypes.guess_type(path)[0] or "video/mp4"}\r\n\r\n'.encode())
            parts.append(Path(path).read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(url, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        request = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        description = json.loads(e.read() or b"{}").get("description", "")
        if "message is not modified" in description:
            return None
        raise RuntimeError(f"Telegram: {description[:200]}") from None


def card(job):
    channel = config()["channels"][job["channel"]]["title"]
    text = [f"🎬 <b>{esc(job['title'])}</b>", f"Канал: {esc(channel)} · YouTube",
            f"Когда: <b>{msk(job['slot'])} МСК</b> — {esc(job.get('slot_reason', ''))}"]
    if job.get("description"):
        text.append("\n" + esc(job["description"][:500]))
    if job.get("tags"):
        text.append("Теги: " + esc(", ".join(job["tags"][:15])))
    text.append(f"\n{STATUS_RU.get(job['status'], job['status'])} · #{job['id']}")
    if job.get("url"):
        text.append(job["url"])
    if job.get("error"):
        text.append("⚠️ " + esc(job["error"][:300]))
    return "\n".join(text)[:1000]


def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def buttons(job):
    if job["status"] != "asked":
        return {"inline_keyboard": [[{"text": "▶️ Открыть ролик", "url": job["url"]}]]} if job.get("url") else {"inline_keyboard": []}
    i = job["id"]
    return {"inline_keyboard": [
        [{"text": f"✅ Залить в {msk(job['slot'])[-5:]}", "callback_data": f"ffp:{i}:approve", "style": "success"},
         {"text": "⚡ Сейчас", "callback_data": f"ffp:{i}:now"}],
        [{"text": "⏭ Перенести", "callback_data": f"ffp:{i}:postpone"},
         {"text": "✖️ Отмена", "callback_data": f"ffp:{i}:cancel", "style": "danger"}]]}


def send_card(job):
    fields = {"chat_id": config()["chat_id"], "caption": card(job), "parse_mode": "HTML",
              "reply_markup": json.dumps(buttons(job))}
    if os.path.getsize(job["video"]) <= TG_VIDEO_LIMIT:
        result = telegram("sendVideo", {**fields, "supports_streaming": "true"}, {"video": job["video"]})
    else:
        fields["text"] = fields.pop("caption") + "\n(видео больше 50 МБ — в Telegram не прикреплено)"
        fields["reply_markup"] = json.loads(fields["reply_markup"])
        result = telegram("sendMessage", fields)
    job["message_id"] = result["message_id"]
    job["message_is_text"] = "video" not in result


def refresh_card(job):
    if not job.get("message_id"):
        return
    base = {"chat_id": config()["chat_id"], "message_id": job["message_id"], "parse_mode": "HTML",
            "reply_markup": buttons(job)}
    try:
        if job.get("message_is_text"):
            telegram("editMessageText", {**base, "text": card(job)})
        else:
            telegram("editMessageCaption", {**base, "caption": card(job)})
    except RuntimeError as error:
        log(job, f"карточка не обновлена: {error}")


# ---------- actions ----------

def decide(queue, job, action):
    if job["status"] != "asked":
        return f"#{job['id']}: уже {STATUS_RU.get(job['status'], job['status'])}"
    if action == "approve":
        job["status"] = "approved"
    elif action == "now":
        job.update(status="approved", immediate=True, slot=now().isoformat(), slot_reason="сразу по кнопке")
    elif action == "postpone":
        slot, reason = pick_slot(queue, job["channel"], parse_time(job["slot"]) + dt.timedelta(hours=1), skip={job["id"]})
        job.update(slot=slot.isoformat(), slot_reason=reason)
    elif action == "cancel":
        job["status"] = "cancelled"
    else:
        raise SystemExit("неизвестное действие")
    log(job, f"кнопка: {action}")
    return f"#{job['id']}: {STATUS_RU.get(job['status'])}, {msk(job['slot'])} МСК"


def process(queue):
    changed = False
    for path in sorted(DECISIONS.glob("*.json")):
        try:
            d = json.loads(path.read_text())
            job = next((j for j in queue["jobs"] if j["id"] == d["id"]), None)
            if job:
                decide(queue, job, d["action"])
                refresh_card(job)
                changed = True
        except Exception as error:
            print("decision", path.name, error, file=sys.stderr)
        path.unlink(missing_ok=True)
    for job in queue["jobs"]:
        if job["status"] == "uploading":  # the previous run died during the upload
            job.update(status="unknown", error="загрузка прервалась — проверь канал вручную, повтора не будет")
            log(job, "прервано → unknown")
            refresh_card(job)
            changed = True
        elif job["status"] == "asked" and parse_time(job["slot"]) < now() - dt.timedelta(minutes=5):
            slot, reason = pick_slot(queue, job["channel"], skip={job["id"]})  # nobody pressed in time: next slot
            job.update(slot=slot.isoformat(), slot_reason="время прошло без ответа → " + reason)
            log(job, "слот истёк, перенесено")
            refresh_card(job)
            changed = True
    if changed:
        save(queue)
    for job in queue["jobs"]:
        if job["status"] == "new":
            try:
                job["status"] = "asked"
                send_card(job)
                log(job, "отправлено в Telegram")
            except Exception as error:
                job["status"] = "new"
                log(job, f"Telegram: {error}")
            save(queue)
    for job in queue["jobs"]:
        if job["status"] != "approved":
            continue
        if parse_time(job["slot"]) - now() < LEAD and not job.get("immediate"):
            job["immediate"] = True  # approved too late for scheduling: publish right away
        job["status"] = "uploading"
        log(job, "загрузка начата")
        save(queue)
        refresh_card(job)
        try:
            if job.get("dry_run"):
                video_id, privacy = None, "dry-run"
            else:
                video_id, privacy = upload(job)
        except urllib.error.HTTPError as error:
            body = (error.read() or b"")[:300].decode(errors="replace")
            job.update(status="failed" if error.code < 500 else "unknown", error=f"YouTube HTTP {error.code}: {body}")
        except Exception as error:
            job.update(status="failed" if isinstance(error, RuntimeError) else "unknown", error=str(error)[:300])
        else:
            if privacy == "dry-run":
                job.update(status="cancelled", error="тест кнопок: на YouTube ничего не загружалось")
            else:
                job.update(status="published" if privacy == "public" else "scheduled", video_id=video_id,
                           url=f"https://youtube.com/shorts/{video_id}", uploaded=now().isoformat())
                job.pop("error", None)
        log(job, f"итог: {job['status']}")
        save(queue)
        refresh_card(job)
    for job in queue["jobs"]:
        if job["status"] == "scheduled" and parse_time(job["slot"]) <= now():
            job["status"] = "published"
            log(job, "время публикации наступило")
            save(queue)
            refresh_card(job)


# ---------- OAuth (re)connection ----------

def auth(step, channel, url=None):
    c_conf = config()["channels"][channel]
    creds_path = Path(c_conf["credentials"])
    pending = DATA / f"auth-{channel}.json"
    scopes = [UPLOAD_SCOPE, "https://www.googleapis.com/auth/youtube.readonly",
              "https://www.googleapis.com/auth/yt-analytics.readonly",
              "https://www.googleapis.com/auth/youtube.force-ssl"]
    redirect = "http://localhost:53682/"
    c = json.loads(creds_path.read_text())
    if step == "start":
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(16)
        write_private(pending, {"verifier": verifier, "state": state})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        print("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(scopes),
            "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256"}))
        return
    pend = json.loads(pending.read_text())
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url.strip()).query)
    if q.get("error") or q.get("state", [""])[0] != pend["state"]:
        raise SystemExit("ссылка не подходит — запусти auth start заново")
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"], "code": q["code"][0],
                                   "code_verifier": pend["verifier"], "grant_type": "authorization_code",
                                   "redirect_uri": redirect}).encode()
    with urllib.request.urlopen(c.get("token_uri") or "https://oauth2.googleapis.com/token", body, timeout=30) as r:
        t = json.load(r)
    if "refresh_token" not in t:
        raise SystemExit("Google не выдал refresh_token — запусти auth start заново")
    _, mine = api(t["access_token"], "https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true")
    items = mine.get("items") or []
    if not items or items[0]["id"] != c_conf["channel_id"]:
        raise SystemExit("вход выполнен не в тот канал: " + (items[0]["snippet"]["title"] if items else "канала нет"))
    shutil.copy2(creds_path, creds_path.with_name(creds_path.name + ".bak-" + now().strftime("%Y%m%d-%H%M%S")))
    c.update(token=t["access_token"], refresh_token=t["refresh_token"], scopes=t.get("scope", "").split(),
             expiry=(now() + dt.timedelta(seconds=t.get("expires_in", 3600))).isoformat())
    write_private(creds_path, c)
    pending.unlink()
    print("ok:", items[0]["snippet"]["title"], "| права:", ", ".join(s.rsplit("/", 1)[-1] for s in c["scopes"]))


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("video")
    a.add_argument("--channel", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--description", default="")
    a.add_argument("--tags", default="")
    a.add_argument("--category")
    a.add_argument("--when", default="best")
    a.add_argument("--force", action="store_true", help="разрешить тот же файл на том же канале повторно")
    a.add_argument("--dry-run", action="store_true", help="проверка кнопок: ничего не загружать на YouTube")
    sub.add_parser("list")
    sub.add_parser("tick")
    for action in ("approve", "now", "postpone", "cancel"):
        sub.add_parser(action).add_argument("id")
    au = sub.add_parser("auth")
    au.add_argument("step", choices=["start", "finish"])
    au.add_argument("channel")
    au.add_argument("url", nargs="?")
    args = p.parse_args()

    DECISIONS.mkdir(parents=True, exist_ok=True)
    if args.cmd == "auth":
        return auth(args.step, args.channel, args.url)
    with open(DATA / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queue = load()
        if args.cmd == "add":
            if args.channel not in config()["channels"]:
                raise SystemExit("каналы: " + ", ".join(config()["channels"]))
            video = str(Path(args.video).resolve())
            if not video.lower().endswith(".mp4") or not Path(video).is_file():
                raise SystemExit("нужен существующий .mp4")
            digest = sha256(video)
            dup = [j for j in queue["jobs"] if j["sha256"] == digest and j["channel"] == args.channel
                   and j["status"] not in ("cancelled", "failed")]
            if dup and not args.force:
                raise SystemExit(f"этот файл уже в очереди канала: #{dup[0]['id']} ({dup[0]['status']})")
            if args.when == "best":
                slot, reason = pick_slot(queue, args.channel)
            elif args.when == "now":
                slot, reason = now(), "сразу после кнопки"
            else:
                slot, reason = parse_time(args.when), "время задано вручную"
            job = {"id": secrets.token_hex(3), "created": now().isoformat(), "video": video, "sha256": digest,
                   "channel": args.channel, "title": args.title, "description": args.description,
                   "tags": [t.strip().lstrip("#") for t in args.tags.split(",") if t.strip()],
                   "category": args.category, "dry_run": args.dry_run, "slot": slot.isoformat(), "slot_reason": reason, "status": "new"}
            log(job, "добавлено")
            queue["jobs"].append(job)
            save(queue)
            print(f"#{job['id']} → {config()['channels'][args.channel]['title']}, {msk(job['slot'])} МСК ({reason}). "
                  "Карточка с кнопками придёт в Telegram в течение минуты.")
        elif args.cmd == "list":
            for j in queue["jobs"][-30:]:
                print(f"#{j['id']} {msk(j['slot'])} {j['channel']:8} {STATUS_RU.get(j['status'], j['status'])} "
                      f"«{j['title'][:50]}» {j.get('url', '')} {j.get('error', '')}")
        elif args.cmd == "tick":
            process(queue)
        else:
            job = find(queue, args.id)
            print(decide(queue, job, args.cmd))
            save(queue)
            refresh_card(job)


if __name__ == "__main__":
    main()

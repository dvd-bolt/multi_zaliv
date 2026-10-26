"""Platform boundaries. Network success is never substituted for publication confirmation."""

import logging
import re
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select, update

from . import vault
from .db import Account, Job, Metric, Session, Variant, now, settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
AUTH = {}
AUTH_LOCK = threading.Lock()
AUTH_TICKETS = {}
# The OAuth helper otherwise logs callback URLs (including one-use authorization codes).
logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)


class AccountIdentityError(ValueError):
    """User-facing identity errors generated locally, never remote OAuth payloads."""


def check_rebinding(db, account, external_id):
    same = (
        account.external_id == external_id
        if account.platform == "youtube"
        else (account.external_id or "").casefold() == external_id.casefold()
    )
    if (
        account.external_id
        and not same
        and db.scalar(select(Variant.id).where(Variant.account_id == account.id).limit(1))
    ):
        raise AccountIdentityError(
            "Р­С‚РѕС‚ Р°РєРєР°СѓРЅС‚ СѓР¶Рµ СЃРІСЏР·Р°РЅ СЃ Р·Р°РґР°РЅРёСЏРјРё РґСЂСѓРіРѕРіРѕ РєР°РЅР°Р»Р°. Р”РѕР±Р°РІСЊС‚Рµ РЅРѕРІС‹Р№ РєР°РЅР°Р» РѕС‚РґРµР»СЊРЅРѕР№ Р·Р°РїРёСЃСЊСЋ."
        )


@contextmanager
def cancelling_auth(key):
    with AUTH_LOCK:
        attempt = AUTH_TICKETS.pop(key, None)
        if callable(getattr(attempt, "dispose", None)):
            attempt.dispose()
        AUTH[key] = "disconnected"
        yield


def auth_result(key, ticket, status):
    with AUTH_LOCK:
        if AUTH_TICKETS.get(key) is ticket:
            AUTH[key] = status


def youtube_service(account):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = vault.get(account.id)
    if not token:
        raise ValueError("РџРѕРґРєР»СЋС‡РёС‚Рµ YouTube С‡РµСЂРµР· Google OAuth")
    credentials = Credentials.from_authorized_user_info(token, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        import json

        vault.put(account.id, json.loads(credentials.to_json()))
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def begin_youtube(account_id):
    from .tiktok_login import pending

    client = vault.get("google_client")
    if not client:
        raise ValueError(
            "РЎРЅР°С‡Р°Р»Р° РёРјРїРѕСЂС‚РёСЂСѓР№С‚Рµ OAuth JSON РїСЂРёР»РѕР¶РµРЅРёСЏ Desktop РІ РЅР°СЃС‚СЂРѕР№РєР°С…"
        )
    with AUTH_LOCK:
        if any(pending(s) for s in AUTH.values()):
            raise ValueError("Р—Р°РІРµСЂС€РёС‚Рµ С‚РµРєСѓС‰РёР№ РІС…РѕРґ РІ Р±СЂР°СѓР·РµСЂРµ")
        AUTH[account_id] = "waiting"
        ticket = object()
        AUTH_TICKETS[account_id] = ticket

    def login():
        try:
            import json

            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_config(client, SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                timeout_seconds=180,
                authorization_prompt_message="",
                success_message="SurvivalFlow: РІС…РѕРґ Р·Р°РІРµСЂС€С‘РЅ. РњРѕР¶РЅРѕ Р·Р°РєСЂС‹С‚СЊ СЌС‚Сѓ РІРєР»Р°РґРєСѓ.",
            )
            from googleapiclient.discovery import build

            service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
            if len(items) != 1:
                raise ValueError(
                    "РќРµ СѓРґР°Р»РѕСЃСЊ РѕРґРЅРѕР·РЅР°С‡РЅРѕ РѕРїСЂРµРґРµР»РёС‚СЊ YouTube-РєР°РЅР°Р»"
                )
            with AUTH_LOCK, Session.begin() as db:
                if AUTH_TICKETS.get(account_id) is not ticket:
                    return
                account = db.get(Account, account_id)
                check_rebinding(db, account, items[0]["id"])
                duplicate = db.scalar(
                    select(Account).where(Account.external_id == items[0]["id"], Account.id != account_id)
                )
                if duplicate:
                    raise AccountIdentityError(
                        "Р­С‚РѕС‚ РєР°РЅР°Р» СѓР¶Рµ РїРѕРґРєР»СЋС‡С‘РЅ РІ РґСЂСѓРіРѕР№ Р·Р°РїРёСЃРё"
                    )
                vault.put(account_id, json.loads(credentials.to_json()))
                account.name = items[0]["snippet"]["title"]
                account.external_id, account.connected, account.method = items[0]["id"], True, "oauth"
                AUTH[account_id] = "connected"
        except AccountIdentityError as error:
            auth_result(account_id, ticket, "error: " + str(error))
        except Exception:
            # OAuth exceptions may contain tokens/redirect URLs. Do not expose them in UI or logs.
            auth_result(
                account_id,
                ticket,
                "error: Р’С…РѕРґ РЅРµ Р·Р°РІРµСЂС€С‘РЅ. РџСЂРѕРІРµСЂСЊС‚Рµ OAuth Desktop, С‚РµСЃС‚РѕРІС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ Рё РґРѕСЃС‚СѓРї Рє РєР°РЅР°Р»Сѓ.",
            )

    thread = threading.Thread(target=login, daemon=True)
    thread.start()
    return thread


def import_tiktok(account, payload):
    if not isinstance(payload.get("handle", ""), str):
        raise ValueError("РЈРєР°Р¶РёС‚Рµ РёРјСЏ TikTok-Р°РєРєР°СѓРЅС‚Р° СЃС‚СЂРѕРєРѕР№")
    handle = payload.get("handle", "").lstrip("@")
    cookies = payload.get("cookies")
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", handle):
        raise ValueError("РЈРєР°Р¶РёС‚Рµ РёРјСЏ TikTok-Р°РєРєР°СѓРЅС‚Р° Р±РµР· СЃСЃС‹Р»РєРё")
    if (
        not isinstance(cookies, list)
        or not all(isinstance(c, dict) for c in cookies)
        or not any(c.get("name") == "sessionid" for c in cookies)
    ):
        raise ValueError("РќСѓР¶РµРЅ СЌРєСЃРїРѕСЂС‚ cookies СЃ sessionid РёР· РІР°С€РµРіРѕ Р±СЂР°СѓР·РµСЂР°")
    allowed = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
    cleaned = [
        {k: v for k, v in cookie.items() if k in allowed}
        for cookie in cookies
        if str(cookie.get("domain", "")).lstrip(".") in ("tiktok.com", "www.tiktok.com")
    ]
    if not any(c.get("name") == "sessionid" for c in cleaned):
        raise ValueError("РЎРµСЃСЃРёСЏ РґРѕР»Р¶РЅР° РїСЂРёРЅР°РґР»РµР¶Р°С‚СЊ tiktok.com")
    with Session() as db:
        check_rebinding(db, account, handle)
    vault.put(account.id, {"cookies": cleaned, "handle": handle})
    # Browser cookies alone do not prove channel identity; require a separate explicit check in UI.
    account.external_id, account.name = handle, "@" + handle
    account.method, account.connected = "browser_unverified", False


def dispatch_one(db):
    jobs = db.scalars(
        select(Job)
        .where(Job.status.in_(["scheduled", "waiting_auth", "platform_processing"]))
        .order_by(Job.created)
    ).all()
    for job in jobs:
        variant = db.get(Variant, job.variant_id)
        account = db.get(Account, variant.account_id)
        if job.status == "platform_processing":
            if datetime.now(UTC) - datetime.fromisoformat(job.updated) < timedelta(seconds=60):
                continue
            if account.platform == "tiktok":
                from .tiktok_public import available

                if available(account.external_id, job.external_id):
                    job.status, job.error, job.updated = "published", None, now()
                else:
                    job.updated = now()
            elif account.platform == "youtube":
                try:
                    items = (
                        youtube_service(account)
                        .videos()
                        .list(part="status,processingDetails", id=job.external_id)
                        .execute()
                        .get("items", [])
                    )
                    if items:
                        state = items[0]["status"].get("uploadStatus")
                        if state == "processed":
                            job.status, job.error = "published", None
                        elif state in ("rejected", "failed", "deleted"):
                            job.status, job.error = (
                                "rejected",
                                "РџР»Р°С‚С„РѕСЂРјР° РѕС‚РєР»РѕРЅРёР»Р° СЂРѕР»РёРє: " + state,
                            )
                        job.updated = now()
                    else:
                        job.error = "РџР»Р°С‚С„РѕСЂРјР° РїРѕРєР° РЅРµ РІРµСЂРЅСѓР»Р° СЂРѕР»РёРє. РџСЂРѕРІРµСЂРёРј РїРѕР·Р¶Рµ Р±РµР· РїРѕРІС‚РѕСЂРЅРѕР№ РѕС‚РїСЂР°РІРєРё."
                        job.updated = now()
                except Exception:
                    job.error = "РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕРІРµСЂРёС‚СЊ РѕР±СЂР°Р±РѕС‚РєСѓ. РџРѕРІС‚РѕСЂРёРј РїСЂРѕРІРµСЂРєСѓ Р±РµР· РїРѕРІС‚РѕСЂРЅРѕР№ Р·Р°РіСЂСѓР·РєРё."
                    job.updated = now()
            db.commit()
            continue
        if not job.approved:
            continue
        if not account.connected:
            job.status = "waiting_auth"
            continue
        if (
            account.platform == "tiktok"
            and account.method != "browser_verified"
            and not job.validation_upload
        ):
            job.status = "waiting_auth"
            job.error = "Р’С…РѕРґ РІ TikTok СЃРѕС…СЂР°РЅС‘РЅ. РђРІС‚РѕРїСѓР±Р»РёРєР°С†РёСЏ РµС‰С‘ С‚СЂРµР±СѓРµС‚ РїСЂРѕРІРµСЂРєРё РёРЅС‚РµРіСЂР°С†РёРё."
            continue
        if job.scheduled:
            date = datetime.fromisoformat(job.scheduled)
            current = datetime.now(UTC)
            if date > current:
                continue
            if current - date > timedelta(minutes=10):
                job.status, job.error = (
                    "missed",
                    "Р’СЂРµРјСЏ РїСЂРѕРїСѓС‰РµРЅРѕ. РџРѕРґС‚РІРµСЂРґРёС‚Рµ РЅРѕРІС‹Р№ Р·Р°РїСѓСЃРє.",
                )
                continue
        interval = settings(db)["interval"]
        last = db.scalar(
            select(Job)
            .join(Variant)
            .where(
                Variant.account_id == account.id,
                Job.status.in_(["published", "platform_processing", "unknown"]),
            )
            .order_by(func.coalesce(Job.upload_started_at, Job.updated).desc())
        )
        if last and datetime.now(UTC) - datetime.fromisoformat(
            last.upload_started_at or last.updated
        ) < timedelta(minutes=interval):
            continue
        if not variant.output or not Path(variant.output).exists():
            job.status, job.error = (
                "failed",
                "Р­РєСЃРїРѕСЂС‚ РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚; С‚СЂРµР±СѓРµС‚СЃСЏ РїРѕРІС‚РѕСЂРЅР°СЏ РѕР±СЂР°Р±РѕС‚РєР°",
            )
            return
        from .buba import require_ready

        try:
            require_ready(db, job)
        except ValueError as error:
            job.status, job.approved, job.error = "ready", False, str(error)
            db.commit()
            return
        claimed = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == job.status, Job.approved.is_(True))
            .values(status="uploading", updated=now(), upload_started_at=now())
        )
        if not claimed.rowcount:
            db.rollback()
            continue
        db.commit()  # Persist uncertainty BEFORE touching a remote service.
        try:
            if account.platform == "youtube":
                from googleapiclient.http import MediaFileUpload

                config = variant.config
                service = youtube_service(account)
                identity = service.channels().list(part="id", mine=True).execute()["items"]
                if not any(item["id"] == account.external_id for item in identity):
                    raise ValueError("РђРІС‚РѕСЂРёР·РѕРІР°РЅ РґСЂСѓРіРѕР№ РєР°РЅР°Р»")
                request = service.videos().insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": config["title"] or "Р’РёРґРµРѕ",
                            "description": config["caption"],
                            "categoryId": "22",
                        },
                        "status": {
                            "privacyStatus": config["visibility"],
                            "selfDeclaredMadeForKids": config["made_for_kids"],
                            "containsSyntheticMedia": config["synthetic"],
                        },
                    },
                    media_body=MediaFileUpload(variant.output, chunksize=8 * 1024 * 1024, resumable=True),
                )
                response = None
                while response is None:
                    _, response = request.next_chunk(num_retries=0)
                external_id = response.get("id")
                if not external_id:
                    raise ValueError("РџР»Р°С‚С„РѕСЂРјР° РЅРµ РІРµСЂРЅСѓР»Р° ID")
                job.external_id, job.url = external_id, "https://www.youtube.com/watch?v=" + external_id
                job.status = "platform_processing"
            else:
                from .tiktok_upload import BeforePublishError, upload

                expected = account.external_id

                def checkpoint(job=job, account=account, expected=expected):
                    db.expire_all()
                    if (
                        job.status != "uploading"
                        or not job.approved
                        or not account.connected
                        or not account.enabled
                        or account.external_id != expected
                    ):
                        raise BeforePublishError(
                            "Р—Р°РґР°РЅРёРµ РёР»Рё РїРѕРґРєР»СЋС‡РµРЅРёРµ РёР·РјРµРЅРёР»РѕСЃСЊ; РїСѓР±Р»РёРєР°С†РёСЏ РѕСЃС‚Р°РЅРѕРІР»РµРЅР°",
                            auth=True,
                        )
                    job.upload_started_at, job.updated = now(), now()
                    db.commit()

                try:
                    result = upload(
                        vault.get(account.id),
                        variant.output,
                        variant.config,
                        expected,
                        before_post=checkpoint,
                    )
                except BeforePublishError as error:
                    # Adapter proves it never clicked publish. Explicit reapproval is required.
                    job.status = "waiting_auth" if error.auth else "ready"
                    job.approved, job.validation_upload, job.upload_started_at = False, False, None
                    job.error = str(error)
                    if error.auth:
                        account.connected, account.method = False, "browser_login"
                    job.updated = now()
                    db.commit()
                    return
                duplicate = db.scalar(
                    select(Job.id)
                    .join(Variant)
                    .where(
                        Variant.account_id == account.id,
                        Job.external_id == result.external_id,
                        Job.id != job.id,
                    )
                )
                if duplicate:
                    raise ValueError(
                        "ID СѓР¶Рµ СѓС‡С‚С‘РЅ Сѓ РґСЂСѓРіРѕРіРѕ Р·Р°РґР°РЅРёСЏ; С‚СЂРµР±СѓРµС‚СЃСЏ СЂСѓС‡РЅР°СЏ РїСЂРѕРІРµСЂРєР°"
                    )
                job.external_id, job.url, job.status = result.external_id, result.url, result.status
                job.error = "TikTok РїСЂРёРЅСЏР» РїСѓР±Р»РёРєР°С†РёСЋ Рё РІРµСЂРЅСѓР» ID. РћР±СЂР°Р±РѕС‚РєР° Рё С„Р°РєС‚РёС‡РµСЃРєР°СЏ РІРёРґРёРјРѕСЃС‚СЊ РїРѕРєР° РЅРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅС‹."
                account.method = "browser_verified"
        except Exception:
            # Never retry automatically after a possibly completed remote write.
            job.status = "unknown"
            job.error = "Р РµР·СѓР»СЊС‚Р°С‚ РѕС‚РїСЂР°РІРєРё РЅРµРёР·РІРµСЃС‚РµРЅ. РџСЂРѕРІРµСЂСЊС‚Рµ РєР°РЅР°Р» РІСЂСѓС‡РЅСѓСЋ РїРµСЂРµРґ РґР°Р»СЊРЅРµР№С€РёРјРё РґРµР№СЃС‚РІРёСЏРјРё."
        job.updated = now()
        db.commit()
        return


def refresh_metrics(db):
    result = {"updated": 0, "unavailable": 0}
    with db.no_autoflush:
        for job in db.scalars(select(Job).where(Job.external_id.is_not(None))).all():
            account = db.get(Account, db.get(Variant, job.variant_id).account_id)
            try:
                if account.platform == "youtube":
                    items = (
                        youtube_service(account)
                        .videos()
                        .list(part="statistics", id=job.external_id)
                        .execute()["items"]
                    )
                    if not items:
                        raise ValueError("РќРµС‚ РґР°РЅРЅС‹С…")
                    stats = items[0]["statistics"]
                    values = {
                        k: int(stats[remote]) if remote in stats else None
                        for k, remote in (
                            ("views", "viewCount"),
                            ("likes", "likeCount"),
                            ("comments", "commentCount"),
                        )
                    }
                    source = "YouTube Data API"
                else:
                    token = vault.get(account.id + "_metrics")
                    if not token:
                        from .tiktok_public import metrics

                        values = metrics(account.external_id, job.external_id)
                        source = "TikTok public page"
                    else:
                        response = httpx.post(
                            "https://open.tiktokapis.com/v2/video/query/",
                            params={"fields": "id,view_count,like_count,comment_count,share_count"},
                            headers={"Authorization": "Bearer " + token["access_token"]},
                            json={"filters": {"video_ids": [job.external_id]}},
                            timeout=20,
                        )
                        response.raise_for_status()
                        videos = response.json().get("data", {}).get("videos", [])
                        if len(videos) != 1 or videos[0]["id"] != job.external_id:
                            raise ValueError("РќРµС‚ РґР°РЅРЅС‹С…")
                        values = {
                            key: videos[0].get(remote)
                            for key, remote in (
                                ("views", "view_count"),
                                ("likes", "like_count"),
                                ("comments", "comment_count"),
                                ("shares", "share_count"),
                            )
                        }
                        source = "TikTok Display API"
                db.add(Metric(job_id=job.id, values=values, source=source))
                result["updated"] += 1
            except Exception:
                result["unavailable"] += 1
    return result

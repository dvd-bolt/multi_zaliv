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
from .db import Account, Job, Metric, Session, Setting, Variant, now, settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
# Posting the author's comment. Requested at new sign-ins only: refreshing an old token with a scope it
# was never granted fails with invalid_scope and would stop uploads of every existing channel.
COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
LOGIN_SCOPES = [*SCOPES, COMMENT_SCOPE]


def granted_scopes(token):
    return list((token or {}).get("scopes") or SCOPES)


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
            "Этот аккаунт уже связан с заданиями другого канала. Добавьте новый канал отдельной записью."
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
        raise AccountIdentityError("Подключите YouTube через Google OAuth")
    credentials = Credentials.from_authorized_user_info(token, granted_scopes(token))
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        import json

        vault.put(account.id, json.loads(credentials.to_json()))
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def youtube_preflight_failure(error):
    """Safe diagnostics only for errors proven to occur before videos.insert is executed."""
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError

    if isinstance(error, RefreshError):
        return "Сохранённый вход Google истёк или отозван. Подключите YouTube заново.", True
    if isinstance(error, AccountIdentityError):
        return str(error), True
    if isinstance(error, HttpError):
        import json

        try:
            reasons = {e.get("reason") for e in json.loads(error.content).get("error", {}).get("errors", [])}
        except (ValueError, TypeError, AttributeError):
            reasons = set()
        if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
            return "YouTube Data API отклонил проверку доступа: исчерпана квота. Повторите позже.", False
        if error.resp.status in (401, 403):
            return "YouTube отклонил доступ. Подключите канал заново и проверьте разрешения Google.", True
    return "Не удалось подготовить отправку YouTube. Проверьте подключение, сеть и доступность MP4.", False


def begin_youtube(account_id):
    from .tiktok_login import pending

    client = vault.get("google_client")
    if not client:
        raise ValueError("Сначала импортируйте OAuth JSON приложения Desktop в настройках")
    with AUTH_LOCK:
        if any(pending(s) for s in AUTH.values()):
            raise ValueError("Завершите текущий вход в браузере")
        AUTH[account_id] = "waiting"
        ticket = object()
        AUTH_TICKETS[account_id] = ticket

    def login():
        try:
            import json
            import os

            from google_auth_oauthlib.flow import InstalledAppFlow

            # Google lets the user untick optional permissions; without this a declined comment
            # permission would fail the whole sign-in instead of only disabling author comments.
            os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
            flow = InstalledAppFlow.from_client_config(client, LOGIN_SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                timeout_seconds=180,
                authorization_prompt_message="",
                success_message="FrameFlow: вход завершён. Можно закрыть эту вкладку.",
            )
            from googleapiclient.discovery import build

            service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
            if len(items) != 1:
                raise ValueError("Не удалось однозначно определить YouTube-канал")
            with AUTH_LOCK, Session.begin() as db:
                if AUTH_TICKETS.get(account_id) is not ticket:
                    return
                account = db.get(Account, account_id)
                check_rebinding(db, account, items[0]["id"])
                duplicate = db.scalar(
                    select(Account).where(Account.external_id == items[0]["id"], Account.id != account_id)
                )
                if duplicate:
                    raise AccountIdentityError("Этот канал уже подключён в другой записи")
                saved = json.loads(credentials.to_json())
                if getattr(credentials, "granted_scopes", None):
                    saved["scopes"] = list(credentials.granted_scopes)
                vault.put(account_id, saved)
                account.name = items[0]["snippet"]["title"]
                account.external_id, account.connected, account.method = items[0]["id"], True, "oauth"
                from .preflight import forget

                forget(db, account_id)
                AUTH[account_id] = "connected"
        except AccountIdentityError as error:
            auth_result(account_id, ticket, "error: " + str(error))
        except Exception:
            # OAuth exceptions may contain tokens/redirect URLs. Do not expose them in UI or logs.
            auth_result(
                account_id,
                ticket,
                "error: Вход не завершён. Проверьте OAuth Desktop, тестовых пользователей и доступ к каналу.",
            )

    thread = threading.Thread(target=login, daemon=True)
    thread.start()
    return thread


def import_tiktok(account, payload):
    if not isinstance(payload.get("handle", ""), str):
        raise ValueError("Укажите имя TikTok-аккаунта строкой")
    handle = payload.get("handle", "").lstrip("@")
    cookies = payload.get("cookies")
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", handle):
        raise ValueError("Укажите имя TikTok-аккаунта без ссылки")
    if (
        not isinstance(cookies, list)
        or not all(isinstance(c, dict) for c in cookies)
        or not any(c.get("name") == "sessionid" for c in cookies)
    ):
        raise ValueError("Нужен экспорт cookies с sessionid из вашего браузера")
    allowed = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
    cleaned = [
        {k: v for k, v in cookie.items() if k in allowed}
        for cookie in cookies
        if str(cookie.get("domain", "")).lstrip(".") in ("tiktok.com", "www.tiktok.com")
    ]
    if not any(c.get("name") == "sessionid" for c in cleaned):
        raise ValueError("Сессия должна принадлежать tiktok.com")
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
                                "Платформа отклонила ролик: " + state,
                            )
                        job.updated = now()
                    else:
                        job.error = "Платформа пока не вернула ролик. Проверим позже без повторной отправки."
                        job.updated = now()
                except Exception:
                    job.error = "Не удалось проверить обработку. Повторим проверку без повторной загрузки."
                    job.updated = now()
            db.commit()
            continue
        if not job.approved:
            continue
        if not account.enabled:
            job.status = "waiting_auth"
            job.error = "Канал выключен. Включите его для продолжения автозалива."
            continue
        if not account.connected:
            job.status = "waiting_auth"
            continue
        from .preflight import blocked

        if blocked(db, account):
            # A read-only check proved the sign-in unusable; never start an upload into it.
            if job.status != "waiting_auth":
                job.status, job.updated = "waiting_auth", now()
                job.error = "Проверка перед публикацией не подтвердила вход. Ролик не отправлялся."
            continue
        if (
            account.platform == "tiktok"
            and account.method != "browser_verified"
            and not job.validation_upload
        ):
            job.status = "waiting_auth"
            job.error = "Вход в TikTok сохранён. Автопубликация ещё требует проверки интеграции."
            continue
        if job.scheduled:
            date = datetime.fromisoformat(job.scheduled)
            current = datetime.now(UTC)
            if date > current:
                continue
            if current - date > timedelta(minutes=10) and not job.auto_publish:
                job.status, job.error = (
                    "missed",
                    "Время пропущено. Подтвердите новый запуск.",
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
                "Экспорт отсутствует; требуется повторная обработка",
            )
            return
        from .buba import require_ready

        try:
            require_ready(db, job)
        except ValueError as error:
            job.status, job.approved, job.error = "ready", False, str(error)
            db.commit()
            return
        # Upload metadata is prepared before the remote write: an error here must not become "unknown".
        from .db import Asset
        from .hashtags import title_from, youtube_tags

        source = db.get(Asset, variant.asset_id)
        youtube_title = (variant.config.get("title") or "").strip() or title_from(
            variant.config.get("caption", ""), source.name if source else ""
        )
        youtube_category = str(settings(db).get("youtube_category") or "1")
        if account.platform == "youtube" and not youtube_title:
            job.status, job.approved = "ready", False
            job.error = "Укажите название ролика для YouTube"
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
        youtube_write_started = False
        try:
            if account.platform == "youtube":
                from googleapiclient.http import MediaFileUpload

                config = variant.config
                service = youtube_service(account)
                identity = service.channels().list(part="id", mine=True).execute()["items"]
                if not any(item["id"] == account.external_id for item in identity):
                    raise AccountIdentityError("Авторизован другой канал. Подключите нужный YouTube-канал.")
                request = service.videos().insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": youtube_title,
                            "description": config["caption"],
                            "categoryId": youtube_category,
                            "tags": youtube_tags(
                                config["caption"], config.get("genre"), config.get("title", "")
                            ),
                            "defaultLanguage": "ru",
                            "defaultAudioLanguage": "ru",
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
                youtube_write_started = True
                while response is None:
                    _, response = request.next_chunk(num_retries=0)
                external_id = response.get("id")
                if not external_id:
                    raise ValueError("Платформа не вернула ID")
                job.external_id, job.url = external_id, "https://www.youtube.com/watch?v=" + external_id
                job.status = "platform_processing"
                job.updated = now()
                db.commit()  # Save the video result before attempting the independent thumbnail write.
                if settings(db).get("youtube_thumbnail"):
                    from .youtube_covers import generate_and_attach

                    generate_and_attach(db, job, variant, service)
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
                            "Задание или подключение изменилось; публикация остановлена",
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
                        sound=((job.extras or {}).get("sound") or {}).get("name"),
                        copyright=settings(db).get("tiktok_copyright", "warn"),
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
                    raise ValueError("ID уже учтён у другого задания; требуется ручная проверка")
                job.external_id, job.url, job.status = result.external_id, result.url, result.status
                if result.checks:
                    job.extras = {**(job.extras or {}), "tiktok_checks": result.checks}
                job.error = "TikTok принял публикацию и вернул ID. Обработка и фактическая видимость пока не подтверждены."
                account.method = "browser_verified"
        except Exception as error:
            if account.platform == "youtube" and not youtube_write_started:
                detail, needs_auth = youtube_preflight_failure(error)
                job.status = "waiting_auth" if needs_auth else "ready"
                job.approved, job.upload_started_at = False, None
                job.error = detail + " Отправка видео не начиналась. После исправления подтвердите отправку."
                if needs_auth:
                    account.connected = False
            else:
                # Never retry automatically after a possibly completed remote write.
                job.status = "unknown"
                job.error = (
                    "Результат отправки неизвестен. Проверьте канал вручную перед дальнейшими действиями."
                )
        job.updated = now()
        db.commit()
        return


def refresh_metrics(db, account_ids=None):
    result = {"updated": 0, "unavailable": 0}
    from .youtube_library import sync

    for account in db.scalars(
        select(Account).where(Account.platform == "youtube", Account.connected.is_(True))
    ).all():
        if account_ids is not None and account.id not in account_ids:
            continue
        try:
            result["updated"] += sync(db, account)
        except Exception:
            result["unavailable"] += 1
            key = "youtube_sync_" + account.id
            store = db.get(Setting, key)
            value = {
                **(store.value if store else {}),
                "error": "Не удалось обновить канал. Проверьте подключение YouTube и квоту API",
            }
            if store:
                store.value = value
            else:
                db.add(Setting(key=key, value=value))
    with db.no_autoflush:
        for job in db.scalars(select(Job).where(Job.external_id.is_not(None))).all():
            account = db.get(Account, db.get(Variant, job.variant_id).account_id)
            if account.platform == "youtube" or (account_ids is not None and account.id not in account_ids):
                continue
            try:
                token = vault.get(account.id + "_metrics")
                if not token:
                    from .tiktok_public import fetch_item, item_metrics

                    item = fetch_item(account.external_id, job.external_id)
                    if not item:
                        raise ValueError("Публичная статистика TikTok недоступна")
                    values = item_metrics(item)
                    publication_time = item.get("createTime")
                    source = "TikTok public page"
                else:
                    response = httpx.post(
                        "https://open.tiktokapis.com/v2/video/query/",
                        params={"fields": "id,create_time,view_count,like_count,comment_count,share_count"},
                        headers={"Authorization": "Bearer " + token["access_token"]},
                        json={"filters": {"video_ids": [job.external_id]}},
                        timeout=20,
                    )
                    response.raise_for_status()
                    videos = response.json().get("data", {}).get("videos", [])
                    if len(videos) != 1 or videos[0]["id"] != job.external_id:
                        raise ValueError("Нет данных")
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
                    publication_time = videos[0].get("create_time")
                captured = now()
                db.add(Metric(job_id=job.id, values=values, source=source, captured=captured))
                from .analytics import record

                confirmed = None
                if (type(publication_time) is int or
                        isinstance(publication_time, str) and publication_time.isdigit()):
                    try:
                        confirmed = datetime.fromtimestamp(int(publication_time), UTC).isoformat()
                    except (ValueError, OverflowError, OSError):
                        pass
                record(db, account.id, job.external_id, confirmed, source + " create_time",
                       [{"captured": captured, "values": values, "source": source}])
                result["updated"] += 1
            except Exception:
                result["unavailable"] += 1
    store = db.get(Setting, "metrics_refresh")
    if store:
        store.value = result.copy()
    else:
        db.add(Setting(key="metrics_refresh", value=result.copy()))
    return result

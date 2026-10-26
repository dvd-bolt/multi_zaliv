import csv
import hashlib
import io
import os
import shutil
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import vault
from .browser_pairing import (
    BRIDGE_PATHS,
    begin_browser,
    check_pairing,
    confirm_browser,
    extension_origin,
    is_browser_pairing,
    pairing_details,
    submit_pairing,
)
from .config import DATA, PORT, ROOT, binary
from .db import (
    Account,
    Asset,
    BubaSubmission,
    Comment,
    Job,
    Metric,
    Session,
    Setting,
    Variant,
    now,
    public_account,
    settings,
    uid,
)
from .maintenance import backup_database, cleanup_exports
from .media import builtin_banner, inspect
from .platforms import AUTH, begin_youtube, cancelling_auth, import_tiktok, refresh_metrics
from .queue import action, enqueue
from .schemas import (
    AccountCreate,
    BatchApproval,
    BubaCheck,
    BubaTracking,
    CommentReview,
    Edit,
    Enqueue,
    JobAction,
    SettingsUpdate,
    TikTokConfirmation,
)
from .tiktok_login import begin_tiktok, confirm_login, return_to_tiktok

app = FastAPI(title="SurvivalFlow", docs_url="/api/docs", redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def protect_local_api(request: Request, call_next):
    origin = request.headers.get("origin")
    bridge = request.url.path in BRIDGE_PATHS
    if bridge:
        if not extension_origin(origin) or request.method not in ("POST", "OPTIONS"):
            return JSONResponse({"detail": "Нужен запрос расширения SurvivalFlow"}, status_code=403)
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        elif len(await request.body()) > 262144:
            response = JSONResponse({"detail": "Слишком большой запрос расширения"}, status_code=413)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type",
                "Vary": "Origin",
                "Cache-Control": "no-store",
            }
        )
        return response
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port not in (PORT, 5173):
            return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path in ("/", "/index.html") or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid(_, error):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(RuntimeError)
async def unavailable(_, error):
    return JSONResponse({"detail": str(error)}, status_code=503)


def require(db, model, key):
    item = db.get(model, key)
    if item is None:
        raise HTTPException(404, "Объект не найден")
    return item


def asset_data(asset):
    return {k: getattr(asset, k) for k in ("id", "kind", "name", "media", "edit", "created")}


def job_data(db, job):
    variant = db.get(Variant, job.variant_id)
    account = db.get(Account, variant.account_id)
    asset = db.get(Asset, variant.asset_id)
    return {
        **{
            k: getattr(job, k)
            for k in (
                "id",
                "status",
                "progress",
                "scheduled",
                "approved",
                "validation_upload",
                "attempts",
                "error",
                "external_id",
                "url",
                "created",
                "updated",
            )
        },
        "account": public_account(account),
        "asset_name": asset.name,
        "asset_id": asset.id,
        "config": variant.config,
        "has_output": bool(variant.output and Path(variant.output).exists()),
    }


@app.get("/api/state")
def state():
    with Session() as db:
        heartbeat = None
        try:
            heartbeat = datetime.fromtimestamp((DATA / "worker.heartbeat").stat().st_mtime, UTC).isoformat()
        except (OSError, ValueError, KeyError):
            pass
        alive = bool(
            heartbeat and (datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds() < 15
        )
        return {
            "assets": [
                asset_data(a)
                for a in db.scalars(
                    select(Asset).where(Asset.archived.is_(False)).order_by(Asset.created)
                ).all()
            ],
            "accounts": [
                {**public_account(a), "auth": AUTH.get(a.id)} for a in db.scalars(select(Account)).all()
            ],
            "jobs": [job_data(db, j) for j in db.scalars(select(Job).order_by(Job.created.desc())).all()],
            "settings": settings(db),
            "worker": alive,
            "heartbeat": heartbeat,
            "google_configured": bool(vault.get("google_client")),
        }


@app.post("/api/buba/check")
def buba_check(request: BubaCheck):
    from .buba import report

    with Session() as db:
        source = require(db, Asset, request.asset_id)
        if source.kind != "video":
            raise ValueError("Выберите исходное видео")
        config = request.edit.effective(source.media["duration"])
        banner = require(db, Asset, request.edit.banner_id) if request.edit.banner_id else None
        return report(source.media, banner.media if banner else None, config)


@app.get("/api/jobs/{key}/buba")
def buba_job(key: str):
    from .buba import job_report, payout_status

    with Session() as db:
        job = require(db, Job, key)
        return {"checks": job_report(db, job), "payout": payout_status(db, job)}


@app.post("/api/jobs/{key}/buba")
def track_buba(key: str, request: BubaTracking):
    from .buba import payout_status

    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = require(db, Job, key)
        variant = require(db, Variant, job.variant_id)
        source = require(db, Asset, variant.asset_id)
        if (
            not request.public_confirmed
            or not job.external_id
            or variant.config.get("visibility") != "public"
        ):
            raise ValueError("Подтвердите публичность опубликованного ролика с внешним ID")
        if request.published_at > datetime.now(UTC):
            raise ValueError("Дата публикации не может быть в будущем")
        record = db.get(BubaSubmission, key)
        if record and record.state in ("submitted", "paid", "rejected"):
            if request.state not in ("paid", "rejected") or record.state in ("paid", "rejected"):
                raise ValueError("Повторная подача/снятие отметки уже поданной заявки запрещены")
            if request.published_at != datetime.fromisoformat(record.published_at):
                raise ValueError("Нельзя менять дату уже поданной заявки")
        if not record:
            record = BubaSubmission(
                job_id=key, source_sha=None, published_at=request.published_at.isoformat(), state="tracking"
            )
            db.add(record)
        record.published_at = request.published_at.isoformat()
        db.flush()
        if request.state == "submitted":
            other = db.scalar(
                select(BubaSubmission).where(
                    BubaSubmission.source_sha == source.sha256, BubaSubmission.job_id != key
                )
            )
            if other:
                raise ValueError(
                    "Этот исходник уже подавался с другого аккаунта или платформы; повторная заявка запрещена"
                )
            eligibility = payout_status(db, job)
            if not eligibility["eligible_by_local_data"]:
                raise ValueError("Нужны 10 000 просмотров и срок подачи не позднее 7 календарных дней")
        if request.state in ("submitted", "paid", "rejected"):
            if request.state != "submitted" and record.state not in ("submitted", "paid", "rejected"):
                raise ValueError("Сначала отметьте фактическую подачу заявки")
            record.source_sha = source.sha256
        record.state, record.updated = request.state, now()
        db.flush()
        return payout_status(db, job)


@app.get("/api/jobs/{key}/comments")
def job_comments(key: str):
    from .comments import result

    with Session() as db:
        require(db, Job, key)
    return result(key)


@app.post("/api/jobs/{key}/comments/scan")
def scan_job_comments(key: str):
    from .comments import scan

    return scan(key)


@app.patch("/api/comments/{key}")
def review_comment(key: str, request: CommentReview):
    with Session.begin() as db:
        row = require(db, Comment, key)
        row.reviewed = request.reviewed
        return {"reviewed": row.reviewed, "remote_deleted": False}


@app.post("/api/jobs/{key}/tiktok-result")
def check_tiktok_result(key: str):
    from .tiktok_upload import check_result

    with Session() as db:
        job = require(db, Job, key)
        variant = require(db, Variant, job.variant_id)
        account = require(db, Account, variant.account_id)
        if (
            account.platform != "tiktok"
            or not account.connected
            or job.status not in ("unknown", "platform_processing")
            or not job.upload_started_at
        ):
            raise ValueError("Проверка доступна для отправленного TikTok с действующей сессией")
        account_id, handle, variant_id = account.id, account.external_id, variant.id
        source, caption, started = (
            variant.output,
            variant.config.get("caption", ""),
            datetime.fromisoformat(job.upload_started_at).timestamp(),
        )
    result = check_result(vault.get(account_id), source, caption, handle, started)
    if not result:
        raise ValueError(
            "Однозначный результат в TikTok Studio пока не найден. Повторная публикация не выполнялась."
        )
    from .tiktok_public import available

    publicly_available = available(handle, result.external_id)
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = require(db, Job, key)
        account = require(db, Account, account_id)
        if (
            job.status == "published"
            and job.external_id == result.external_id
            and job.variant_id == variant_id
            and account.external_id == handle
        ):
            return job_data(db, job)  # The background public check may finish during this read-only check.
        if (
            job.status not in ("unknown", "platform_processing")
            or job.variant_id != variant_id
            or account.external_id != handle
            or not account.connected
        ):
            raise ValueError("Задание или аккаунт изменились во время проверки")
        duplicate = db.scalar(
            select(Job.id)
            .join(Variant)
            .where(Variant.account_id == account_id, Job.external_id == result.external_id, Job.id != key)
        )
        if duplicate:
            raise ValueError("Этот ID уже учтён в другом задании")
        job.external_id, job.url, job.status = result.external_id, result.url, "platform_processing"
        job.error = "Ролик найден в TikTok Studio по описанию, длительности и времени отправки. Публичная доступность пока не подтверждена."
        if publicly_available:
            job.status, job.error = "published", None
        job.updated = now()
        account.method = "browser_verified"
        return job_data(db, job)


@app.get("/api/health")
def health():
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            tools[name] = bool(binary(name))
        except RuntimeError:
            tools[name] = False
    return {
        "ok": all(tools.values()),
        "tools": tools,
        "free_gb": round(shutil.disk_usage(DATA).free / 2**30, 1),
        "application": "SurvivalFlow",
        "data_directory": str(DATA),
        "pid": os.getpid(),
    }


@lru_cache(maxsize=128)
def text_banner(text):
    buffer = io.BytesIO()
    builtin_banner(text, buffer)
    return buffer.getvalue()


@app.get("/api/banner-preview")
def preview_banner(text: str = "Продолжение в нашем канале"):
    if len(text) > 120:
        raise ValueError("Текст баннера должен быть не длиннее 120 символов")
    return Response(text_banner(text), media_type="image/png")


@app.post("/api/assets")
def upload(kind: str, file: Annotated[UploadFile, File()]):
    if kind not in ("video", "banner"):
        raise ValueError("Неизвестный тип файла")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise ValueError("Неподдерживаемый формат файла")
    asset_id = uid()
    destination = DATA / "uploads" / (asset_id + suffix)
    digest, size = hashlib.sha256(), 0
    try:
        with destination.open("wb") as output:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > 4 * 1024**3:
                    raise ValueError("Файл больше 4 ГБ")
                digest.update(block)
                output.write(block)
        info = inspect(destination, kind, asset_id)
        edit = (
            Edit(duration=min(2.5, info["duration"] / 2), buba_mode=True).model_dump()
            if kind == "video"
            else {}
        )
        with Session.begin() as db:
            asset = Asset(
                id=asset_id,
                kind=kind,
                name=Path(file.filename or "Файл").name,
                path=str(destination),
                sha256=digest.hexdigest(),
                media=info,
                edit=edit,
            )
            db.add(asset)
            db.flush()
            return asset_data(asset)
    except Exception:
        destination.unlink(missing_ok=True)
        (DATA / "thumbnails" / f"{asset_id}.png").unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@app.get("/api/assets/{key}/{mode}")
def asset_file(key: str, mode: str):
    with Session() as db:
        item = require(db, Asset, key)
        if mode not in ("file", "thumbnail"):
            raise HTTPException(404)
        path = Path(item.path) if mode == "file" else DATA / "thumbnails" / f"{item.id}.png"
        if not path.exists():
            raise HTTPException(404, "Файл отсутствует")
        return FileResponse(path)


@app.delete("/api/assets/{key}")
def archive_asset(key: str):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        asset = require(db, Asset, key)
        if asset.kind != "video":
            raise ValueError("Убирать из списка можно только исходные видео")
        asset.archived = True
    return {"archived": key, "original_preserved": True}


@app.put("/api/assets/{key}/edit")
def save_edit(key: str, request: Edit):
    with Session.begin() as db:
        asset = require(db, Asset, key)
        if asset.kind != "video":
            raise ValueError("Настройки доступны только для видео")
        request.effective(asset.media["duration"])
        if request.banner_id and require(db, Asset, request.banner_id).kind != "banner":
            raise ValueError("Выберите баннер")
        asset.edit = request.model_dump()
        return asset_data(asset)


@app.post("/api/queue")
def create_jobs(request: Enqueue):
    with Session.begin() as db:
        # Serialize read/check/insert, so simultaneous clicks reuse the same durable jobs.
        db.execute(text("BEGIN IMMEDIATE"))
        return enqueue(db, request)


@app.post("/api/jobs/{key}/action")
def job_action(key: str, request: JobAction):
    with Session.begin() as db:
        if request.action != "reconcile":
            db.execute(text("BEGIN IMMEDIATE"))
        job = require(db, Job, key)
        action(db, job, request)
        return job_data(db, job)


@app.get("/api/jobs/{key}/file")
def export(key: str, download: bool = True):
    with Session() as db:
        job = require(db, Job, key)
        path = db.get(Variant, job.variant_id).output
        if not path or not Path(path).exists():
            raise HTTPException(404, "Экспорт пока не готов")
        return FileResponse(
            path, media_type="video/mp4", filename=f"SurvivalFlow-{job.id[:8]}.mp4" if download else None
        )


@app.post("/api/jobs/approve-batch")
def approve_batch(request: BatchApproval):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        ids = list(dict.fromkeys(request.job_ids))
        for key in ids:
            job = require(db, Job, key)
            if job.status not in ("ready", "waiting_auth"):
                raise ValueError("Состав пакета изменился. Откройте подтверждение заново.")
            action(db, job, JobAction(action="approve"))
        return {"approved": ids}


@app.post("/api/accounts")
def add_account(request: AccountCreate):
    with Session.begin() as db:
        if len(db.scalars(select(Account)).all()) >= 30:
            raise ValueError("Достигнут предел 30 аккаунтов")
        account = Account(**request.model_dump())
        db.add(account)
        db.flush()
        return public_account(account)


@app.post("/api/accounts/{key}/connect")
def connect(key: str):
    with Session() as db:
        account = require(db, Account, key)
        if account.platform == "youtube":
            begin_youtube(key)
        else:
            begin_browser(key)
            return {"status": "browser_waiting"}
        return {"status": "waiting"}


@app.get("/api/accounts/{key}/browser-pairing")
def get_pairing(key: str):
    return pairing_details(key)


@app.post("/api/browser-pairing/check")
def bridge_check(payload: dict):
    return check_pairing(payload)


@app.post("/api/browser-pairing/submit")
def bridge_submit(payload: dict):
    return submit_pairing(payload)


@app.post("/api/browser-extension/folder")
def extension_folder():
    path = ROOT / "browser-extension"
    if not path.is_dir():
        raise RuntimeError("Папка расширения не найдена")
    os.startfile(path)
    return {"opened": True}


@app.post("/api/accounts/{key}/connect/isolated")
def connect_isolated(key: str):
    with Session() as db:
        if require(db, Account, key).platform != "tiktok":
            raise ValueError("Выберите TikTok")
    begin_tiktok(key)
    return {"status": "waiting"}


@app.post("/api/accounts/{key}/connect/confirm")
def confirm_tiktok(key: str, request: TikTokConfirmation):
    with Session() as db:
        if require(db, Account, key).platform != "tiktok":
            raise ValueError("Выберите TikTok-аккаунт")
    if is_browser_pairing(key):
        confirm_browser(key, request.handle)
    else:
        confirm_login(key, request.handle)
    return {"status": "verifying"}


@app.post("/api/accounts/{key}/connect/cancel")
def cancel_connection(key: str):
    with Session() as db:
        require(db, Account, key)
    with cancelling_auth(key):
        pass
    return {"status": "cancelled"}


@app.post("/api/accounts/{key}/connect/return-to-tiktok")
def return_tiktok(key: str):
    with Session() as db:
        if require(db, Account, key).platform != "tiktok":
            raise ValueError("Выберите TikTok-аккаунт")
    return_to_tiktok(key)
    return {"status": "returning"}


@app.post("/api/accounts/{key}/session")
def import_session(key: str, payload: dict):
    with Session() as db:
        if require(db, Account, key).platform != "tiktok":
            raise ValueError("Выберите TikTok")
    with cancelling_auth(key), Session.begin() as db:
        account = require(db, Account, key)
        import_tiktok(account, payload)
        return public_account(account)


@app.post("/api/accounts/{key}/disconnect")
def disconnect(key: str):
    with cancelling_auth(key), Session.begin() as db:
        account = require(db, Account, key)
        vault.remove(key)
        account.connected, account.method = False, ""
        return public_account(account)


@app.put("/api/google-client")
def google_client(payload: dict):
    if not isinstance(payload.get("installed"), dict) or not payload["installed"].get("client_id"):
        raise ValueError("Нужен OAuth JSON типа Desktop app из Google Cloud")
    vault.put("google_client", payload)
    return {"saved": True}


@app.patch("/api/settings")
def update_settings(request: SettingsUpdate):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = db.get(Setting, "application")
        item.value = {**settings(db), **request.model_dump(exclude_none=True)}
        return item.value


@app.get("/api/metrics")
def metrics():
    with Session() as db:
        return [
            {"id": m.id, "job_id": m.job_id, "captured": m.captured, "values": m.values, "source": m.source}
            for m in db.scalars(select(Metric).order_by(Metric.captured)).all()
        ]


@app.post("/api/metrics/refresh")
def update_metrics():
    with Session.begin() as db:
        return refresh_metrics(db)


@app.post("/api/metrics/import")
def import_metrics(file: Annotated[UploadFile, File()]):
    raw = file.file.read(2 * 1024 * 1024 + 1)
    file.file.close()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("CSV больше 2 МБ")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except UnicodeError as error:
        raise ValueError("Нужен CSV в кодировке UTF-8") from error
    if not rows or len(rows) > 5000:
        raise ValueError("В CSV должно быть от 1 до 5000 строк")
    with Session.begin() as db:
        for row in rows:
            job = require(db, Job, row.get("job_id", ""))
            try:
                captured = datetime.fromisoformat(row.get("captured", ""))
                if captured.tzinfo is None or captured > datetime.now(UTC):
                    raise ValueError()
                timestamp = captured.astimezone(UTC).isoformat()
                values = {
                    key: int(row[key]) if row.get(key, "").strip() else None
                    for key in ("views", "likes", "comments", "shares")
                }
                if not any(value is not None for value in values.values()) or any(
                    value is not None and value < 0 for value in values.values()
                ):
                    raise ValueError()
            except (ValueError, TypeError) as error:
                raise ValueError(
                    "Проверьте captured (ISO 8601 с поясом) и неотрицательные целые метрики"
                ) from error
            existing = db.scalar(
                select(Metric).where(
                    Metric.job_id == job.id,
                    Metric.captured == timestamp,
                    Metric.source == "Импорт CSV пользователя",
                )
            )
            if existing:
                existing.values = values
            else:
                db.add(
                    Metric(job_id=job.id, captured=timestamp, values=values, source="Импорт CSV пользователя")
                )
                db.flush()
    return {"imported": len(rows)}


@app.get("/api/metrics/template")
def metrics_template():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["job_id", "captured", "views", "likes", "comments", "shares", "video", "account"])
    with Session() as db:
        for job in db.scalars(select(Job).order_by(Job.created.desc()).limit(100)).all():
            variant = db.get(Variant, job.variant_id)
            writer.writerow(
                [
                    job.id,
                    now(),
                    "",
                    "",
                    "",
                    "",
                    db.get(Asset, variant.asset_id).name,
                    db.get(Account, variant.account_id).name,
                ]
            )
    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=survivalflow-metrics.csv"},
    )


@app.post("/api/backup")
def backup():
    path = backup_database()
    return {"name": path.name, "path": str(path), "secrets_included": False}


@app.post("/api/cleanup")
def cleanup():
    with Session() as db:
        days = settings(db)["retention_days"]
    return cleanup_exports(days)


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

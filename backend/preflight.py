"""Read-only sign-in checks before publication. Never opens an upload form or writes to a platform.

Result per account (Setting "preflight_<id>"): status ok | auth | error, detail, checked (UTC ISO).
"auth" means the saved sign-in is proven unusable; "error" means the check itself failed (network,
quota, browser) and says nothing about the sign-in, so it never disconnects an account.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .db import Account, Job, Setting, Variant, now

# Before a due publication: re-check at most this often. TikTok opens a real browser, so less often.
FRESH = {"youtube": timedelta(minutes=30), "tiktok": timedelta(hours=6)}
AHEAD = timedelta(minutes=45)
RECHECK = timedelta(minutes=30)


def youtube_check(account):
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError

    from .platforms import AccountIdentityError, youtube_preflight_failure, youtube_service

    try:
        service = youtube_service(account)
        items = service.channels().list(part="id", mine=True).execute().get("items", [])
    except (RefreshError, AccountIdentityError, HttpError) as error:
        detail, needs_auth = youtube_preflight_failure(error)
        return ("auth" if needs_auth else "error"), detail
    except Exception:
        return "error", "Не удалось проверить YouTube: нет сети или Google не ответил. Вход не проверен."
    if not any(item["id"] == account.external_id for item in items):
        return "auth", "Авторизован другой канал. Подключите нужный YouTube-канал заново."
    return "ok", "Вход в YouTube действует, канал совпадает."


def tiktok_check(account):
    from . import vault
    from .tiktok_upload import BeforePublishError, browser_page, identity

    try:
        with browser_page(vault.get(account.id), headless=True) as page:
            identity(page, account.external_id)
    except BeforePublishError as error:
        return ("auth" if error.auth else "error"), str(error)
    except Exception as error:
        if any(code in str(error) for code in ("ERR_", "net::", "Timeout")):
            return "error", "TikTok не открылся: сеть или сайт недоступны. Вход не проверен."
        # browser_page waits for the own-profile button; without a session it never appears.
        return "auth", "TikTok не показал вход в аккаунт. Переподключите его через расширение Chrome."
    return "ok", "Вход в TikTok действует, аккаунт совпадает."


CHECKS = {"youtube": youtube_check, "tiktok": tiktok_check}


def stored(db, account_id):
    item = db.get(Setting, "preflight_" + account_id)
    return item.value if item else None


def remember(db, account_id, value):
    item = db.get(Setting, "preflight_" + account_id)
    if item:
        item.value = value
    else:
        db.add(Setting(key="preflight_" + account_id, value=value))


def check(db, account, checks=None):
    """Check one account and store the result. The caller commits."""
    checks = checks or CHECKS
    if not account.connected or not account.external_id:
        status, detail = "auth", "Аккаунт не подключён."
    elif not account.enabled:
        status, detail = "error", "Канал выключен в FrameFlow."
    else:
        status, detail = checks[account.platform](account)
    value = {"status": status, "detail": detail, "checked": now()}
    remember(db, account.id, value)
    return value


def check_accounts(db, account_ids, checks=None):
    result = {}
    for key in dict.fromkeys(account_ids):
        account = db.get(Account, key)
        if account:
            result[key] = check(db, account, checks)
            if result[key]["status"] == "auth":
                hold_jobs(db, account, result[key]["detail"])
            elif result[key]["status"] == "ok":
                release_jobs(db, account)
            db.commit()  # a slow TikTok browser check must not hold a write transaction
    return result


def due_accounts(db, current=None):
    """Accounts with an approved publication in the next AHEAD whose last check is not fresh."""
    current = current or datetime.now(UTC)
    edge = (current + AHEAD).isoformat()
    jobs = db.scalars(
        select(Job).where(
            Job.approved.is_(True),
            Job.status.in_(["queued", "rendering", "ready", "scheduled", "waiting_auth"]),
            Job.upload_started_at.is_(None),
        )
    ).all()
    accounts = {}
    for job in jobs:
        if job.scheduled and job.scheduled > edge:
            continue
        account = db.get(Account, db.get(Variant, job.variant_id).account_id)
        if not account.connected or not account.enabled:
            continue
        last = stored(db, account.id)
        # A held account is re-checked often: the user may have fixed the sign-in in the browser.
        fresh = RECHECK if last and last["status"] == "auth" else FRESH[account.platform]
        if last and current - datetime.fromisoformat(last["checked"]) < fresh:
            continue
        accounts[account.id] = account
    return list(accounts.values())


def forget(db, account_id):
    """A fresh connection replaces any earlier check result."""
    item = db.get(Setting, "preflight_" + account_id)
    if item:
        db.delete(item)


def blocked(db, account):
    """The last check proved the sign-in unusable; publications wait until reconnect or a passing check."""
    last = stored(db, account.id)
    return bool(last and last["status"] == "auth")


def hold_jobs(db, account, detail):
    """A proven sign-in failure: stop this account's pending publications before any upload starts.

    The account itself stays connected: a check can be wrong, and reconnecting TikTok costs a new
    verification upload. Jobs wait in waiting_auth until a passing check or a reconnect.
    """
    for job in db.scalars(
        select(Job)
        .join(Variant)
        .where(
            Variant.account_id == account.id,
            Job.approved.is_(True),
            Job.status.in_(["ready", "scheduled"]),
            Job.upload_started_at.is_(None),
        )
    ).all():
        job.status = "waiting_auth"
        job.error = "Проверка перед публикацией: " + detail + " Ролик не отправлялся."
        job.updated = now()


def release_jobs(db, account):
    """After a passing check, jobs held by an earlier failed check continue as approved."""
    for job in db.scalars(
        select(Job)
        .join(Variant)
        .where(
            Variant.account_id == account.id,
            Job.approved.is_(True),
            Job.status == "waiting_auth",
            Job.upload_started_at.is_(None),
            Job.error.like("Проверка перед публикацией:%"),
        )
    ).all():
        job.status, job.error, job.updated = "scheduled", None, now()


def scheduled_checks(checks=None):
    """Worker job: check accounts with publications due soon; hold their jobs on a proven failure."""
    from .db import Session

    with Session() as db:
        for account in due_accounts(db):
            result = check(db, account, checks)
            if result["status"] == "auth":
                hold_jobs(db, account, result["detail"])
            elif result["status"] == "ok":
                release_jobs(db, account)
            db.commit()

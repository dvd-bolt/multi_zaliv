"""«Выпуск»: one saved path from ready materials to publications.

A release is a durable draft (materials, channels, time, consent). Starting it creates ordinary jobs through
the existing queue. A TikTok account without a confirmed upload gets exactly one first publication that
the user publishes explicitly; the rest of that account's batch is only enqueued after that success and
only when the user chose so in the final plan. Nothing here relaxes browser_verified, BUBA or unknown.
"""

import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from .db import Account, Asset, Job, Release, Session, now, settings
from .schemas import Enqueue

router = APIRouter(prefix="/api/releases")

SUCCESS = ("published", "platform_processing")
STOPPED = ("cancelled", "failed", "rejected", "deleted")


class Plan(BaseModel):
    step: int = Field(1, ge=1, le=3)
    publish: bool = True
    scheduled: str | None = None
    schedules: dict[str, str] = Field(default_factory=dict, max_length=30)
    comment: str | None = Field(None, max_length=500)
    comment_pin: bool = True
    tiktok_sound: str | None = Field(None, max_length=100)
    continue_after_first: bool = False
    buba_confirmed: bool = False


class ReleaseInput(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=30)
    account_ids: list[str] = Field(default_factory=list, max_length=30)


class ReleaseUpdate(BaseModel):
    asset_ids: list[str] | None = Field(None, max_length=30)
    account_ids: list[str] | None = Field(None, max_length=30)
    plan: Plan | None = None


def first_needed(account):
    return account.platform == "tiktok" and account.method != "browser_verified"


def version(asset):
    """What the user confirmed: the source file and every setting that changes the published video."""
    return hashlib.sha256(json.dumps([asset.sha256, asset.edit], sort_keys=True).encode()).hexdigest()


def public(release):
    return {
        "id": release.id,
        "status": release.status,
        "asset_ids": release.asset_ids,
        "account_ids": release.account_ids,
        "plan": {**Plan().model_dump(), **(release.plan or {})},
        "jobs": release.jobs or {},
        "created": release.created,
        "updated": release.updated,
    }


def require(db, key):
    release = db.get(Release, key)
    if release is None or release.status == "removed":
        raise HTTPException(404, "Выпуск не найден")
    return release


def clean_ids(db, asset_ids, account_ids):
    assets = [
        key
        for key in dict.fromkeys(asset_ids)
        if (a := db.get(Asset, key)) is not None and a.kind == "video" and not a.archived
    ]
    accounts = [
        key for key in dict.fromkeys(account_ids) if (a := db.get(Account, key)) is not None and a.enabled
    ]
    return assets, accounts


@router.get("")
def listing():
    with Session() as db:
        rows = db.scalars(
            select(Release).where(Release.status != "removed").order_by(Release.updated.desc()).limit(8)
        ).all()
        return [public(r) for r in rows]


@router.post("")
def create(request: ReleaseInput):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        assets, accounts = clean_ids(db, request.asset_ids, request.account_ids)
        if not assets:
            raise ValueError("Выберите готовые ролики для выпуска")
        # A repeated click or a returning window opens the same draft instead of a duplicate.
        for draft in db.scalars(select(Release).where(Release.status == "draft")).all():
            if sorted(draft.asset_ids) == sorted(assets):
                if accounts and not draft.account_ids:
                    draft.account_ids, draft.updated = accounts, now()
                return public(draft)
        release = Release(asset_ids=assets, account_ids=accounts, plan=Plan().model_dump(), jobs={})
        db.add(release)
        db.flush()
        return public(release)


@router.get("/{key}")
def read(key: str):
    with Session() as db:
        return public(require(db, key))


@router.post("/{key}/continue/{account_id}")
def allow_rest(key: str, account_id: str):
    """The user explicitly allows the rest of the batch for one account after its first publication."""
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        release = require(db, key)
        jobs = dict(release.jobs or {})
        deferred = dict(jobs.get("deferred") or {})
        entry = deferred.get(account_id)
        if not entry or entry.get("state") not in ("not_allowed", "changed", "blocked"):
            raise ValueError("Для этого канала нет отложенных роликов")
        deferred[account_id] = {
            **entry,
            "state": "waiting",
            "detail": "",
            "versions": {
                key: version(db.get(Asset, key)) for key in entry["asset_ids"] if db.get(Asset, key)
            },
        }
        jobs["deferred"] = deferred
        release.jobs, release.updated = jobs, now()
        advance(db)
        return public(release)


@router.put("/{key}")
def change(key: str, request: ReleaseUpdate):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        release = require(db, key)
        if release.status != "draft":
            raise ValueError("Выпуск уже запущен; его состав меняется в очереди")
        assets, accounts = clean_ids(
            db,
            release.asset_ids if request.asset_ids is None else request.asset_ids,
            release.account_ids if request.account_ids is None else request.account_ids,
        )
        if request.asset_ids is not None and not assets:
            raise ValueError("В выпуске должен остаться хотя бы один ролик")
        release.asset_ids, release.account_ids = assets, accounts
        if request.plan is not None:
            release.plan = request.plan.model_dump()
        release.updated = now()
        return public(release)


@router.delete("/{key}")
def remove(key: str):
    with Session.begin() as db:
        release = require(db, key)
        # Removing a started release only hides the plan; its jobs stay in the queue.
        release.status, release.updated = "removed", now()
        return {"removed": key}


@router.post("/{key}/start")
def start(key: str):
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        release = require(db, key)
        if release.status != "draft":
            return public(release)  # A repeated click must not create anything twice.
        launch(db, release)
    if release.plan.get("publish"):
        try:
            from .app import ensure_queue_worker

            ensure_queue_worker()
        except (OSError, RuntimeError, ImportError):
            pass
    with Session() as db:
        return public(db.get(Release, key))


def launch(db, release):
    from .queue import enqueue

    plan = {**Plan().model_dump(), **(release.plan or {})}
    assets, account_ids = clean_ids(db, release.asset_ids, release.account_ids)
    if not assets:
        raise ValueError("Выберите ролики")
    if not account_ids:
        raise ValueError("Выберите каналы")
    accounts = [db.get(Account, key) for key in account_ids]
    if settings(db).get("one_per_platform", True):
        platforms = [a.platform for a in accounts]
        if len(platforms) != len(set(platforms)):
            raise ValueError(
                "Один ролик — на один TikTok и один YouTube. Выберите по одному каналу на платформу "
                "(или отключите правило в Настройках)."
            )
    publish = plan["publish"]
    if publish and any(a.platform == "tiktok" for a in accounts):
        for key in assets:
            if (db.get(Asset, key).edit or {}).get("visibility") == "unlisted":
                raise ValueError("Для TikTok выберите «Открытый доступ» или «Только я»")
    if publish:
        for account in accounts:
            if not account.connected or not account.external_id:
                raise ValueError(f"Войдите в {account.name} в разделе «Каналы»")
    if publish and plan["buba_confirmed"]:
        from .buba import REVIEW

        for key in assets:
            asset = db.get(Asset, key)
            if asset.edit.get("buba_mode"):
                asset.edit = {**asset.edit, "buba_review": {name: True for name in REVIEW}}
    common = {
        "scheduled": plan["scheduled"],
        "schedules": plan["schedules"],
        "buba_confirmed": plan["buba_confirmed"],
        "comment": plan["comment"],
        "comment_pin": plan["comment_pin"],
        "tiktok_sound": plan["tiktok_sound"],
    }
    ready = [a.id for a in accounts if not (publish and first_needed(a))]
    fresh = [a for a in accounts if publish and first_needed(a)]
    job_ids, first, deferred = [], {}, {}
    if ready:
        result = enqueue(db, Enqueue(asset_ids=assets, account_ids=ready, auto_publish=publish, **common))
        job_ids += result["created"] + result["existing"]
    for account in fresh:
        # The first video is rendered for viewing; the user publishes it with an explicit button.
        result = enqueue(
            db,
            Enqueue(
                asset_ids=assets[:1],
                account_ids=[account.id],
                auto_publish=False,
                **{**common, "scheduled": None, "schedules": {}},
            ),
        )
        ids = result["created"] + result["existing"]
        job_ids += ids
        first[account.id] = ids[0]
        if plan["continue_after_first"] and len(assets) > 1:
            deferred[account.id] = {
                "asset_ids": assets[1:],
                "versions": {key: version(db.get(Asset, key)) for key in assets[1:]},
                "state": "waiting",
            }
        elif len(assets) > 1:
            deferred[account.id] = {"asset_ids": assets[1:], "versions": {}, "state": "not_allowed"}
    release.asset_ids, release.account_ids = assets, account_ids
    release.jobs = {"job_ids": list(dict.fromkeys(job_ids)), "first": first, "deferred": deferred}
    release.status, release.updated = "started", now()


def advance(db):
    """Continue the batch of an account after its first TikTok publication was confirmed."""
    from .queue import enqueue

    for release in db.scalars(select(Release).where(Release.status == "started")).all():
        jobs = dict(release.jobs or {})
        deferred = dict(jobs.get("deferred") or {})
        changed = False
        for account_id, entry in deferred.items():
            if entry.get("state") != "waiting":
                continue
            account = db.get(Account, account_id)
            job = db.get(Job, (jobs.get("first") or {}).get(account_id, ""))
            if account is None or job is None:
                continue
            if job.status in (*STOPPED, "unknown"):
                if entry.get("detail") != job.status:
                    entry = {**entry, "detail": job.status}
                    deferred[account_id], changed = entry, True
                continue
            if not (
                job.status in SUCCESS
                and job.external_id
                and account.method == "browser_verified"
                and account.connected
                and account.enabled
            ):
                continue
            assets = [db.get(Asset, key) for key in entry["asset_ids"]]
            if any(a is None or a.archived or version(a) != entry["versions"].get(a.id) for a in assets):
                deferred[account_id] = {**entry, "state": "changed"}
                changed = True
                continue
            plan = {**Plan().model_dump(), **(release.plan or {})}
            try:
                result = enqueue(
                    db,
                    Enqueue(
                        asset_ids=entry["asset_ids"],
                        account_ids=[account_id],
                        auto_publish=True,
                        buba_confirmed=plan["buba_confirmed"],
                        comment=plan["comment"],
                        comment_pin=plan["comment_pin"],
                        tiktok_sound=plan["tiktok_sound"],
                    ),
                )
            except ValueError as error:
                deferred[account_id] = {**entry, "state": "blocked", "detail": str(error)}
                changed = True
                continue
            ids = result["created"] + result["existing"]
            jobs["job_ids"] = list(dict.fromkeys([*jobs.get("job_ids", []), *ids]))
            deferred[account_id] = {**entry, "state": "queued"}
            changed = True
        if changed:
            jobs["deferred"] = deferred
            release.jobs, release.updated = jobs, now()


def tick():
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        advance(db)

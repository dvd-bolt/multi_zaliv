import importlib

import pytest
from fastapi.testclient import TestClient

from backend import releases
from backend.app import app
from backend.config import DATA
from backend.db import Account, Asset, Job, Release, Session, Setting, Variant, settings, uid
from backend.schemas import Edit

client = TestClient(app)
TEST_OUTPUT = DATA / "exports" / "release-tests"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.import_module("backend.app"), "ensure_queue_worker", lambda: None)
    assets, youtube, tiktok = [uid() for _ in range(3)], uid(), uid()
    with Session.begin() as db:
        for number, key in enumerate(assets):
            path = tmp_path / f"{key}.mp4"
            path.write_bytes(b"clip")
            db.add(
                Asset(
                    id=key,
                    kind="video",
                    name=f"Клип {number + 1}",
                    path=str(path),
                    sha256=key,
                    media={"duration": 30, "width": 1080, "height": 1920, "has_audio": True},
                    edit=Edit(
                        caption=f"Клип {number + 1}", title=f"Клип {number + 1}", visibility="public"
                    ).model_dump(),
                )
            )
        db.add(
            Account(
                id=youtube, platform="youtube", name="YT", connected=True, external_id="UC_X", method="oauth"
            )
        )
        db.add(
            Account(
                id=tiktok,
                platform="tiktok",
                name="TT",
                connected=True,
                external_id="tt_new",
                method="browser_login",
            )
        )
        previous = dict(db.get(Setting, "application").value)
        db.get(Setting, "application").value = {**settings(db), "paused": True}
    yield {"assets": assets, "youtube": youtube, "tiktok": tiktok}
    with Session.begin() as db:
        db.get(Setting, "application").value = previous


def draft(world, **plan):
    created = client.post(
        "/api/releases",
        json={"asset_ids": world["assets"], "account_ids": [world["youtube"], world["tiktok"]]},
    )
    assert created.status_code == 200, created.text
    key = created.json()["id"]
    if plan:
        body = {**created.json()["plan"], **plan}
        assert client.put(f"/api/releases/{key}", json={"plan": body}).status_code == 200
    return key


def jobs_for(account_id):
    with Session() as db:
        return [
            (job, variant)
            for job, variant in db.query(Job, Variant).join(Variant).all()
            if variant.account_id == account_id
        ]


def test_repeated_click_returns_the_same_draft(world):
    first = client.post("/api/releases", json={"asset_ids": world["assets"]}).json()
    again = client.post("/api/releases", json={"asset_ids": list(reversed(world["assets"]))}).json()
    assert first["id"] == again["id"] and first["status"] == "draft"
    saved = client.put(f"/api/releases/{first['id']}", json={"plan": {**first["plan"], "step": 2}}).json()
    assert client.get(f"/api/releases/{first['id']}").json()["plan"]["step"] == saved["plan"]["step"] == 2


def test_new_tiktok_gets_one_first_publication_and_the_rest_waits(world):
    key = draft(world, continue_after_first=True)
    started = client.post(f"/api/releases/{key}/start")
    assert started.status_code == 200, started.text
    release = started.json()
    assert release["status"] == "started"
    youtube = jobs_for(world["youtube"])
    assert len(youtube) == 3 and all(job.auto_publish and job.approved for job, _ in youtube)
    tiktok = jobs_for(world["tiktok"])
    # Only the first video, rendered for viewing; publication needs the explicit first-upload button.
    assert len(tiktok) == 1 and not tiktok[0][0].auto_publish and not tiktok[0][0].approved
    assert release["jobs"]["first"][world["tiktok"]] == tiktok[0][0].id
    assert release["jobs"]["deferred"][world["tiktok"]]["state"] == "waiting"
    # A repeated start creates nothing.
    assert client.post(f"/api/releases/{key}/start").json()["jobs"] == release["jobs"]
    assert len(jobs_for(world["youtube"])) == 3

    releases.tick()
    assert len(jobs_for(world["tiktok"])) == 1  # nothing before a confirmed success

    with Session.begin() as db:
        job = db.get(Job, tiktok[0][0].id)
        job.status, job.external_id = "platform_processing", "7300000000000000001"
        db.get(Account, world["tiktok"]).method = "browser_verified"
    releases.tick()
    tiktok = jobs_for(world["tiktok"])
    assert len(tiktok) == 3
    assert sum(job.auto_publish and job.approved for job, _ in tiktok) == 2
    assert client.get(f"/api/releases/{key}").json()["jobs"]["deferred"][world["tiktok"]]["state"] == "queued"
    releases.tick()
    assert len(jobs_for(world["tiktok"])) == 3


def test_unknown_first_result_holds_the_rest(world):
    key = draft(world, continue_after_first=True)
    first = client.post(f"/api/releases/{key}/start").json()["jobs"]["first"][world["tiktok"]]
    with Session.begin() as db:
        db.get(Job, first).status = "unknown"
    releases.tick()
    entry = client.get(f"/api/releases/{key}").json()["jobs"]["deferred"][world["tiktok"]]
    assert entry["state"] == "waiting" and entry["detail"] == "unknown"
    assert len(jobs_for(world["tiktok"])) == 1


def test_changed_video_after_confirmation_is_not_published_silently(world):
    key = draft(world, continue_after_first=True)
    first = client.post(f"/api/releases/{key}/start").json()["jobs"]["first"][world["tiktok"]]
    with Session.begin() as db:
        asset = db.get(Asset, world["assets"][2])
        asset.edit = {**asset.edit, "caption": "Другое описание"}
        job = db.get(Job, first)
        job.status, job.external_id = "published", "7300000000000000002"
        db.get(Account, world["tiktok"]).method = "browser_verified"
    releases.tick()
    entry = client.get(f"/api/releases/{key}").json()["jobs"]["deferred"][world["tiktok"]]
    assert entry["state"] == "changed"
    assert len(jobs_for(world["tiktok"])) == 1
    # The user looks again and explicitly allows the rest.
    allowed = client.post(f"/api/releases/{key}/continue/{world['tiktok']}")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["jobs"]["deferred"][world["tiktok"]]["state"] == "queued"
    assert len(jobs_for(world["tiktok"])) == 3


def test_without_consent_the_rest_waits_for_an_explicit_decision(world):
    key = draft(world)
    first = client.post(f"/api/releases/{key}/start").json()["jobs"]["first"][world["tiktok"]]
    with Session.begin() as db:
        job = db.get(Job, first)
        job.status, job.external_id = "published", "7300000000000000003"
        db.get(Account, world["tiktok"]).method = "browser_verified"
    releases.tick()
    assert client.get(f"/api/releases/{key}").json()["jobs"]["deferred"][world["tiktok"]]["state"] == (
        "not_allowed"
    )
    assert len(jobs_for(world["tiktok"])) == 1


def test_start_checks_visibility_sign_in_and_one_channel_per_platform(world):
    with Session.begin() as db:
        asset = db.get(Asset, world["assets"][0])
        asset.edit = {**asset.edit, "visibility": "unlisted"}
    key = draft(world)
    refused = client.post(f"/api/releases/{key}/start")
    assert refused.status_code == 400 and "TikTok" in refused.json()["detail"]
    with Session.begin() as db:
        asset = db.get(Asset, world["assets"][0])
        asset.edit = {**asset.edit, "visibility": "private"}
        db.get(Account, world["tiktok"]).connected = False
    refused = client.post(f"/api/releases/{key}/start")
    assert refused.status_code == 400 and "Войдите" in refused.json()["detail"]
    assert client.get(f"/api/releases/{key}").json()["status"] == "draft"
    with Session.begin() as db:
        db.get(Account, world["tiktok"]).connected = True
        second = Account(platform="youtube", name="YT2", connected=True, external_id="UC_Y", method="oauth")
        db.add(second)
        db.flush()
        second_id = second.id
        db.get(Setting, "application").value = {**settings(db), "one_per_platform": True}
    try:
        client.put(
            f"/api/releases/{key}",
            json={"account_ids": [world["youtube"], second_id, world["tiktok"]]},
        )
        refused = client.post(f"/api/releases/{key}/start")
        assert refused.status_code == 400 and "один" in refused.json()["detail"].lower()
    finally:
        with Session.begin() as db:
            db.get(Setting, "application").value = {**settings(db), "one_per_platform": False}
    assert not jobs_for(world["tiktok"])


def test_mp4_only_mode_never_authorizes_publication(world):
    key = draft(world, publish=False)
    client.post(f"/api/releases/{key}/start")
    rows = jobs_for(world["youtube"]) + jobs_for(world["tiktok"])
    assert len(rows) == 6 and not any(job.approved or job.auto_publish for job, _ in rows)
    with Session() as db:
        assert db.get(Release, key).jobs["first"] == {}


def unknown_first(world):
    key = draft(world, continue_after_first=True)
    first = client.post(f"/api/releases/{key}/start").json()["jobs"]["first"][world["tiktok"]]
    output = TEST_OUTPUT / f"{first}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"mp4")
    with Session.begin() as db:
        job = db.get(Job, first)
        db.get(Variant, job.variant_id).output = str(output)
        job.status, job.upload_started_at, job.approved, job.validation_upload = (
            "unknown",
            "2026-11-10T10:00:00+00:00",
            True,
            True,
        )
    return key, first


def test_lost_tiktok_answer_can_be_closed_with_a_link_from_the_same_account(world):
    key, first = unknown_first(world)
    action = f"/api/jobs/{first}/action"
    wrong = client.post(
        action,
        json={
            "action": "tiktok_link",
            "external_id": "https://www.tiktok.com/@other/video/7300000000000000009",
        },
    )
    assert wrong.status_code == 400 and "другой аккаунт" in wrong.json()["detail"]
    done = client.post(
        action,
        json={
            "action": "tiktok_link",
            "external_id": "https://www.tiktok.com/@tt_new/video/7300000000000000009?lang=ru",
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "platform_processing"
    assert done.json()["url"] == "https://www.tiktok.com/@tt_new/video/7300000000000000009"
    with Session() as db:
        assert db.get(Account, world["tiktok"]).method == "browser_verified"
    releases.tick()
    assert len(jobs_for(world["tiktok"])) == 3


def test_absent_tiktok_video_needs_a_new_explicit_click(world):
    key, first = unknown_first(world)
    done = client.post(f"/api/jobs/{first}/action", json={"action": "tiktok_absent"})
    assert done.status_code == 200, done.text
    job = done.json()
    assert job["status"] == "ready" and not job["approved"] and not job["upload_started_at"]
    releases.tick()
    assert len(jobs_for(world["tiktok"])) == 1
    with Session() as db:
        assert db.get(Account, world["tiktok"]).method == "browser_login"
    # Not available for jobs whose result is already known.
    again = client.post(f"/api/jobs/{first}/action", json={"action": "tiktok_absent"})
    assert again.status_code == 400

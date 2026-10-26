import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from backend import vault
from backend.app import app
from backend.config import DATA, binary
from backend.db import Job, Session, Variant
from backend.media import command, probe, render
from backend.queue import recover
from backend.schemas import Edit
from backend.worker import tick

client = TestClient(app)


@pytest.fixture(scope="module")
def package():
    files = []
    for index in range(3):
        path = DATA / f"sample-{index}.mp4"
        args = [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{['344b61', '614b34', '34614b'][index]}:s=160x90:r=8:d=1.5",
        ]
        if index == 0:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-c:a", "aac"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
        command(args)
        with path.open("rb") as file:
            response = client.post("/api/assets?kind=video", files={"file": (path.name, file, "video/mp4")})
        assert response.status_code == 200, response.text
        files.append(response.json())
    banner_path = DATA / "banner.png"
    Image.new("RGBA", (200, 80), (255, 0, 0, 255)).save(banner_path)
    with banner_path.open("rb") as file:
        banner = client.post(
            "/api/assets?kind=banner", files={"file": ("banner.png", file, "image/png")}
        ).json()
    accounts = [
        client.post(
            "/api/accounts", json={"platform": "youtube" if i < 3 else "tiktok", "name": f"LOCAL TEST {i}"}
        ).json()
        for i in range(6)
    ]
    for asset in files:
        edit = {
            **asset["edit"],
            "buba_mode": False,
            "format": "720",
            "duration": 0.5,
            "banner_id": banner["id"],
            "width": 50,
            "x": 50,
            "y": 50,
            "targets": [a["id"] for a in accounts],
        }
        response = client.put(f"/api/assets/{asset['id']}/edit", json=edit)
        assert response.status_code == 200, response.text
    return files, accounts, banner


def frame(path, second):
    return command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "crop=2:2:359:639",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )


def test_18_real_exports_and_idempotence(package):
    assets, accounts, _ = package
    body = {"asset_ids": [a["id"] for a in assets], "account_ids": [a["id"] for a in accounts]}
    result = client.post("/api/queue", json=body)
    assert result.status_code == 200, result.text
    assert len(result.json()["created"]) == 18
    duplicate = client.post("/api/queue", json=body).json()
    assert not duplicate["created"] and len(duplicate["existing"]) == 18
    client.patch("/api/settings", json={"paused": False})
    import time

    began = time.monotonic()
    for _ in range(18):
        tick()
    elapsed = time.monotonic() - began
    jobs = client.get("/api/state").json()["jobs"]
    assert len(jobs) == 18
    assert all(j["status"] == "ready" for j in jobs), json.dumps(jobs, ensure_ascii=False)
    hashes = []
    pixels = {}
    with Session() as db:
        for job in db.scalars(select(Job)).all():
            variant = db.get(Variant, job.variant_id)
            output = Path(variant.output)
            info = probe(output)
            assert (info["width"], info["height"]) == (720, 1280)
            assert abs(info["duration"] - 1.5) < 0.2
            assert info["has_audio"] == (variant.asset_id == assets[0]["id"])
            before, during = frame(output, 0.25), frame(output, 1)
            assert during[0] > 200 and during[1] < 50 and during[2] < 50
            assert before != during
            pixels.setdefault(variant.asset_id, set()).add(before)
            hashes.append(hashlib.sha256(output.read_bytes()).hexdigest())
    assert len(set(hashes)) == 18
    assert all(len(values) == 6 for values in pixels.values()), (
        "Фильтры должны менять кадры, а не только метаданные"
    )
    (DATA / "test-results.json").write_text(
        json.dumps(
            {"exports": 18, "elapsed_seconds": round(elapsed, 2), "directory": str(DATA)}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print(f"\n18 real exports in {elapsed:.2f}s; artifacts: {DATA}")


def test_restart_cancel_and_unknown_never_retry(package):
    with Session.begin() as db:
        jobs = db.scalars(select(Job)).all()
        jobs[0].status, jobs[1].status = "rendering", "uploading"
        ids = jobs[0].id, jobs[1].id, jobs[2].id
    with Session.begin() as db:
        recover(db)
    state = {j["id"]: j for j in client.get("/api/state").json()["jobs"]}
    assert state[ids[0]]["status"] == "queued"
    assert state[ids[1]]["status"] == "unknown"
    assert client.post(f"/api/jobs/{ids[1]}/action", json={"action": "retry"}).status_code == 400
    assert client.post(f"/api/jobs/{ids[2]}/action", json={"action": "cancel"}).status_code == 200
    assert client.post(f"/api/jobs/{ids[2]}/action", json={"action": "retry"}).status_code == 200
    assert client.post(f"/api/jobs/{ids[0]}/action", json={"action": "approve"}).status_code == 400


def test_validation_and_local_origin(package):
    assets, _, _ = package
    invalid = {**assets[0]["edit"], "timing": "manual", "start": 1.4, "duration": 0.5}
    assert client.put(f"/api/assets/{assets[0]['id']}/edit", json=invalid).status_code == 400
    assert (
        client.post("/api/assets?kind=video", files={"file": ("bad.mp4", b"broken", "video/mp4")}).status_code
        == 400
    )
    assert (
        client.patch(
            "/api/settings", json={"paused": True}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert client.get("/api/state", headers={"Host": "evil.test"}).status_code == 400
    with pytest.raises(ValueError):
        Edit.model_validate({"overrides": {"x": {"zoom": 500}}})
    assert (
        client.post(
            "/api/queue", json={"asset_ids": [assets[0]["id"]], "account_ids": ["missing"]}
        ).status_code
        == 400
    )


def test_dpapi_backup_and_missing_metrics():
    vault.put("test_key", {"token": "synthetic-secret"})
    assert b"synthetic-secret" not in vault.secret_path("test_key").read_bytes()
    assert vault.get("test_key") == {"token": "synthetic-secret"}
    vault.remove("test_key")
    assert vault.get("test_key") is None
    with pytest.raises(ValueError):
        vault.get("../escape")
    backup = client.post("/api/backup").json()
    assert Path(backup["path"]).is_file()
    assert backup["secrets_included"] is False
    assert client.get("/api/metrics").json() == []


def test_animation_starts_at_banner_time_and_cancellation(package):
    assets, _, _ = package
    gif = DATA / "animated.gif"
    Image.new("RGB", (200, 80), "red").save(
        gif, save_all=True, append_images=[Image.new("RGB", (200, 80), "lime")], duration=[300, 300], loop=0
    )
    with Session() as db:
        from backend.db import Asset

        source = Path(db.get(Asset, assets[0]["id"]).path)
    config = Edit(format="720", timing="manual", start=0.75, duration=0.6, width=50, x=50, y=50).effective(
        1.5
    )
    output = DATA / "animated-result.mp4"
    render(source, gif, {"image": True, "animated": True}, output, config, 1.5, lambda _: None, lambda: False)
    early, late = frame(output, 0.85), frame(output, 1.2)
    assert early[0] > 200 and early[1] < 50
    assert late[1] > 200 and late[0] < 50
    cancelled = DATA / "cancelled-result.mp4"
    with pytest.raises(InterruptedError):
        render(
            source,
            gif,
            {"image": True, "animated": True},
            cancelled,
            config,
            1.5,
            lambda _: None,
            lambda: True,
        )
    assert not cancelled.exists() and not cancelled.with_suffix(".part.mp4").exists()


def test_metrics_csv_is_labelled_idempotent_and_atomic(package):
    job = client.get("/api/state").json()["jobs"][0]
    body = f"job_id,captured,views,likes,comments,shares\n{job['id']},2026-01-01T00:00:00Z,0,2,,\n"
    for _ in range(2):
        result = client.post("/api/metrics/import", files={"file": ("metrics.csv", body, "text/csv")})
        assert result.status_code == 200, result.text
    metrics = client.get("/api/metrics").json()
    assert len(metrics) == 1
    assert metrics[0]["values"] == {"views": 0, "likes": 2, "comments": None, "shares": None}
    assert metrics[0]["source"] == "Импорт CSV пользователя"
    assert client.get("/api/state").json()["jobs"][0]["status"] == job["status"]
    invalid = body.replace(",0,2,,", ",-1,2,,")
    assert (
        client.post("/api/metrics/import", files={"file": ("bad.csv", invalid, "text/csv")}).status_code
        == 400
    )
    assert len(client.get("/api/metrics").json()) == 1


def test_decoded_frames_are_reproducible(package):
    with Session() as db:
        variant = db.scalar(select(Variant).where(Variant.output.is_not(None)))
        from backend.db import Asset

        source = db.get(Asset, variant.asset_id)
        banner = db.get(Asset, variant.banner_id)
        output = DATA / "repeat.mp4"
        render(
            Path(source.path),
            Path(banner.path),
            banner.media,
            output,
            variant.config,
            source.media["duration"],
            lambda _: None,
            lambda: False,
        )

        def checksum(path):
            return command(
                [binary("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"]
            )

        assert checksum(output) == checksum(variant.output)


def test_batch_approval_is_atomic_and_bound_to_explicit_ids(package):
    jobs = client.get("/api/state").json()["jobs"]
    ready = [j for j in jobs if j["status"] == "ready" and j["account"]["platform"] == "youtube"][:2]
    bad = next(j for j in jobs if j["status"] == "unknown")
    result = client.post("/api/jobs/approve-batch", json={"job_ids": [ready[0]["id"], bad["id"]]})
    assert result.status_code == 400
    state = {j["id"]: j for j in client.get("/api/state").json()["jobs"]}
    assert not state[ready[0]["id"]]["approved"]
    assert (
        client.post("/api/jobs/approve-batch", json={"job_ids": [j["id"] for j in ready]}).status_code == 200
    )
    state = {j["id"]: j for j in client.get("/api/state").json()["jobs"]}
    assert all(state[j["id"]]["status"] == "waiting_auth" for j in ready)

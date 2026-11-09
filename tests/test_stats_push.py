import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import notify, platforms, stats_push, worker
from backend.db import Account, Asset, Base, BubaSubmission, Job, Metric, Setting, Variant


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        notify,
        "connection",
        lambda: {"url": "https://owner.example/api/ext/frameflow", "secret": "TEST-SECRET"},
    )
    monkeypatch.setattr(stats_push, "build_version", lambda: "test-build")
    with factory.begin() as db:
        db.add(Account(id="account", platform="youtube", name="Канал", connected=True, method="oauth"))
        db.add(
            Asset(
                id="asset",
                kind="video",
                name="Исходник.mp4",
                path="PRIVATE-PATH",
                sha256="hash",
                media={},
                edit={"token": "PRIVATE-EDIT"},
            )
        )
        db.flush()
        db.add(
            Variant(
                id="variant",
                asset_id="asset",
                account_id="account",
                fingerprint="unique",
                output="PRIVATE-EXPORT",
                config={
                    "caption": "Название\nОписание",
                    "buba_mode": True,
                    "cookies": "PRIVATE-COOKIE",
                    "profile": "PRIVATE-PROFILE",
                },
            )
        )
        db.flush()
        db.add(
            Job(
                id="job",
                variant_id="variant",
                status="published",
                external_id="remote-id",
                url="https://youtube.com/watch?v=remote-id",
                upload_started_at="2026-11-01T10:00:00+00:00",
                error="PRIVATE-ERROR",
            )
        )
        db.flush()
        db.add(
            BubaSubmission(
                job_id="job",
                state="tracking",
                published_at="2026-11-01T10:00:00+00:00",
                source_sha="PRIVATE-SHA",
            )
        )
        db.add(
            Setting(key="metrics_refresh", value={"updated": 1, "unavailable": 2, "token": "PRIVATE-REFRESH"})
        )
        for index in range(60):
            db.add(
                Metric(
                    id=f"m{index:03}",
                    job_id="job",
                    captured=(datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=index)).isoformat(),
                    source="YouTube Data API",
                    values={"views": index, "likes": 0, "comments": None, "token": "PRIVATE-METRIC"},
                )
            )
    yield factory
    engine.dispose()


def test_snapshot_structure_and_no_private_fields(database):
    with database() as db:
        snapshot = stats_push.build(db)
    assert snapshot["version"] == 1
    assert datetime.fromisoformat(snapshot["generated"]).utcoffset() == timedelta(0)
    assert snapshot["app"] == {"product": "FrameFlow", "build": "test-build"}
    assert snapshot["accounts"] == [
        {"id": "account", "platform": "youtube", "name": "Канал", "status": "connected"}
    ]
    assert snapshot["refresh"] == {"updated": 1, "unavailable": 2}
    assert snapshot["queue"] == dict.fromkeys(("scheduled", "failed", "unknown", "waiting_auth"), 0)
    video = snapshot["videos"][0]
    assert video["title"] == "Название"
    assert video["asset"] == "Исходник.mp4"
    assert video["external_id"] == "remote-id"
    assert video["published_at"] == "2026-11-01T10:00:00+00:00"
    assert video["latest"] == {
        "captured": "2026-11-01T00:59:00+00:00",
        "source": "YouTube Data API",
        "views": 59,
        "likes": 0,
        "comments": None,
        "shares": None,
    }
    assert len(video["history"]) == 48
    assert [m["views"] for m in video["history"]] == list(range(12, 60))
    assert video["payout"]["threshold"] == 10_000
    assert video["payout"]["deadline"] == "2026-11-08T00:00:00+03:00"
    assert video["payout"]["state"] == "tracking"
    text = json.dumps(snapshot)
    for forbidden in (
        "PRIVATE-",
        "TEST-SECRET",
        "cookies",
        "token",
        "profile",
        "fingerprint",
        "growth",
        "views_per_hour",
    ):
        assert forbidden not in text


def test_history_shrinks_by_encoded_body_bytes(database, monkeypatch):
    with database() as db:
        full = stats_push.build(db)
        limit = len(stats_push.encode(full)) - 100
        monkeypatch.setattr(stats_push, "MAX_BYTES", limit)
        small = stats_push.build(db)
    assert len(stats_push.encode(small)) < limit
    assert 0 < len(small["videos"][0]["history"]) < 48
    assert small["videos"][0]["history"][-1] == full["videos"][0]["history"][-1]
    assert small["videos"][0]["latest"] == full["videos"][0]["latest"]


def test_filters_missing_metrics_payout_and_queue(database):
    with database.begin() as db:
        db.get(Variant, "variant").config = {"title": "Заголовок"}
        db.query(Metric).delete()
        for index, state in enumerate(("scheduled", "failed", "unknown", "waiting_auth", "published")):
            db.add(
                Variant(
                    id=f"v{index}",
                    asset_id="asset",
                    account_id="account",
                    fingerprint=f"fp{index}",
                    config={},
                )
            )
            db.flush()
            db.add(Job(id=f"j{index}", variant_id=f"v{index}", status=state, external_id=None))
    with database() as db:
        snapshot = stats_push.build(db)
    assert len(snapshot["videos"]) == 1
    assert snapshot["videos"][0]["latest"] is None
    assert snapshot["videos"][0]["history"] == []
    assert "payout" not in snapshot["videos"][0]
    assert snapshot["queue"] == dict.fromkeys(("scheduled", "failed", "unknown", "waiting_auth"), 1)


@pytest.mark.parametrize(
    ("code", "result"),
    [(200, "ok"), (401, "auth_error"), (403, "auth_error"), (404, "not_ready"), (503, "server_error")],
)
def test_push_http_results(database, caplog, code, result):
    sent = []

    def send(conn, payload, timeout):
        assert timeout == 20
        assert conn["secret"] == "TEST-SECRET"
        sent.append(payload)
        return code

    with database() as db:
        status = stats_push.push(db, send)
        assert status["result"] == result
        assert status["http_status"] == code
        assert bool(status["last_success"]) == (code == 200)
        assert db.get(Setting, "stats_push").value["attempted_at"]
        stats_push.push(db, send)
    assert len(sent) == 2
    assert list(sent[0]) == ["snapshot"]
    assert "TEST-SECRET" not in json.dumps(sent)
    assert caplog.text.count("сервер не принял код") == (1 if code in (401, 403) else 0)


def test_network_error_no_retry_preserves_success_and_logs_no_exception(database, caplog):
    def offline(*args, **kwargs):
        raise urllib.error.URLError("TEST-SECRET https://PRIVATE-PROFILE")

    with database() as db:
        success = stats_push.push(db, lambda *args, **kwargs: 200)["last_success"]
        failed = stats_push.push(db, offline)
        assert failed["result"] == "network_error"
        assert failed["last_success"] == success
        assert failed["http_status"] is None
        assert set(db.get(Setting, "stats_push").value) == {
            "attempted_at",
            "last_success",
            "result",
            "http_status",
        }
    assert "TEST-SECRET" not in caplog.text
    assert "PRIVATE-PROFILE" not in caplog.text


def test_no_connection_and_opt_out(database, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not build or send")

    monkeypatch.setattr(stats_push, "build", forbidden)
    with database() as db:
        monkeypatch.setattr(notify, "connection", lambda: None)
        assert stats_push.push(db, forbidden) is None
        assert db.get(Setting, "stats_push") is None
        assert not stats_push.status(db)["enabled"]
        monkeypatch.setattr(notify, "connection", lambda: {"url": "https://owner", "secret": "TEST-SECRET"})
        assert stats_push.status(db)["enabled"]
        stats_push.configure(db, False)
        db.commit()
        assert stats_push.push(db, forbidden) is None
    with database() as db:
        assert not stats_push.status(db)["enabled"]


def test_metadata_too_large_is_not_sent(database, monkeypatch):
    monkeypatch.setattr(stats_push, "MAX_BYTES", 10)
    with database() as db:
        result = stats_push.push(db, lambda *args, **kwargs: pytest.fail("too large"))
    assert result["result"] == "too_large"


def test_post_contract(database, monkeypatch):
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 20
        return Response()

    monkeypatch.setattr(stats_push.urllib.request, "urlopen", urlopen)
    with database() as db:
        assert stats_push.push(db)["http_status"] == 200
    request = requests[0]
    assert request.full_url == "https://owner.example/api/ext/frameflow/stats"
    assert request.get_header("Authorization") == "Bearer TEST-SECRET"
    assert request.method == "POST"
    assert len(request.data) < 1_000_000
    assert json.loads(request.data)["snapshot"]["videos"][0]["latest"]["views"] == 59

    def not_found(*args, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(stats_push.urllib.request, "urlopen", not_found)
    with database() as db:
        assert stats_push.push(db)["result"] == "not_ready"


def test_worker_push_failure_keeps_committed_metrics(database, monkeypatch, caplog):
    monkeypatch.setattr(worker, "Session", database)

    def refresh(db):
        db.add(Metric(job_id="job", source="test-worker", values={"views": 1234}))
        return {"updated": 1, "unavailable": 0}

    def broken(db):
        raise RuntimeError("TEST-SECRET")

    monkeypatch.setattr(platforms, "refresh_metrics", refresh)
    monkeypatch.setattr(stats_push, "push", broken)
    assert worker.metrics() == {"updated": 1, "unavailable": 0}
    with database() as db:
        assert db.scalar(select(Metric).where(Metric.source == "test-worker")).values["views"] == 1234
    assert "метрики сохранены" in caplog.text
    assert "TEST-SECRET" not in caplog.text


def test_manual_refresh_and_csv_push_after_commit(database, monkeypatch):
    from backend import app as application

    monkeypatch.setattr(application, "Session", database)
    sent = []

    def send(db):
        # A separate session must see the saved update before sending.
        with database() as check:
            sent.append(stats_push.build(check))

    monkeypatch.setattr(stats_push, "push", send)

    with database.begin() as db:
        db.get(Account, "account").external_id = "UC_TEST"

    # Test actual refresh_metrics, replacing only the remote YouTube service.
    class YouTube:
        def channels(self):
            return self

        def playlistItems(self):
            return self

        def videos(self):
            return self

        def list(self, **kwargs):
            self.params = kwargs
            return self

        def execute(self):
            if self.params.get("mine"):
                return {"items": [{"id": "UC_TEST", "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}}}]}
            if "playlistId" in self.params:
                return {"items": [{"contentDetails": {"videoId": "remote-id"}}]}
            return {"items": [{"id": "remote-id", "snippet": {"channelId": "UC_TEST", "title": "Название",
                               "publishedAt": "2026-11-01T10:00:00Z"},
                               "statistics": {"viewCount": "2026", "commentCount": "5"}}]}

    monkeypatch.setattr(platforms, "youtube_service", lambda account: YouTube())
    client = TestClient(application.app)
    response = client.post("/api/metrics/refresh")
    assert response.status_code == 200
    assert response.json() == {"updated": 1, "unavailable": 0}
    assert sent[-1]["refresh"] == response.json()
    assert sent[-1]["videos"][0]["latest"]["views"] == 2026
    csv = "job_id,captured,views,likes,comments,shares\njob,2026-01-01T00:00:00Z,42,0,,\n"
    assert client.post(
        "/api/metrics/import", files={"file": ("metrics.csv", io.BytesIO(csv.encode()))}
    ).json() == {"imported": 1}
    assert len(sent) == 2
    with database() as db:
        assert (
            db.scalar(select(Metric).where(Metric.source == "Импорт CSV пользователя")).values["views"] == 42
        )
    assert client.post("/api/metrics/import", files={"file": ("bad.csv", b"invalid")}).status_code == 400
    assert len(sent) == 2
    assert client.patch("/api/stats-push", json={"enabled": False}).status_code == 200
    with database() as db:
        assert not stats_push.status(db)["enabled"]


def test_upcoming_lists_waiting_jobs_without_private_fields(database):
    with database.begin() as db:
        job = db.get(Job, "job")
        job.status, job.scheduled = "scheduled", "2026-11-10T15:00:00+00:00"
    with database() as db:
        snapshot = stats_push.build(db)
    assert snapshot["upcoming"] == [
        {"time": "2026-11-10T15:00:00+00:00", "status": "scheduled", "platform": "youtube", "account": "Канал",
         "title": "Название"}
    ]
    assert "PRIVATE" not in json.dumps(snapshot["upcoming"], ensure_ascii=False)

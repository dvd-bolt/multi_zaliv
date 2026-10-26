import base64
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import gemini
from backend.app import app

GOOD = {
    "title": "Неожиданный поворот",
    "description": "Обычный разговор идёт не по плану.",
    "hashtags": ["#мультфильмы", "#юмор"],
    "uncertain": True,
}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(gemini, "TASKS", {})
    monkeypatch.setattr(gemini, "COOLDOWNS", {})
    monkeypatch.setattr(gemini, "key", lambda: "synthetic-secret")


def answer(value=GOOD, finish="STOP"):
    return httpx.Response(
        200,
        json={"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(value)}]}}]},
    )


@pytest.mark.parametrize("quotas", [0, 1, 2, 3, 4])
def test_quota_chain_is_ordered_and_bounded(monkeypatch, quotas):
    calls = []

    def post(url, **kwargs):
        calls.append(url.split("/models/")[1].split(":")[0])
        assert kwargs["headers"]["x-goog-api-key"] == "synthetic-secret"
        assert "key=" not in url
        return httpx.Response(429) if len(calls) <= quotas else answer()

    monkeypatch.setattr(gemini.httpx, "post", post)
    if quotas == 4:
        with pytest.raises(ValueError, match="Все модели"):
            gemini.generate([{"text": "synthetic"}])
        with pytest.raises(ValueError, match="Все модели"):
            gemini.generate([])
    else:
        result = gemini.generate([{"text": "synthetic"}])
        assert result["model"] == gemini.MODELS[quotas]
    assert calls == list(gemini.MODELS[: min(quotas + 1, 4)])


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_errors_do_not_burn_other_models_or_echo_secrets(monkeypatch, status):
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        return httpx.Response(status, json={"error": {"message": "synthetic-secret"}})

    monkeypatch.setattr(gemini.httpx, "post", post)
    with pytest.raises(ValueError) as error:
        gemini.generate([])
    assert "synthetic-secret" not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("failure", [408, 500, 502, 503, 504, "timeout", "connection"])
def test_unavailable_primary_falls_back_in_order(monkeypatch, failure):
    calls, updates = [], []

    def post(url, **kwargs):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic-secret")
            if failure == "connection":
                raise httpx.ConnectError("synthetic-secret")
            return httpx.Response(failure)
        return answer()

    monkeypatch.setattr(gemini.httpx, "post", post)
    result = gemini.generate([], progress=updates.append)
    assert result["model"] == gemini.MODELS[1] and calls == list(gemini.MODELS[:2])
    assert result["failures"][0]["model"] == gemini.MODELS[0]
    assert len(updates) == 2 and "synthetic-secret" not in json.dumps(result)


def test_all_timeouts_have_bounded_safe_diagnostics(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        raise httpx.ReadTimeout("synthetic-secret")

    monkeypatch.setattr(gemini.httpx, "post", post)
    with pytest.raises(gemini.ModelsUnavailable) as error:
        gemini.generate([])
    assert len(calls) == 4 and len(error.value.attempts) == 4
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        answer(finish="MAX_TOKENS"),
        answer({**GOOD, "hashtags": ["#unrelated tag"]}),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    ],
)
def test_invalid_or_blocked_output_never_becomes_caption(monkeypatch, response):
    monkeypatch.setattr(gemini.httpx, "post", lambda *_, **__: response)
    with pytest.raises(ValueError, match="корректное описание"):
        gemini.generate([])


def test_source_preparation_uses_video_and_audio_then_removes_temp(monkeypatch, tmp_path):
    paths = []

    def transcode(args, **kwargs):
        assert "0:a:0?" in args and "0:v:0" in args
        target = gemini.Path(args[-1])
        target.write_bytes(b"synthetic-video-audio")
        paths.append(target)

    monkeypatch.setattr(gemini, "command", transcode)
    monkeypatch.setattr(gemini, "binary", lambda _: "synthetic-ffmpeg")

    def generate(parts):
        assert base64.b64decode(parts[0]["inlineData"]["data"]) == b"synthetic-video-audio"
        return GOOD

    monkeypatch.setattr(gemini, "generate", generate)
    assert gemini.analyze(tmp_path / "original.mp4", 25) == GOOD
    assert not paths[0].exists()
    with pytest.raises(ValueError, match="5 минут"):
        gemini.analyze(tmp_path / "original.mp4", 301)


def test_cache_and_single_flight_avoid_repeat_paid_requests(monkeypatch, tmp_path):
    event = threading.Event()
    result = {**GOOD, "model": gemini.MODELS[0], "tried": [gemini.MODELS[0]]}
    calls = []

    def analyze(*_):
        calls.append(True)
        event.wait(3)
        return result

    monkeypatch.setattr(gemini, "analyze", analyze)
    sha = "a" * 64
    monkeypatch.setattr(gemini, "cache_path", lambda _: tmp_path / "cached.json")
    assert gemini.start("one", sha, "source", 1)["state"] == "running"
    assert gemini.start("one", sha, "source", 1)["state"] == "running"
    with pytest.raises(ValueError, match="Дождитесь"):
        gemini.start("two", sha, "source", 1)
    event.set()
    for _ in range(100):
        if gemini.task_status("one", sha)["state"] == "ready":
            break
        time.sleep(0.01)
    assert gemini.start("one", sha, "source", 1)["state"] == "ready"
    gemini.TASKS.clear()
    assert gemini.task_status("one", sha)["proposal"]["title"] == GOOD["title"]
    assert len(calls) == 1


def test_secret_validation_never_echoes_input_and_status_has_no_key(monkeypatch):
    client = TestClient(app)
    secret = "sensitive-value" * 30
    response = client.put("/api/ai/key", json={"api_key": secret})
    assert response.status_code == 422 and secret not in response.text
    monkeypatch.setattr(gemini.vault, "get", lambda _: {"api_key": "synthetic-secret"})
    response = client.get("/api/ai")
    assert response.json()["configured"] and "synthetic-secret" not in response.text


def test_caption_poll_route_is_not_shadowed_by_asset_file_route(tmp_path):
    from backend.db import Asset, Session, uid

    source = tmp_path / "route-fixture.mp4"
    source.write_bytes(b"synthetic media")
    asset_id = uid()
    with Session.begin() as db:
        db.add(
            Asset(
                id=asset_id,
                kind="video",
                name=source.name,
                path=str(source),
                sha256="b" * 64,
                media={"duration": 1},
                edit={},
            )
        )
    try:
        client = TestClient(app)
        result = client.get(f"/api/assets/{asset_id}/ai-caption")
        assert result.status_code == 200, result.text
        assert result.json() == {"state": "idle"}
        media = client.get(f"/api/assets/{asset_id}/file")
        assert media.status_code == 200 and media.content == b"synthetic media"
        assert client.get(f"/api/assets/{asset_id}/unknown-mode").status_code == 404
        assert client.get(f"/api/assets/{asset_id}/thumbnail").status_code == 404
    finally:
        with Session.begin() as db:
            db.delete(db.get(Asset, asset_id))

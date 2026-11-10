import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

from backend import clips
from backend.app import app


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "DATA", tmp_path)
    return clips.new_state("Storage regression")


@pytest.mark.skipif(os.name != "nt", reason="Windows denies replace while a reader holds the target")
def test_save_waits_for_real_windows_reader_and_preserves_valid_json(state):
    path = clips.state_path(state["id"])
    opened = threading.Event()
    release = threading.Event()

    def reader():
        with path.open("rb") as file:
            assert json.load(file)["id"] == state["id"]
            opened.set()
            release.wait(5)

    thread = threading.Thread(target=reader)
    thread.start()
    assert opened.wait(2)
    timer = threading.Timer(.08, release.set)
    timer.start()
    try:
        state["progress"] = 57
        clips.save(state)
        assert clips.load(state["id"])["progress"] == 57
        assert not list(path.parent.glob("*.tmp"))
    finally:
        release.set()
        timer.cancel()
        thread.join(2)


def test_permanent_replace_failure_keeps_old_json_and_cleans_temp(state, monkeypatch):
    path = clips.state_path(state["id"])
    before = path.read_bytes()
    attempts = []

    def denied(source, target):
        attempts.append(source)
        raise PermissionError("persistent lock")

    monkeypatch.setattr(clips.os, "replace", denied)
    monkeypatch.setattr(clips.time, "sleep", lambda _: None)
    state["progress"] = 57
    with pytest.raises(PermissionError, match="persistent lock"):
        clips.save(state)
    assert len(attempts) == 10
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_download_error_can_retry_same_source_without_deleting_partial_file(state):
    state.update(url="https://www.youtube.com/watch?v=test1234567", status="failed", error="locked")
    clips.save(state)
    partial = clips.state_path(state["id"]).parent / "source.mp4.part"
    partial.write_bytes(b"already downloaded")
    client = TestClient(app)
    response = client.post(f"/api/clips/{state['id']}/analyze")
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == state["id"] and result["status"] == "queued_download"
    assert not result["error"] and partial.read_bytes() == b"already downloaded"
    assert client.post(f"/api/clips/{state['id']}/analyze").status_code == 400


def test_missing_local_source_without_link_cannot_retry(state):
    state.update(status="failed")
    clips.save(state)
    assert TestClient(app).post(f"/api/clips/{state['id']}/analyze").status_code == 400

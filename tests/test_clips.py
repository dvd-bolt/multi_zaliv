import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import clips
from backend.app import app
from backend.config import binary
from backend.db import Asset, Job, Session
from backend.media import command, inspect


def words_for(seconds):
    """Speech every 0.5 s with a sentence end every 8 words."""
    out, t, n = [], 0.5, 0
    while t + .4 < seconds:
        n += 1
        out.append({"start": round(t, 2), "end": round(t + .4, 2), "text": f"слово{n}" + ("." if n % 8 == 0 else "")})
        t += .5
    return out


@pytest.fixture
def source(tmp_path, monkeypatch):
    video = tmp_path / "long.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=90",
             "-f", "lavfi", "-i", "sine=frequency=300:duration=90", "-shortest", "-c:v", "libx264",
             "-c:a", "aac", str(video)])
    monkeypatch.setattr(clips, "transcript_words", lambda path, key: (words_for(90), [0.1] * 90, 90.0))
    client = TestClient(app)
    with video.open("rb") as f:
        state = client.post("/api/clips/upload", files={"file": ("Подкаст.mp4", f, "video/mp4")}).json()
    yield client, state
    import shutil
    shutil.rmtree(clips.root() / state["id"], ignore_errors=True)


def test_upload_analyse_without_gemini_render_and_send_to_editor(source, monkeypatch):
    client, state = source
    monkeypatch.setattr(clips.gemini, "request_json", lambda *a, **k: (_ for _ in ()).throw(ValueError("нет ключа")))
    assert state["status"] == "queued_analyze" and state["has_video"]
    assert clips.process_one()
    state = client.get("/api/clips").json()["sources"][0]
    assert state["status"] == "ready" and state["method"] == "речь и громкость", state
    assert state["candidates"] and all(clips.MIN_CLIP <= c["end"] - c["start"] <= clips.MAX_CLIP
                                       for c in state["candidates"])
    first = state["candidates"][0]
    # the user keeps one moment and gives it a title
    body = {"candidates": [{**first, "title": "Самое смешное", "selected": True}], "crop": "blur",
            "captions": True, "style": "words"}
    state = client.put(f"/api/clips/{state['id']}", json=body).json()
    assert len(state["candidates"]) == 1
    assert client.post(f"/api/clips/{state['id']}/editor").status_code >= 400  # nothing rendered yet
    assert client.post(f"/api/clips/{state['id']}/render").json()["status"] == "queued_render"
    assert clips.process_one()
    state = client.get("/api/clips").json()["sources"][0]
    clip = state["candidates"][0]
    assert state["status"] == "ready" and clip["status"] == "done" and clip["has_output"], state
    data = json.loads(clips.state_path(state["id"]).read_text())
    info = inspect(Path(data["candidates"][0]["output_path"]), "video", "x")
    assert info["width"] == 1080 and info["height"] == 1920 and info["has_audio"]
    assert abs(info["duration"] - (clip["end"] - clip["start"])) < .5
    with Session() as db:
        jobs_before = len(db.query(Job).all())
    assets = client.post(f"/api/clips/{state['id']}/editor").json()["assets"]
    again = client.post(f"/api/clips/{state['id']}/editor").json()["assets"]
    assert len(assets) == 1 and again[0]["id"] == assets[0]["id"]  # no duplicate sources
    assert assets[0]["edit"]["title"] == "Самое смешное"
    with Session() as db:
        assert db.get(Asset, assets[0]["id"]) and len(db.query(Job).all()) == jobs_before  # nothing published
    assert client.delete(f"/api/clips/{state['id']}").status_code >= 400  # files are used by the editor


def test_gemini_moments_are_snapped_to_sentences(source, monkeypatch):
    client, state = source
    answer = {"moments": [{"start": 9.7, "end": 41.2, "title": "Поворот", "reason": "сюрприз", "score": 8},
                          {"start": 30, "end": 50, "title": "Пересекается", "score": 3}]}
    monkeypatch.setattr(clips.gemini, "request_json",
                        lambda *a, **k: (json.dumps(answer, ensure_ascii=False), "gemini-test", [], []))
    assert clips.process_one()
    state = client.get("/api/clips").json()["sources"][0]
    assert state["method"] == "Gemini test"
    assert [c["title"] for c in state["candidates"]] == ["Поворот"]  # the overlapping moment is dropped
    c = state["candidates"][0]
    sentence_starts = {w["start"] for i, w in enumerate(words_for(90)) if i % 8 == 0}
    assert round(c["start"] + .15, 2) in sentence_starts


def test_bad_lengths_and_links_are_refused(source):
    client, state = source
    assert client.post("/api/clips/link", json={"url": "file:///etc/passwd"}).status_code >= 400
    clips.process_one()
    state = client.get("/api/clips").json()["sources"][0]
    c = state["candidates"][0]
    short = {"candidates": [{**c, "end": c["start"] + 3}], "crop": "center", "captions": False, "style": "words"}
    assert client.put(f"/api/clips/{state['id']}", json=short).status_code >= 400


def test_clip_cues_are_relative_and_grouped():
    cues = clips.clip_cues(words_for(30), 10.0, 20.0)
    assert cues[0]["start"] >= 0 and cues[-1]["end"] <= 10.05
    assert all(len(c["words"]) <= 6 for c in cues)
    assert clips.clip_cues([], 0, 10) == []

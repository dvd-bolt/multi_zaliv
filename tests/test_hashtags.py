import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.hashtags import DEFAULT_HASHTAGS, with_defaults
from backend.schemas import Edit


def test_fixed_tags_are_case_insensitive_and_match_full_tokens():
    source = "Сцена #МУЛЬТИКИ #мультфильмы_другое"
    result = with_defaults(source)
    assert result.startswith(source)
    assert result.count("#МУЛЬТИКИ") == 1 and "\n#мультфильмы #юмор" in result
    assert with_defaults(result) == result


def test_fixed_tags_work_without_ai_and_preserve_legacy_opt_out():
    edit = Edit(caption="Смешной момент", auto_hashtags=True, buba_mode=True)
    assert all(tag in edit.caption for tag in (*DEFAULT_HASHTAGS, "#бубавпн"))
    assert Edit.model_validate(edit.model_dump()).caption == edit.caption
    assert Edit(caption="Без автоматики").caption == "Без автоматики"
    assert "#бубавпн" not in Edit(auto_hashtags=True).caption
    with pytest.raises(ValueError):
        Edit(caption="a" * 2199, auto_hashtags=True)


def test_import_has_visible_tags_and_queue_config_preserves_them(tmp_path):
    from sqlalchemy import select

    from backend.config import binary
    from backend.db import Account, Asset, Job, Session, Variant
    from backend.media import command

    source = tmp_path / "tags.mp4"
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=90x160:rate=2:duration=3",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    client = TestClient(app)
    with source.open("rb") as file:
        response = client.post("/api/assets?kind=video", files={"file": (source.name, file, "video/mp4")})
    assert response.status_code == 200
    asset = response.json()
    assert asset["edit"]["auto_hashtags"]
    assert all(tag in asset["edit"]["caption"] for tag in DEFAULT_HASHTAGS)
    account = client.post("/api/accounts", json={"name": "synthetic hashtags", "platform": "youtube"}).json()
    result = client.post("/api/queue", json={"asset_ids": [asset["id"]], "account_ids": [account["id"]]})
    assert result.status_code == 200
    jid = result.json()["created"][0]
    try:
        with Session() as db:
            config = db.scalar(select(Variant).join(Job).where(Job.id == jid)).config
            assert config["caption"] == asset["edit"]["caption"]
    finally:
        with Session.begin() as db:
            job = db.get(Job, jid)
            variant = db.get(Variant, job.variant_id)
            db.delete(job)
            db.flush()
            db.delete(variant)
            db.flush()
            db.delete(db.get(Asset, asset["id"]))
            db.delete(db.get(Account, account["id"]))

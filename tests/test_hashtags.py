import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.hashtags import (
    DEFAULT_HASHTAGS,
    MAX_TAGS,
    detect,
    present,
    title_from,
    with_defaults,
    youtube_tags,
)
from backend.schemas import Edit


def test_genre_tags_are_case_insensitive_limited_and_idempotent():
    source = "Сцена #ХОРРОР #хоррор_другое"
    result = with_defaults(source, "horror")
    assert result.startswith(source)
    assert "#хоррор" not in result[len(source) :].lower().split()  # already present in another case
    assert len(present(result)) == MAX_TAGS
    assert with_defaults(result, "horror") == result


def test_auto_genre_from_title_and_text():
    assert detect("Шахта №4: Не свети вверх") == "horror"
    assert detect("Смешной прикол с котом") == "comedy"
    assert detect("") == "story"
    assert "#хоррор" in with_defaults("История о людях в лифте. Страшно.", "auto")
    assert (
        title_from("Герои прячутся в темноте. Потом выходят. #анимация", "x.mp4")
        == "Герои прячутся в темноте."
    )
    assert title_from("#бубавпн", "my_episode.mp4") == "my episode"
    assert youtube_tags("Текст #бубавпн", "horror")[:2] == ["бубавпн", "хоррор"]


def test_fixed_tags_work_without_ai_and_preserve_legacy_opt_out():
    edit = Edit(caption="Смешной момент", auto_hashtags=True, buba_mode=True)
    assert all(tag in edit.caption for tag in ("#юмор", "#бубавпн"))
    assert "#мультфильмы" not in edit.caption
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

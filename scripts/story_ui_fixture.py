"""Disposable UI acceptance server. No worker publishers, credentials, or network generation."""

import json
import threading
import time
from pathlib import Path

import uvicorn

from backend import stories, story_captions
from backend.analytics import record
from backend.app import app
from backend.config import DATA, ROOT, binary
from backend.db import Account, ChannelVideo, Session, Setting, Story, StoryFile, uid
from backend.media import command
from backend.story_tts import DEFAULT_PROFILE

assert DATA != ROOT / "data"
story_captions.DATA = ROOT / "data"  # Reuse only the downloaded public model, never secrets.
with Session.begin() as db:
    db.get(Setting, "application").value = {"paused": False, "buba_required": False}
    from datetime import UTC, datetime, timedelta

    published = datetime.now(UTC) - timedelta(days=3)
    for i in range(3):
        a = Account(id=uid(), platform="youtube" if i < 2 else "tiktok", name=f"Контрольный канал {i+1}")
        db.add(a)
        db.flush()
        if i == 2:
            continue
        for j in range(3):
            video_id = f"example{i}{j}"
            db.add(ChannelVideo(account_id=a.id, external_id=video_id, history=[], present=True,
                                details={"title": f"Контрольный ролик {j+1}", "visibility": "public",
                                         "published_at": published.isoformat(), "values": {}}))
            record(db, a.id, video_id, published.isoformat(), "YouTube snippet.publishedAt", [
                {"captured": (published + timedelta(hours=h, minutes=2)).isoformat(),
                 "values": {"views": v*(j+1), "likes": j+1, "comments": 0}, "source": "Контрольные наблюдения"}
                for h, v in [(1, 100), (24, 1000), (48, 1200)]])
bg = DATA / "background.mp4"
command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=90x160:rate=10:duration=60", "-c:v", "libx264", str(bg)])
real = json.loads((ROOT / "docs/validation/averages-stories/real-tts.json").read_text(encoding="utf-8"))
caption_file = ROOT / "docs/validation/averages-stories/real-captions.json"
captions = json.loads(caption_file.read_text(encoding="utf-8")) if caption_file.exists() else []
with Session.begin() as db:
    for category in ("cars", "carpets", "balls"):
        db.add(StoryFile(id=uid(), kind="background", name="Демонстрационный фон " + category,
                         category=category, path=str(bg), sha256="fixture-" + category,
                         media={"duration": 60}))
    parts = []
    for item in real["parts"]:
        path = Path(item["audio"])
        audio_id = uid()
        info = stories.audio_info(path)
        db.add(StoryFile(id=audio_id, kind="audio", name=f"Озвучка {item['number']}.wav",
                         path=str(path), sha256=uid(), media=info))
        cue = next((c["cues"] for c in captions if c["part"] == item["number"]), [])
        parts.append({"id": uid(), "text": item["text"], "audio_id": audio_id,
                      "audio_path": str(path), "audio_origin": "import", "start": 0,
                      "end": info["duration"], "duration": info["duration"], "cues": cue,
                      "captions_reviewed": bool(cue)})
    story = Story(title="Тест: свадебный альбом", document={"parts": parts, "profile": DEFAULT_PROFILE,
                   "categories": ["cars"], "loop": False, "width": 720, "subtitles": bool(captions)})
    db.add(story)


def work():
    while True:
        try:
            stories.process_one()
            (DATA / "worker.heartbeat").touch()
        except Exception as error:
            print(type(error).__name__, flush=True)
        time.sleep(.3)


threading.Thread(target=work, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=8794, log_level="error")

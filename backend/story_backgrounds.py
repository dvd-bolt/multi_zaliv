"""Story backgrounds from a link (YouTube and other sites supported by yt-dlp).

The video is downloaded without sound (the story voice replaces it) up to 1080p and stored in the same
background library as uploaded files; random fragments are chosen later by the usual background logic.
Downloads run in a thread; their state lives in memory and is shown in the stories listing.
"""

import hashlib
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import binary
from .db import uid

LOCK = threading.Lock()
TASKS = {}
MAX_SECONDS = 3 * 3600
MAX_BYTES = 4 * 1024**3
FORMAT = "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]/bv*/b"


def check_url(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 500:
        raise ValueError("Нужна ссылка вида https://…")
    return url.strip()


def listing():
    with LOCK:
        return [dict(t) for t in sorted(TASKS.values(), key=lambda t: t["created"], reverse=True)[:10]]


def update(key, **values):
    with LOCK:
        TASKS[key].update(values)


def start(url, category, folder, register):
    """register(path, name, sha256) → StoryFile data; called once the file is complete."""
    url = check_url(url)
    key = uid()
    with LOCK:
        if any(t["url"] == url and t["status"] == "running" for t in TASKS.values()):
            raise ValueError("Эта ссылка уже скачивается")
        TASKS[key] = {"id": key, "url": url, "category": category, "status": "running", "progress": 0,
                      "detail": "Подготовка", "error": "", "created": time.time(), "file_id": None}
    threading.Thread(target=run, args=(key, url, category, folder, register), daemon=True).start()
    with LOCK:
        return dict(TASKS[key])


def run(key, url, category, folder, register):
    try:
        import yt_dlp
    except ImportError:
        update(key, status="failed", error="Не установлен yt-dlp: запустите scripts/setup.ps1")
        return
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}-link"

    def hook(state):
        if state.get("status") == "downloading":
            total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
            done = state.get("downloaded_bytes") or 0
            if total > MAX_BYTES:
                raise ValueError("Файл больше 4 ГБ")
            update(key, progress=round(100 * done / total) if total else 0, detail="Скачивание")

    def short_enough(info, *, incomplete):
        duration = info.get("duration")
        if duration and duration > MAX_SECONDS:
            return "Видео длиннее 3 часов — выберите короче"
        return None

    options = {"format": FORMAT, "outtmpl": str(target) + ".%(ext)s", "noplaylist": True, "quiet": True,
               "no_warnings": True, "progress_hooks": [hook], "match_filter": short_enough,
               "ffmpeg_location": binary("ffmpeg"), "merge_output_format": "mp4", "retries": 3}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
        if not info:
            raise ValueError("Видео не скачано (ограничение длины или доступ)")
        files = [p for p in folder.glob(f"{key}-link.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if not files:
            raise ValueError("Видео не скачано (ограничение длины или доступ)")
        path = files[0]
        update(key, detail="Проверка файла", progress=100)
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024**2), b""):
                digest.update(block)
        name = (info.get("title") or "Фон по ссылке")[:80] + path.suffix
        row = register(path, name, digest.hexdigest())
        update(key, status="done", detail="Готово", file_id=row["id"], name=name)
    except Exception as error:  # every failure is shown to the user; partial files are removed
        for leftover in folder.glob(f"{key}-link*"):
            Path(leftover).unlink(missing_ok=True)
        message = str(error).replace("ERROR: ", "")[-300:] or "Не удалось скачать"
        update(key, status="failed", error=message, detail="Ошибка")

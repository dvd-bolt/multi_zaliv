"""Durable story drafts, local media and worker tasks; publishing reuses the existing editor."""

import copy
import hashlib
import json
import math
import random
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update

from . import story_backgrounds, story_captions, story_script, story_tts
from .config import DATA, binary
from .db import Asset, Session, Setting, Story, StoryFile, StoryTask, now, uid
from .media import NO_WINDOW, command, inspect
from .schemas import Edit

router = APIRouter(prefix="/api/stories")
CATEGORIES = ("balls", "carpets", "cars")
CATEGORY_NAMES = {"Шарики": "balls", "Ковры": "carpets", "Мойка ковров": "carpets",
                  "Машины": "cars", "Мойка машин": "cars"}
ACTIVE = ("queued", "running", "cancelling")


@contextmanager
def writing():
    # Serialize revision/active-task checks with the corresponding write.
    with Session.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        yield db


def directory():
    path = DATA / "stories"
    path.mkdir(parents=True, exist_ok=True)
    return path


def required(db, model, key):
    row = db.get(model, key)
    if row is None:
        raise HTTPException(404, "История, часть или файл не найдены")
    return row


def audio_info(path):
    data = json.loads(command([binary("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                               "-of", "json", str(path)]))
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        raise ValueError("В файле нет звуковой дорожки")
    length = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(length) or not 0 < length <= 3600:
        raise ValueError("Аудио должно иметь длительность от 0 до 3600 секунд")
    return {"duration": length}


def file_data(row):
    return {k: getattr(row, k) for k in ("id", "name", "kind", "category", "media")}


def public(story):
    doc = copy.deepcopy(story.document)
    # Stories saved before 2.0 keep their look until the user changes the settings.
    for key, value in (("caption_style", "classic"), ("hook", ""), ("next_hint", False)):
        doc.setdefault(key, value)
    for part in doc["parts"]:
        for key in ("audio_path", "output_path"):
            path = part.pop(key, None)
            part["has_audio" if key == "audio_path" else "has_output"] = bool(path and Path(path).is_file())
    return {"id": story.id, "title": story.title, "revision": story.revision, "updated": story.updated, **doc}


def task_data(t):
    return {k: getattr(t, k) for k in ("id", "story_id", "revision", "kind", "parts", "status",
                                     "progress", "detail", "error", "created", "updated")}


def busy(db, key):
    if db.scalar(select(StoryTask.id).where(StoryTask.story_id == key, StoryTask.status.in_(ACTIVE))):
        raise HTTPException(409, "Сначала завершите или отмените текущую обработку истории")


class Profile(BaseModel):
    model: Literal["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"] = story_tts.MODELS[0]
    voice: Literal["Orus", "Charon", "Kore", "Aoede", "Puck", "Leda", "Algenib", "Sulafat"] = "Orus"
    style: str = Field(story_tts.DEFAULT_PROFILE["style"], min_length=1, max_length=600)
    language: Literal["ru"] = "ru"


class PartInput(BaseModel):
    id: str | None = None
    text: str = Field("", max_length=12000)
    audio_id: str | None = None
    start: float = Field(0, ge=0, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, allow_inf_nan=False)


class Draft(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    revision: int = Field(ge=1)
    parts: list[PartInput] = Field(min_length=2, max_length=3)
    profile: Profile = Field(default_factory=Profile)
    categories: list[Literal["balls", "carpets", "cars"]] = Field(default_factory=lambda: ["cars"])
    loop: bool = False
    subtitles: bool = True
    width: Literal[720, 1080] = 1080
    caption_style: Literal["words", "classic"] = "words"
    hook: str = Field("", max_length=80)
    next_hint: bool = True
    source_script: str = Field("", max_length=40000)


def parse_script(value):
    value = value.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > 40000:
        raise ValueError("Сценарий слишком длинный")
    marks = list(re.finditer(r"^\[Часть\s+(\d+)\]\s*$", value, re.MULTILINE))
    if len(marks) not in (2, 3) or [int(m[1]) for m in marks] != list(range(1, len(marks)+1)):
        raise ValueError("Нужны последовательные [Часть 1], [Часть 2] и при необходимости [Часть 3]")
    meta = {}
    for line in value[:marks[0].start()].strip().splitlines():
        if not line.strip():
            continue
        name, sep, data = line.partition(":")
        if not sep or name.strip() not in ("Название", "Частей", "Профиль голоса", "Фон"):
            raise ValueError("До первой части укажите поля Название, Частей, Профиль голоса, Фон")
        if name.strip() in meta:
            raise ValueError("Повтор поля сценария: " + name.strip())
        meta[name.strip()] = data.strip()
    if not meta.get("Название") or meta.get("Частей") != str(len(marks)):
        raise ValueError("Укажите Название и правильное количество Частей")
    parts = [value[m.end():marks[i+1].start() if i+1 < len(marks) else None].strip()
             for i, m in enumerate(marks)]
    if any(not p or re.search(r"^\[Часть", p, re.MULTILINE) for p in parts):
        raise ValueError("В каждой части должен быть произносимый текст без лишних маркеров")
    category = CATEGORY_NAMES.get(meta.get("Фон"))
    if meta.get("Фон") and not category:
        raise ValueError("Фон: Шарики, Ковры или Мойка машин")
    if meta.get("Профиль голоса") not in (None, "", "Истории — основной"):
        raise ValueError("В первой версии профиль называется «Истории — основной»; его модель и голос выбираются во вкладке «Голос»")
    return {"title": meta["Название"], "parts": [{"text": p} for p in parts],
            "categories": [category] if category else [], "profile_name": meta.get("Профиль голоса", "")}


@router.get("")
def listing():
    with Session() as db:
        return {"stories": [public(s) for s in db.scalars(select(Story).order_by(Story.updated.desc())).all()],
                "files": [file_data(f) for f in db.scalars(select(StoryFile).order_by(StoryFile.created.desc())).all()],
                "tasks": [task_data(t) for t in db.scalars(select(StoryTask).order_by(StoryTask.created.desc()).limit(50)).all()],
                "models": story_tts.MODELS, "voices": story_tts.VOICES,
                "downloads": story_backgrounds.listing(),
                "default_profile": story_tts.DEFAULT_PROFILE}


@router.post("")
def create():
    with writing() as db:
        saved_profile = db.get(Setting, "story_voice_profile")
        s = Story(title="Новая история", document={"parts": [{"id": uid(), "text": ""} for _ in range(2)],
                  "profile": saved_profile.value if saved_profile else story_tts.DEFAULT_PROFILE,
                  "categories": ["cars"], "loop": False,
                  "subtitles": True, "width": 1080, "caption_style": "words", "hook": "",
                  "next_hint": True})
        db.add(s)
        db.flush()
        return public(s)


class ScriptInput(BaseModel):
    text: str = Field(max_length=40000)


@router.post("/parse")
def parse(request: ScriptInput):
    return parse_script(request.text)


PROBE_TEXT = "За день до свадьбы я нашёл на столе записку. В ней было всего три слова: не верь фотографиям."


@router.post("/voice/probe")
def voice_probe(request: Profile, generate: bool = False):
    """A short sample of a voice profile. Without generate=true it only reports whether a sample is
    cached; with it — at most one TTS request (an existing sample is reused for free)."""
    profile = request.model_dump()
    path = story_tts.cache_file(PROBE_TEXT, profile)
    if generate and not path.exists():
        path = story_tts.generate(PROBE_TEXT, profile)
    return {"exists": path.exists(), "key": story_tts.cache_key(PROBE_TEXT, profile)}


@router.get("/voice/probe/{key}")
def voice_probe_audio(key: str):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise HTTPException(404)
    path = DATA / "stories" / "voice-cache" / (key + ".wav")
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@router.get("/ideas")
def ideas():
    """Trending competitor topics collected on the owner's server (through the Telegram bot connection)."""
    from . import notify

    conn = notify.connection()
    if not conn:
        raise ValueError("Подключите Telegram-бота (команда /frameflow, затем код в настройках FrameFlow)")
    try:
        response = httpx.get(conn["url"] + "/ideas", headers={"Authorization": "Bearer " + conn["secret"]},
                             timeout=15)
    except httpx.HTTPError:
        raise ValueError("Сервер с идеями сейчас недоступен") from None
    if response.status_code in (401, 403):
        raise ValueError("Сервер не принял код бота. Получите новый командой /frameflow")
    if not response.is_success:
        raise ValueError(f"Сервер идей ответил HTTP {response.status_code}")
    return {"ideas": response.json().get("ideas", [])[:20]}


class ThemeInput(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    parts: Literal[2, 3] = 3
    notes: str = Field("", max_length=600)
    category: Literal["balls", "carpets", "cars", ""] = ""


@router.post("/script/draft")
def script_draft(request: ThemeInput):
    """A Gemini draft for the import dialog: shown to the user, never saved or voiced by itself."""
    name = next((k for k, v in CATEGORY_NAMES.items() if v == request.category), "")
    result = story_script.write(request.theme.strip(), request.parts, request.notes.strip(), name)
    parse_script(result["script"])  # the draft must pass the same contract as a user scenario
    return result


@router.get("/models")
def models():
    return {"available": story_tts.available_models()}


@router.post("/files")
def upload(file: Annotated[UploadFile, File()], kind: Literal["background", "audio"],
           category: Literal["balls", "carpets", "cars"] = "cars"):
    suffix = Path(file.filename or "").suffix.lower()
    allowed = (".mp4", ".mov", ".mkv", ".webm") if kind == "background" else (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".mp4")
    if suffix not in allowed:
        raise ValueError("Неподдерживаемый формат файла")
    key = uid()
    folder = directory() / "inputs"
    folder.mkdir(exist_ok=True)
    path = folder / (key + suffix)
    size, digest = 0, hashlib.sha256()
    try:
        with path.open("wb") as output:
            while block := file.file.read(1024**2):
                size += len(block)
                if size > 4 * 1024**3:
                    raise ValueError("Файл больше 4 ГБ")
                digest.update(block)
                output.write(block)
        media = inspect(path, "video", key) if kind == "background" else audio_info(path)
        with writing() as db:
            row = StoryFile(id=key, kind=kind, category=category if kind == "background" else "",
                            name=Path(file.filename or "Файл").name, path=str(path),
                            sha256=digest.hexdigest(), media=media)
            db.add(row)
            db.flush()
            return file_data(row)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


class LinkInput(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    category: Literal["balls", "carpets", "cars"] = "cars"


@router.post("/files/link")
def upload_link(request: LinkInput):
    """Background video by link: downloaded in a thread, then added to the library like an upload."""
    def register(path, name, digest):
        key = path.name.split("-link")[0]
        media = inspect(path, "video", key)
        with writing() as db:
            row = StoryFile(id=key, kind="background", category=request.category, name=name, path=str(path),
                            sha256=digest, media=media)
            db.add(row)
            db.flush()
            return file_data(row)

    return story_backgrounds.start(request.url, request.category, directory() / "inputs", register)


@router.get("/files/{key}")
def source_file(key: str):
    with Session() as db:
        path = Path(required(db, StoryFile, key).path)
        if not path.is_file():
            raise HTTPException(404, "Исходный файл недоступен")
        return FileResponse(path)


@router.put("/{key}")
def save(key: str, request: Draft):
    with writing() as db:
        story = required(db, Story, key)
        busy(db, key)
        if request.revision != story.revision:
            raise HTTPException(409, "Черновик изменился. Обновите историю перед сохранением")
        old = story.document
        previous = {p["id"]: p for p in old["parts"]}
        parts, ids = [], set()
        for item in request.parts:
            if item.id and item.id not in previous or item.id in ids:
                raise ValueError("Неизвестная или повторная часть истории")
            part = copy.deepcopy(previous.get(item.id, {"id": uid(), "text": ""}))
            ids.add(part["id"])
            changed_voice = item.text != part["text"] or request.profile.model_dump() != old["profile"]
            changed_audio = item.audio_id != part.get("audio_id") or item.start != part.get("start", 0) or item.end != part.get("end")
            if item.audio_id:
                audio = required(db, StoryFile, item.audio_id)
                if audio.kind != "audio":
                    raise ValueError("Выберите файл озвучки")
                end = item.end or audio.media["duration"]
                if not 0 <= item.start < end <= audio.media["duration"] + .01:
                    raise ValueError("Проверьте границы аудио в части")
                part.update(audio_id=item.audio_id, audio_path=audio.path, start=item.start, end=end,
                            duration=end-item.start, audio_origin="import")
            elif changed_voice or changed_audio:
                for field in ("audio_id", "audio_path", "duration", "audio_key", "audio_origin", "start", "end"):
                    part.pop(field, None)
            if changed_voice or changed_audio:
                part.pop("cues", None)
                part.pop("captions_reviewed", None)
            render_changed = changed_voice or changed_audio or story.title != request.title or any(
                old.get(k) != getattr(request, k)
                for k in ("categories", "loop", "subtitles", "width", "caption_style", "hook", "next_hint"))
            render_changed = render_changed or [p.get("id") for p in old["parts"]] != [p.id for p in request.parts]
            if render_changed:
                for field in ("output_path", "asset_id"):
                    part.pop(field, None)
            if old.get("categories") != request.categories:
                part.pop("background", None)
            part["text"] = item.text.strip()
            parts.append(part)
        story.title = request.title.strip()
        if not story.title:
            raise ValueError("Укажите название истории")
        story.document = {**request.model_dump(exclude={"revision", "title", "parts"}), "parts": parts}
        saved_profile = db.get(Setting, "story_voice_profile")
        if saved_profile:
            saved_profile.value = request.profile.model_dump()
        else:
            db.add(Setting(key="story_voice_profile", value=request.profile.model_dump()))
        story.revision += 1
        story.updated = now()
        return public(story)


class TaskRequest(BaseModel):
    kind: Literal["voice", "captions", "render", "pipeline"]
    parts: list[str] = Field(min_length=1, max_length=3)
    revision: int
    max_requests: int = Field(0, ge=0, le=3)


@router.post("/{key}/tasks")
def enqueue(key: str, request: TaskRequest):
    with writing() as db:
        story = required(db, Story, key)
        busy(db, key)
        if request.revision != story.revision:
            raise HTTPException(409, "Сначала обновите и сохраните историю")
        parts = {p["id"]: p for p in story.document["parts"]}
        if len(set(request.parts)) != len(request.parts) or any(i not in parts for i in request.parts):
            raise ValueError("Выберите существующие части")
        calls = 0
        for key_part in request.parts:
            p = parts[key_part]
            has_audio = bool(p.get("audio_path")) and Path(p["audio_path"]).is_file()
            if request.kind == "pipeline":
                # One button: voice what has no audio, captions, render. Captions are accepted automatically
                # only when they match the script word for word; otherwise the part stops for a review.
                if not has_audio:
                    if not p["text"].strip():
                        raise ValueError("Добавьте текст каждой выбранной части")
                    calls += not story_tts.cache_file(p["text"], story.document["profile"]).exists()
                continue
            if request.kind == "voice":
                if not p["text"].strip():
                    raise ValueError("Добавьте текст каждой выбранной части")
                calls += not story_tts.cache_file(p["text"], story.document["profile"]).exists()
            elif not has_audio:
                raise ValueError("Сначала добавьте или создайте озвучку выбранных частей")
            if request.kind == "render" and story.document["subtitles"] and not p.get("captions_reviewed"):
                raise ValueError("Проверьте и подтвердите субтитры либо выключите их для этого экспорта")
        if calls > request.max_requests:
            raise ValueError(f"Нужно новых запросов TTS: {calls}; разрешено: {request.max_requests}")
        task = StoryTask(story_id=story.id, revision=story.revision, kind=request.kind, parts=request.parts)
        db.add(task)
        db.flush()
        return {**task_data(task), "new_requests": calls}


@router.post("/tasks/{key}/cancel")
def cancel(key: str):
    with writing() as db:
        task = required(db, StoryTask, key)
        if task.status == "queued":
            task.status, task.detail = "cancelled", "Отменено до начала"
        elif task.status == "running":
            task.status, task.detail = "cancelling", "Отмена после текущей операции; новый запрос не начнётся"
        task.updated = now()
        return task_data(task)


class CaptionsInput(BaseModel):
    revision: int
    srt: str = Field(max_length=100000)
    reviewed: bool = False


@router.put("/{key}/parts/{part_id}/captions")
def captions_save(key: str, part_id: str, request: CaptionsInput):
    with writing() as db:
        story = required(db, Story, key)
        busy(db, key)
        if story.revision != request.revision:
            raise HTTPException(409, "Черновик изменился")
        doc = copy.deepcopy(story.document)
        part = next((p for p in doc["parts"] if p["id"] == part_id), None)
        if not part or not part.get("duration"):
            raise ValueError("Сначала добавьте озвучку части")
        part["cues"] = story_captions.parse_srt(request.srt, part["duration"])
        part["captions_reviewed"] = request.reviewed
        part.pop("output_path", None)
        part.pop("asset_id", None)
        story.document, story.updated = doc, now()
        story.revision += 1
        return public(story)


@router.post("/{key}/parts/{part_id}/background")
def replace_background(key: str, part_id: str):
    with writing() as db:
        story = required(db, Story, key)
        busy(db, key)
        doc = copy.deepcopy(story.document)
        part = next((p for p in doc["parts"] if p["id"] == part_id), None)
        if not part:
            raise ValueError("Часть не найдена")
        for field in ("background", "output_path", "asset_id"):
            part.pop(field, None)
        story.document, story.updated = doc, now()
        story.revision += 1
        return public(story)


@router.get("/{key}/parts/{part_id}/{kind}")
def output(key: str, part_id: str, kind: Literal["audio", "video", "srt"]):
    with Session() as db:
        story = required(db, Story, key)
        part = next((p for p in story.document["parts"] if p["id"] == part_id), None)
        if not part:
            raise HTTPException(404, "Часть не найдена")
        if kind == "srt":
            if not part.get("cues"):
                raise HTTPException(404, "Субтитры не готовы")
            return Response(story_captions.srt(part["cues"]), media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})
        path = part.get("audio_path" if kind == "audio" else "output_path")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Файл ещё не готов")
        return FileResponse(path)


@router.post("/{key}/parts/{part_id}/editor")
def to_editor(key: str, part_id: str):
    with writing() as db:
        story = required(db, Story, key)
        busy(db, key)
        doc = copy.deepcopy(story.document)
        part = next((p for p in doc["parts"] if p["id"] == part_id), None)
        if not part or not part.get("output_path") or not Path(part["output_path"]).is_file():
            raise ValueError("Сначала соберите MP4 части")
        asset = db.get(Asset, part.get("asset_id")) if part.get("asset_id") else None
        if asset is None:
            asset_id = uid()
            media = inspect(Path(part["output_path"]), "video", asset_id)
            index = doc["parts"].index(part) + 1
            title = f"{story.title} · Часть {index} из {len(doc['parts'])}"
            edit = Edit(title=title[:90], caption=title, genre="story", auto_hashtags=False,
                        saturation=100, warmth=0).model_dump()
            asset = Asset(id=asset_id, kind="video", name=title + ".mp4", path=part["output_path"],
                          sha256=hashlib.sha256(Path(part["output_path"]).read_bytes()).hexdigest(),
                          media=media, edit=edit)
            db.add(asset)
            part["asset_id"] = asset_id
            story.document = doc
            db.flush()
        return {k: getattr(asset, k) for k in ("id", "kind", "name", "media", "edit", "created")}


def choose_background(db, story, part):
    if part.get("background"):
        choice = part["background"]
        row = required(db, StoryFile, choice["file_id"])
        if row.sha256 != choice["sha256"] or not Path(row.path).is_file():
            raise ValueError("Выбранный фон изменился или недоступен; выберите другой")
        if choice["start"] + part["duration"] <= row.media["duration"] or story.document["loop"]:
            return row.path, {**choice, "end": round(choice["start"] + part["duration"], 3),
                              "loop": choice["start"] + part["duration"] > row.media["duration"]}
    files = [f for f in db.scalars(select(StoryFile).where(StoryFile.kind == "background")).all()
             if f.category in story.document["categories"] and Path(f.path).is_file()
             and (story.document["loop"] or f.media["duration"] >= part["duration"])]
    if not files:
        raise ValueError("Нет подходящего фона. Загрузите клип нужной категории/длины или разрешите цикл")
    used = []
    for s in db.scalars(select(Story).order_by(Story.updated.desc())).all():
        used += [p["background"]["file_id"] for p in s.document["parts"] if p.get("background")]
    fresh = [f for f in files if f.id not in used[:10]]
    chosen = random.SystemRandom().choice(fresh or files)
    start = random.SystemRandom().uniform(0, max(0, chosen.media["duration"] - part["duration"]))
    return chosen.path, {"file_id": chosen.id, "sha256": chosen.sha256, "start": round(start, 3),
                         "end": round(start + part["duration"], 3),
                         "loop": chosen.media["duration"] < part["duration"], "name": chosen.name}


def render_part(story, part, path, background, cancelled):
    folder = path.parent
    width, height = story.document["width"], story.document["width"] * 16 // 9
    graph = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30"
    if story.document["subtitles"]:
        ass_file = folder / (part["id"] + ".ass")
        number = next(i for i, p in enumerate(story.document["parts"], 1) if p["id"] == part["id"])
        document = story.document
        ass_file.write_text(story_captions.ass(part["cues"], number, len(document["parts"]), width, height,
                                               style=document.get("caption_style", "classic"),
                                               hook=document.get("hook", ""),
                                               next_hint=document.get("next_hint", False),
                                               duration=part["duration"]), encoding="utf-8-sig")
        graph += f",ass=filename={ass_file.name}"
    args = [binary("ffmpeg"), "-v", "error", "-y"]
    if background["loop"]:
        args += ["-stream_loop", "-1"]
    args += ["-ss", str(background["start"]), "-i", part["background_path"],
             "-ss", str(part.get("start", 0)), "-i", part["audio_path"],
             "-map", "0:v:0", "-map", "1:a:0", "-t", str(part["duration"]),
             "-vf", graph, "-af", "aresample=48000",
             "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-threads", "4",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
             "-map_metadata", "-1", str(path)]
    log = folder / (part["id"] + ".log")
    with log.open("wb") as stderr:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr, cwd=folder,
                                   creationflags=NO_WINDOW)
        began = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise InterruptedError("Рендер отменён")
                if time.monotonic() - began > max(300, part["duration"] * 30):
                    raise ValueError("Превышено время сборки части")
                time.sleep(.2)
            if process.returncode:
                raise ValueError("Не удалось собрать часть: " + log.read_text(errors="replace")[-1000:])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def recover():
    with writing() as db:
        db.execute(update(StoryTask).where(StoryTask.status.in_(["running", "cancelling"])).values(
            status="failed", error="Обработка прервана. Готовые части сохранены; повторите явно",
            detail="Нужен повтор пользователя", updated=now()))


def voice_step(part, document):
    audio = story_tts.generate(part["text"], document["profile"])
    part.update(audio_path=str(audio), duration=audio_info(audio)["duration"],
                audio_key=story_tts.cache_key(part["text"], document["profile"]), audio_origin="tts")
    for field in ("audio_id", "start", "end", "cues", "output_path", "asset_id", "captions_reviewed"):
        part.pop(field, None)


def same_words(a, b):
    def norm(value):
        return re.sub(r"[^\w]+", " ", value.casefold().replace("ё", "е")).split()
    return norm(a) == norm(b)


def captions_step(story, part, part_id, cancelled, auto=False):
    folder = directory() / story.id
    folder.mkdir(exist_ok=True)
    audio = folder / (part_id + "-clip.wav")
    command([binary("ffmpeg"), "-v", "error", "-y", "-ss", str(part.get("start", 0)),
             "-i", part["audio_path"], "-t", str(part["duration"]), "-ac", "1", "-ar", "16000", str(audio)])
    try:
        part["cues"] = story_captions.transcribe(audio, lambda _: None, cancelled, part["text"])
    finally:
        audio.unlink(missing_ok=True)
    part["captions_reviewed"] = False
    part.pop("output_path", None)
    part.pop("asset_id", None)
    if auto:
        if not same_words(" ".join(c["text"] for c in part["cues"]), part["text"]):
            raise ValueError("Субтитры разошлись со сценарием — проверьте их во вкладке «Субтитры» и соберите MP4")
        part["captions_reviewed"] = True


def render_step(story, document, part, part_id, task_id, cancelled):
    with writing() as db:
        current = required(db, Story, story.id)
        bg_path, choice = choose_background(db, current, part)
        part["background"] = choice
        # Persist the random choice before the expensive operation for reproducibility.
        current.document = copy.deepcopy(document)
    folder = directory() / story.id
    folder.mkdir(exist_ok=True)
    path = folder / (part_id + "-" + task_id + ".mp4")
    part["background_path"] = bg_path
    try:
        render_part(SimpleNamespace(document=document), part, path, choice, cancelled)
        info = inspect(path, "video", uid())
        if abs(info["duration"] - part["duration"]) > .3 or info["width"] != document["width"]:
            raise ValueError("Готовая часть не прошла проверку длительности/размера")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        part.pop("background_path", None)
    part["output_path"] = str(path)
    part.pop("asset_id", None)


def process_one():
    with writing() as db:
        task = db.scalar(select(StoryTask).where(StoryTask.status == "queued").order_by(StoryTask.created))
        if not task:
            return False
        task_id = task.id
        task.status, task.detail, task.updated = "running", "Начало обработки", now()
        story = required(db, Story, task.story_id)
        if task.revision != story.revision:
            task.status, task.error = "failed", "История изменилась; создайте новое задание"
            return True
        targets, kind, document = task.parts, task.kind, copy.deepcopy(story.document)

    def cancelled():
        with Session() as db:
            return required(db, StoryTask, task_id).status == "cancelling"

    try:
        for index, part_id in enumerate(targets):
            if cancelled():
                raise InterruptedError("Обработка отменена")
            part = next(p for p in document["parts"] if p["id"] == part_id)
            with writing() as db:
                task = required(db, StoryTask, task_id)
                number = next(i for i, p in enumerate(document["parts"], 1) if p["id"] == part["id"])
                task.detail = f"{kind}: часть {number} из {len(document['parts'])}"
                task.progress, task.updated = index / len(targets) * 100, now()
            steps = [kind]
            if kind == "pipeline":
                steps = []
                if not part.get("audio_path") or not Path(part["audio_path"]).is_file():
                    steps.append("voice")
                if document["subtitles"]:
                    steps.append("captions")
                steps.append("render")
            for step in steps:
                if kind == "pipeline":
                    with writing() as db:
                        task = required(db, StoryTask, task_id)
                        names = {"voice": "озвучка", "captions": "субтитры", "render": "сборка MP4"}
                        task.detail = f"{names[step]}: часть {number} из {len(document['parts'])}"
                        task.updated = now()
                    if cancelled():
                        raise InterruptedError("Обработка отменена")
                if step == "voice":
                    voice_step(part, document)
                elif step == "captions":
                    captions_step(story, part, part_id, cancelled, auto=kind == "pipeline")
                else:
                    render_step(story, document, part, part_id, task_id, cancelled)
                with writing() as db:
                    current = required(db, Story, story.id)
                    current.document, current.updated = copy.deepcopy(document), now()
            # A completed response is retained even if cancellation arrived during TTS.
            with writing() as db:
                current = required(db, Story, story.id)
                current.document, current.updated = copy.deepcopy(document), now()
        with writing() as db:
            task = required(db, StoryTask, task_id)
            task.status = "cancelled" if task.status == "cancelling" else "succeeded"
            task.progress, task.detail, task.updated = 100, "Готово", now()
    except Exception as error:
        with writing() as db:
            task = required(db, StoryTask, task_id)
            task.status = "cancelled" if isinstance(error, InterruptedError) else "failed"
            task.error, task.detail, task.updated = str(error)[-1200:], "Обработка остановлена", now()
    return True

"""Google TTS Interactions API. Exactly one network generation per explicit part request."""

import base64
import hashlib
import json
import wave

import httpx

from .config import DATA
from .gemini import BASE, key

MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
VOICES = ("Orus", "Charon", "Kore", "Aoede", "Puck", "Leda", "Algenib", "Sulafat")
DEFAULT_PROFILE = {"model": MODELS[0], "voice": "Orus", "language": "ru",
                   "style": "Разговорный русский рассказ от первого лица, сдержанная эмоциональность, "
                            "естественные паузы, чёткая дикция."}


def cache_key(text, profile):
    return hashlib.sha256(json.dumps({"text": text, "profile": profile, "adapter": 1},
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def cache_file(text, profile):
    return DATA / "stories" / "voice-cache" / (cache_key(text, profile) + ".wav")


def available_models():
    try:
        response = httpx.get(BASE + "/models", headers={"x-goog-api-key": key()}, timeout=20)
        if not response.is_success:
            raise ValueError("Не удалось проверить доступ к моделям Google TTS")
        names = {m.get("name", "").removeprefix("models/") for m in response.json().get("models", [])}
        return [m for m in MODELS if m in names]
    except httpx.HTTPError:
        raise ValueError("Нет связи с Google при проверке TTS") from None


def audio_blocks(value):
    if isinstance(value, dict):
        if value.get("type") == "audio" and value.get("data"):
            yield value
        else:
            for child in value.values():
                yield from audio_blocks(child)
    elif isinstance(value, list):
        for child in value:
            yield from audio_blocks(child)


def generate(text, profile):
    target = cache_file(text, profile)
    if target.exists():
        return target
    if profile.get("model") not in MODELS or profile.get("voice") not in VOICES:
        raise ValueError("Выберите поддерживаемые модель и голос TTS")
    if not text.strip() or len(text.encode("utf-8")) > 20000:
        raise ValueError("Часть должна содержать текст до 20 000 байт UTF-8; разделите длинную часть")
    payload = {
        "model": profile["model"], "store": False,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                   "annotations": [{"type": "speech_metadata", "style": profile["style"]}]}]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": profile["voice"]}]},
    }
    try:
        response = httpx.post(BASE + "/interactions", headers={"x-goog-api-key": key()},
                              json=payload, timeout=httpx.Timeout(180, connect=10), follow_redirects=False)
    except httpx.HTTPError:
        raise ValueError("Google TTS: ответ не получен. Автоповтора нет; проверьте квоту перед повтором") from None
    if not response.is_success:
        status = response.status_code
        raise ValueError(f"Google TTS вернул HTTP {status}. Проверьте доступ/квоту/модель; автоповтора нет")
    try:
        blocks = list(audio_blocks(response.json()))
        if len(blocks) != 1:
            raise ValueError("Google TTS не вернул один завершённый аудиоблок")
        block = blocks[0]
        audio = base64.b64decode(block["data"], validate=True)
        if not 44 <= len(audio) <= 100 * 1024**2:
            raise ValueError("Некорректный размер аудио Google TTS")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part.wav")
        try:
            if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
                temporary.write_bytes(audio)
            elif block.get("mime_type", "").startswith(("audio/l16", "audio/pcm")):
                with wave.open(str(temporary), "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(24000)
                    out.writeframes(audio)
            else:
                raise ValueError("Google TTS вернул неподдерживаемый формат аудио")
            with wave.open(str(temporary), "rb") as result:
                if result.getnframes() < 100 or result.getframerate() <= 0:
                    raise ValueError("Пустой ответ Google TTS")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
    except (KeyError, TypeError, json.JSONDecodeError, wave.Error):
        raise ValueError("Некорректный аудиоответ Google TTS; автоматического повтора нет") from None

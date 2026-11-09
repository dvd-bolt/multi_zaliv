"""Explicit bounded real TTS acceptance. Never run from pytest or an automatic fallback."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import story_tts  # noqa: E402

TEXTS = [
    "За день до свадьбы я нашёл на столе записку. В ней было всего три слова: не верь фотографиям. "
    "Я собирался позвонить невесте, но заметил на обороте адрес. Там находилась мастерская моего брата. "
    "Я решил сначала узнать правду.",
    "Брат встретил меня с большой коробкой. Внутри лежал восстановленный семейный альбом — наш свадебный подарок. "
    "Записка оказалась частью сюрприза: старые фотографии специально перепутали. "
    "Мы вместе рассмеялись. Хорошо, что я сначала задал вопрос, а не сделал вывод.",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--max-requests", type=int, default=0)
    args = parser.parse_args()
    if not args.real or not 1 <= args.max_requests <= 3:
        parser.error("Нужны явные --real и --max-requests 1…3 после разрешения пользователя")
    folder = ROOT / "docs/validation/averages-stories"
    folder.mkdir(parents=True, exist_ok=True)
    report_file = folder / "real-tts.json"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {
        "authorized_max_requests": args.max_requests, "requests": [], "parts": [],
        "profile": story_tts.DEFAULT_PROFILE, "source": "https://ai.google.dev/gemini-api/docs/speech-generation",
        "user_listening_pending": True,
    }
    limit = min(args.max_requests, report["authorized_max_requests"])
    original = story_tts.httpx.post

    def bounded(url, **kwargs):
        if len(report["requests"]) >= limit:
            raise ValueError("Лимит разрешённых реальных запросов исчерпан")
        entry = {"number": len(report["requests"])+1, "model": kwargs["json"]["model"], "status": "started"}
        report["requests"].append(entry)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        result = original(url, **kwargs)
        entry["status"] = result.status_code
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    story_tts.httpx.post = bounded
    for index, text in enumerate(TEXTS):
        if any(p["number"] == index+1 for p in report["parts"]):
            continue
        try:
            audio = story_tts.generate(text, report["profile"])
            report["parts"].append({"number": index+1, "text": text, "audio": str(audio)})
            report.pop("error", None)
        except ValueError as error:
            report["error"] = str(error)
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"requests": len(report["requests"]), "error": str(error)}, ensure_ascii=False))
            return 1
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"part": index+1, "requests": len(report["requests"]), "audio_ready": True}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

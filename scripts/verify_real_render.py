"""Read/copy one user-selected local source, render locally, verify audio envelope alignment."""

import array
import hashlib
import json
import math
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
original = Path(sys.argv[1]).resolve()
directory = ROOT / "data" / ("quality-real-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
os.environ["SURVIVALFLOW_DATA"] = str(directory)
from backend.config import binary  # noqa: E402
from backend.media import builtin_banner, command, probe, render  # noqa: E402
from backend.schemas import Edit  # noqa: E402

before = hashlib.sha256(original.read_bytes()).hexdigest()
source = directory / ("source" + original.suffix)
shutil.copyfile(original, source)
media = probe(source)
banner = directory / "banner.png"
builtin_banner("Проверка SurvivalFlow. Локальный тест баннера.", banner)
config = Edit(duration=4.5, y=22, opacity=95, brightness=0.5, saturation=102, warmth=0).effective(
    media["duration"]
)
output = directory / "SurvivalFlow-real-check.mp4"
last = -1


def progress(value):
    global last
    if int(value) // 20 != last:
        last = int(value) // 20
        print(f"Real source render {value:.0f}%", flush=True)


began = time.monotonic()
render(source, banner, {"image": True}, output, config, media["duration"], progress, lambda: False)
elapsed = time.monotonic() - began
result = probe(output)
assert (result["width"], result["height"]) == (1080, 1920)
assert abs(result["duration"] - media["duration"]) < 0.1 and result["has_audio"]


def envelope(path):
    samples = array.array(
        "h",
        command(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "-",
            ]
        ),
    )
    return [
        math.sqrt(sum(v * v for v in samples[i : i + 320]) / 320) for i in range(0, len(samples) - 319, 320)
    ]


left, right = envelope(source), envelope(output)


def correlate(lag):
    a = left[max(0, lag) : min(len(left), len(right) + lag)]
    b = right[max(0, -lag) : min(len(right), len(left) - lag)]
    am, bm = sum(a) / len(a), sum(b) / len(b)
    numerator = sum((x - am) * (y - bm) for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum((x - am) ** 2 for x in a) * sum((y - bm) ** 2 for y in b))
    return numerator / den if den else 0


scores = [(correlate(lag), lag) for lag in range(-5, 6)]
correlation, lag = max(scores)
assert correlation > 0.98 and abs(lag) <= 1, f"Audio envelope mismatch: {correlation}, {lag * 0.02}s"
assert hashlib.sha256(original.read_bytes()).hexdigest() == before
poster = directory / "rendered-banner.png"
command(
    [
        binary("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-ss",
        str(config["start"] + 1),
        "-i",
        str(output),
        "-frames:v",
        "1",
        str(poster),
    ]
)
report = {
    "source": str(original),
    "source_sha256_unchanged": before,
    "duration": media["duration"],
    "elapsed_seconds": round(elapsed, 2),
    "output_size_mb": round(output.stat().st_size / 1024**2, 2),
    "audio_envelope_correlation": round(correlation, 6),
    "audio_envelope_lag_seconds": round(lag * 0.02, 3),
    "video": str(output),
    "poster": str(poster),
    "external_publications": False,
}
out = ROOT / "docs" / "validation" / "overnight"
out.mkdir(parents=True, exist_ok=True)
(out / "real-render.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {key: value for key, value in report.items() if key not in ("source", "source_sha256_unchanged")},
        ensure_ascii=False,
    ),
    flush=True,
)

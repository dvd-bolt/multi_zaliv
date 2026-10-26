"""30-second motion + timed audio pulse test; synthetic material only."""

import array
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
directory = ROOT / "data" / ("quality-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
os.environ["SURVIVALFLOW_DATA"] = str(directory)
from backend.config import binary  # noqa: E402
from backend.media import builtin_banner, command, probe, render  # noqa: E402
from backend.schemas import Edit  # noqa: E402

source = directory / "synthetic-motion-30s.mp4"
command(
    [
        binary("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=24:duration=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=16000:duration=30",
        "-af",
        "volume=0:enable='gte(mod(t,5),0.2)'",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(source),
    ]
)
banner = directory / "banner.png"
builtin_banner("Демонстрация SurvivalFlow. Баннер появляется на 15-й секунде.", banner)
config = Edit(
    format="720",
    timing="manual",
    start=15,
    duration=4.5,
    fit="crop",
    brightness=1.2,
    contrast=103,
    saturation=97,
    zoom=102,
    mirror=True,
    opacity=85,
).effective(probe(source)["duration"])
result = directory / "SurvivalFlow-30s.mp4"
last = -1


def progress(value):
    global last
    if int(value) // 20 != last:
        last = int(value) // 20
        print(f"Render {value:.0f}%", flush=True)


began = time.monotonic()
render(source, banner, {"image": True}, result, config, 30, progress, lambda: False)
elapsed = time.monotonic() - began
info = probe(result)
assert abs(info["duration"] - 30) < 0.1 and info["has_audio"]


def onsets(path):
    pcm = array.array(
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
    values = []
    for second in range(0, 30, 5):
        candidates = []
        for offset in range(0, 100, 10):
            start = int((second + offset / 1000) * 16000)
            samples = pcm[start : start + 160]
            if samples and sum(v * v for v in samples) / len(samples) > 200**2:
                candidates.append(second + offset / 1000)
        assert candidates, f"Missing audio pulse at {second}s"
        values.append(candidates[0])
    return values


original_audio, export_audio = onsets(source), onsets(result)
drift = max(abs(a - b) for a, b in zip(original_audio, export_audio, strict=True))
assert drift <= 0.04, f"Audio pulse drift {drift}s"
poster = directory / "rendered-banner.png"
command(
    [binary("ffmpeg"), "-v", "error", "-y", "-ss", "15.8", "-i", str(result), "-frames:v", "1", str(poster)]
)
report = {
    "duration": 30,
    "dimensions": [info["width"], info["height"]],
    "elapsed_seconds": round(elapsed, 2),
    "size_mb": round(result.stat().st_size / 1024**2, 2),
    "audio_pulse_drift_seconds": round(drift, 3),
    "source_onsets": original_audio,
    "output_onsets": export_audio,
    "video": str(result),
    "poster": str(poster),
    "synthetic": True,
    "external_publications": False,
}
out = ROOT / "docs" / "validation" / "overnight"
out.mkdir(parents=True, exist_ok=True)
(out / "long-render.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)

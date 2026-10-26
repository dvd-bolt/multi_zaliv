import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .buba import banner_size
from .config import DATA, binary

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def command(args, timeout=90):
    try:
        result = subprocess.run(
            args, capture_output=True, creationflags=NO_WINDOW, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Проверка файла заняла слишком много времени. Попробуйте другой файл.") from error
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace")[-1400:])
    return result.stdout


def probe(path: Path):
    data = json.loads(
        command([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    )
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not video:
        raise ValueError("В файле нет видеопотока")
    container = data.get("format", {})
    duration = float(video.get("duration") or 0)
    start_time = float(video.get("start_time") or 0)
    if duration <= 0:
        duration = float(container.get("duration") or 0)
        if "matroska" in container.get("format_name", "") or "webm" in container.get("format_name", ""):
            # Matroska duration includes a nonzero segment timestamp origin.
            duration = max(0, duration - start_time)
    return {
        "width": video["width"],
        "height": video["height"],
        "duration": duration,
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
        "start_time": start_time,
        "container_start": float(container.get("start_time") or 0),
        "codec": video.get("codec_name"),
        "format": data.get("format", {}).get("format_name"),
    }


def inspect(path, kind, asset_id):
    try:
        return _inspect(path, kind, asset_id)
    except (UnidentifiedImageError, Image.DecompressionBombError, EOFError, OSError) as error:
        raise ValueError(
            "Не удалось прочитать файл или создать миниатюру. Проверьте формат и свободное место."
        ) from error


def _inspect(path, kind, asset_id):
    if kind == "banner" and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        with Image.open(path) as image:
            duration = (
                sum(
                    image.seek(i) or image.info.get("duration", 100)
                    for i in range(getattr(image, "n_frames", 1))
                )
                / 1000
            )
            info = {
                "width": image.width,
                "height": image.height,
                "duration": duration,
                "has_audio": False,
                "image": True,
                "animated": getattr(image, "n_frames", 1) > 1,
            }
            image.seek(0)
            image.thumbnail((320, 320))
            image.convert("RGBA").save(DATA / "thumbnails" / f"{asset_id}.png")
            return info
    info = probe(path)
    if not 0 < info["duration"] < 12 * 3600:
        raise ValueError("Не удалось определить длительность видео")
    command(
        [
            binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=240:-2",
            str(DATA / "thumbnails" / f"{asset_id}.png"),
        ]
    )
    return info


def variation(config, account_id, slot=0, total=1):
    result = dict(config)
    if config.get("overrides", {}).get(account_id):
        result.update(config["overrides"][account_id])
    else:
        seed = hashlib.sha256(account_id.encode()).digest()
        # Space recipients apart: random sub-pixel offsets can collapse to identical encoded colors.
        spacing = min(1.25, 8 / max(1, total - 1))
        half_spread = (total - 1) * spacing / 2
        center = max(-15 + half_spread, min(15 - half_spread, result["brightness"]))
        result["brightness"] = center + slot * spacing - half_spread
        result["saturation"] = max(70, min(130, result["saturation"] + (seed[1] / 255 - 0.5) * 8))
        result["warmth"] = max(-15, min(15, result["warmth"] + (seed[2] / 255 - 0.5) * 6))
    result.pop("overrides", None)
    result.pop("targets", None)
    return result


def builtin_banner(text, destination):
    image = Image.new("RGBA", (960, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 960, 180), radius=20, fill="#F5A064")
    font_file = Path("C:/Windows/Fonts/seguisb.ttf")
    text = " ".join(text.split()) or "Продолжение в нашем канале"
    # Wrap even an unbroken 120-character word. Reducing to a minimum font alone clips text.
    for size in range(38, 11, -2):
        font = (
            ImageFont.truetype(str(font_file), size)
            if font_file.exists()
            else ImageFont.load_default(size=size)
        )
        lines, line = [], ""
        for word in text.split(" "):
            candidate = (line + " " + word).strip()
            if draw.textlength(candidate, font=font) <= 900:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for character in word:
                if draw.textlength(line + character, font=font) > 900:
                    lines.append(line)
                    line = ""
                line += character
        lines.append(line.rstrip())
        wrapped = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=6)
        if bounds[3] - bounds[1] <= 140:
            break
    draw.multiline_text((480, 90), wrapped, font=font, fill="#24180F", anchor="mm", align="center", spacing=6)
    image.save(destination, format="PNG")


def render(source, banner, banner_media, output, config, length, progress, cancelled, encoder="libx264"):
    w, h = (1080, 1920) if config["format"] == "1080" else (720, 1280)
    start, duration = float(config["start"]), float(config["duration"])
    freeze = config.get("freeze_video", False)
    if freeze:
        length += duration
    banner_width = max(2, int(w * config["width"] / 100) // 2 * 2)
    fit = config.get("fit", "pad")
    aspect = "increase" if fit == "crop" else "decrease"
    framing = f"crop={w}:{h}" if fit == "crop" else f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    zoom = config["zoom"] / 100
    filters = (
        f"scale={w}:{h}:force_original_aspect_ratio={aspect}:force_divisible_by=2,"
        + framing
        + ",setsar=1,"
        + f"scale=trunc(iw*{zoom}/2)*2:trunc(ih*{zoom}/2)*2,crop={w}:{h},"
        + ("hflip," if config["mirror"] else "")
        + f"eq=brightness={config['brightness'] / 100}:contrast={config['contrast'] / 100}:"
        f"saturation={config['saturation'] / 100},"
        + f"colorbalance=rs={config['warmth'] / 300}:bs={-config['warmth'] / 300},setpts=PTS-STARTPTS"
    )
    source_info = probe(source)
    base = f"[0:v]{filters}[base];"
    audio_map = "0:a?"
    if freeze:
        if start == 0:
            base = f"[0:v]{filters},tpad=start_mode=clone:start_duration={duration}[base];"
        else:
            base = (
                f"[0:v]{filters},split[pre][post];"
                f"[pre]trim=end={start},setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={duration}[held];"
                f"[post]trim=start={start},setpts=PTS-STARTPTS[tail];"
                "[held][tail]concat=n=2:v=1:a=0[base];"
            )
        if source_info["has_audio"]:
            audio_map = "[a]"
            if start == 0:
                base += f"[0:a:0]asetpts=PTS-STARTPTS,adelay={duration * 1000}:all=1[a];"
            else:
                base += (
                    "[0:a:0]asetpts=PTS-STARTPTS,asplit[apre][apost];"
                    f"[apre]atrim=end={start},apad=whole_dur={start + duration}[asilent];"
                    f"[apost]atrim=start={start},asetpts=PTS-STARTPTS[atail];"
                    "[asilent][atail]concat=n=2:v=0:a=1[a];"
                )
    if config.get("banner_audio", False) and banner_media.get("has_audio", False):
        base += (
            f"[1:a:0]asetpts=PTS-STARTPTS,atrim=duration={duration},"
            f"apad=whole_dur={duration},atrim=duration={duration},"
            f"adelay={start * 1000}:all=1,apad=whole_dur={length},atrim=duration={length}[ba];"
        )
        if source_info["has_audio"]:
            if not freeze:
                base += (
                    "[0:a:0]asetpts=PTS-STARTPTS,"
                    f"volume=0:enable='gte(t,{start})*lt(t,{start + duration})'[a];"
                )
            base += "[a][ba]amix=inputs=2:duration=longest:normalize=0[mixed];"
            audio_map = "[mixed]"
        else:
            audio_map = "[ba]"
    bw, bh, _, _ = banner_size(config, banner_media)
    banner_scale = (
        f"scale={bw}:{bh}"
        if config.get("banner_height") is not None
        else f"scale={banner_width}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    graph = base + (
        f"[1:v]{banner_scale},format=rgba,"
        f"colorchannelmixer=aa={config['opacity'] / 100},trim=duration={duration},"
        f"setpts=PTS-STARTPTS+{start}/TB[bn];[base][bn]overlay="
        f"x='max(0,min(W-w,W*{config['x'] / 100}-w/2))':"
        f"y='max(0,min(H-h,H*{config['y'] / 100}-h/2))':"
        f"enable='gte(t,{start})*lt(t,{start + duration})':eof_action=repeat:repeatlast=1,"
        "format=yuv420p[v]"
    )
    input_delay = source_info["start_time"] - source_info["container_start"]
    args = [binary("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-y"]
    if input_delay > 0.05:
        args += ["-ss", str(input_delay)]
    args += ["-i", str(source)]
    if banner_media.get("image") and not banner_media.get("animated"):
        args += ["-loop", "1"]
    elif config.get("banner_end") == "loop":
        args += ["-stream_loop", "-1"]
    args += [
        "-i",
        str(banner),
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        audio_map,
        "-t",
        str(length),
        "-c:v",
        encoder,
    ]
    if encoder == "libx264":
        args += ["-preset", "fast", "-crf", "20" if config.get("quality") == "high" else "23"]
    else:
        args += ["-b:v", "6M"]
    temporary = output.with_suffix(".part.mp4")
    args += [
        "-threads",
        "4",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-map_metadata",
        "-1",
        "-metadata",
        f"comment=SurvivalFlow {output.stem}",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(temporary),
    ]
    lines = queue.Queue()
    log = DATA / "logs" / f"render-{output.stem}.log"
    with log.open("wb") as stderr:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr, creationflags=NO_WINDOW)

        def read_progress():
            for line in iter(process.stdout.readline, b""):
                lines.put(line)

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        began = time.monotonic()
        try:
            while process.poll() is None or not lines.empty():
                if cancelled():
                    raise InterruptedError("Обработка отменена")
                if time.monotonic() - began > max(300, length * 30):
                    raise TimeoutError("Превышено время обработки")
                try:
                    line = lines.get(timeout=0.25).decode(errors="replace").strip()
                    if line.startswith("out_time_us="):
                        value = line.split("=", 1)[1]
                        if value.lstrip("-").isdigit():
                            progress(max(0, min(99, int(value) / 1000000 / length * 100)))
                except queue.Empty:
                    continue
            if process.wait() != 0:
                raise ValueError("FFmpeg: " + log.read_text(errors="replace")[-1000:])
            info = probe(temporary)
            if info["width"] != w or info["height"] != h or abs(info["duration"] - length) > 0.25:
                raise ValueError("Экспорт не прошёл проверку размера/длительности")
            temporary.replace(output)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)
            process.stdout.close()
            temporary.unlink(missing_ok=True)

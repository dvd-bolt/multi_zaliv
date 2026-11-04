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
        "has_alpha": str(video.get("tags", {}).get("alpha_mode", "")) == "1"
        or video.get("pix_fmt", "").startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap")),
        "format": data.get("format", {}).get("format_name"),
        "fps": frame_rate(video),
    }


def decoder_options(media):
    # FFmpeg's native VP8/VP9 decoders discard WebM's separate alpha plane.
    # Choose libvpx even for older saved metadata without a has_alpha field.
    decoder = {"vp8": "libvpx", "vp9": "libvpx-vp9"}.get(media.get("codec"))
    return ["-c:v", decoder] if decoder else []


def visible_box(path: Path, info, threshold=16, frames=120):
    """Bounding box of the banner pixels a viewer can see: [x, y, w, h] in source pixels.

    Banners often ship with transparent margins or glow padding. Their frame then
    covers 25% of the video while the visible advert covers noticeably less. The box
    is the union over sampled frames, so an animation that grows is not cut off.
    None means the whole frame is visible (no alpha) or nothing could be measured.
    """
    width, height = info["width"], info["height"]
    box = None

    def merge(found):
        nonlocal box
        if found:
            box = (
                found
                if box is None
                else (
                    min(box[0], found[0]),
                    min(box[1], found[1]),
                    max(box[2], found[2]),
                    max(box[3], found[3]),
                )
            )

    def opaque(alpha):
        return alpha.point(lambda v: 255 if v > threshold else 0).getbbox()

    if info.get("image"):
        with Image.open(path) as image:
            if "A" not in image.getbands() and "transparency" not in image.info:
                return None
            for index in range(min(getattr(image, "n_frames", 1), frames)):
                image.seek(index)
                merge(opaque(image.convert("RGBA").getchannel("A")))
    else:
        if not info.get("has_alpha"):
            return None
        rate = max(0.5, min(10.0, frames / max(info.get("duration") or 1, 0.1)))
        raw = command(
            [
                binary("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                *decoder_options(info),
                "-i",
                str(path),
                "-vf",
                f"fps={rate:.3f},format=rgba,alphaextract",
                "-frames:v",
                str(frames),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            timeout=180,
        )
        size = width * height
        for offset in range(0, len(raw) - size + 1, size):
            merge(opaque(Image.frombytes("L", (width, height), raw[offset : offset + size])))
    if box is None:
        return None
    # Even offsets and sizes keep the crop exact for 4:2:0 chroma.
    left, top = box[0] // 2 * 2, box[1] // 2 * 2
    right, bottom = min(width, (box[2] + 1) // 2 * 2), min(height, (box[3] + 1) // 2 * 2)
    if right - left < 2 or bottom - top < 2 or (left, top, right, bottom) == (0, 0, width, height):
        return None
    return [left, top, right - left, bottom - top]


def asset_visible(asset, save=True):
    """Visible banner box; measured once for banners imported before this feature."""
    media = asset.media or {}
    if asset.kind != "banner" or "visible" in media:
        return media
    try:
        visible = visible_box(Path(asset.path), media)
    except (ValueError, OSError, UnidentifiedImageError):
        return media
    media = {**media, "visible": visible}
    if save:
        asset.media = media
    return media


def asset_pauses(asset, save=True):
    """Speech pauses of a source video; computed once for videos imported before this feature."""
    media = asset.media or {}
    if "pauses" in media or not media.get("has_audio"):
        return media.get("pauses") or []
    try:
        pauses = speech_pauses(Path(asset.path))
    except (ValueError, OSError):
        return []
    if save:
        asset.media = {**media, "pauses": pauses}
    return pauses


def frame_rate(video):
    for key in ("avg_frame_rate", "r_frame_rate"):
        top, _, bottom = str(video.get(key) or "").partition("/")
        try:
            rate = float(top) / float(bottom or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 120:
            return round(rate, 3)
    return 30.0


def speech_pauses(path, step=0.05, drop=12, shortest=0.25):
    """Quiet stretches between phrases: [start, end] in seconds.

    Loudness is measured every 50 ms. A pause is a run quieter than the loud parts of the
    track (80th percentile) by `drop` dB, so constant music or ambience under the voice
    does not hide pauses.
    """
    samples = int(8000 * step)
    output = command(
        [
            binary("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"aresample=8000,asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    ).decode(errors="replace")
    levels = []
    for line in output.splitlines():
        if "RMS_level=" in line:
            value = line.rsplit("=", 1)[1]
            levels.append(-120.0 if value in ("-inf", "inf", "nan") else max(-120.0, float(value)))
    if len(levels) < 10:
        return []
    loud = sorted(levels)[int(len(levels) * 0.8)]
    if loud <= -60:
        return []
    quiet = loud - drop
    pauses, begin = [], None
    for index, level in enumerate(levels + [0.0]):
        if level < quiet and index < len(levels):
            begin = index if begin is None else begin
        elif begin is not None:
            if (index - begin) * step >= shortest:
                pauses.append([round(begin * step, 2), round(index * step, 2)])
            begin = None
    return pauses


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
            info["visible"] = visible_box(path, info)
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
            *decoder_options(info),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=240:-2",
            str(DATA / "thumbnails" / f"{asset_id}.png"),
        ]
    )
    if kind == "banner":
        info["visible"] = visible_box(path, info)
    if info["has_audio"]:
        try:
            info["pauses"] = speech_pauses(path)
        except ValueError:
            info["pauses"] = []
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
    if freeze and config.get("freeze_motion"):
        # A held frame with a slow push-in: the picture keeps moving while the ad plays.
        fps = source_info.get("fps") or 30.0
        frames = max(1, round(duration * fps))
        push = (
            f"[still]trim=start={start},setpts=PTS-STARTPTS,trim=end_frame=1,"
            f"scale={w * 2}:{h * 2},"
            f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d={frames}:s={w}x{h}:fps={fps},setsar=1,"
            f"trim=duration={duration},setpts=PTS-STARTPTS[held];"
        )
        if start == 0:
            base = (
                f"[0:v]{filters},split[still][post];"
                + push
                + "[post]setpts=PTS-STARTPTS[tail];[held][tail]concat=n=2:v=1:a=0[base];"
            )
        else:
            base = (
                f"[0:v]{filters},split=3[pre][still][post];"
                f"[pre]trim=end={start},setpts=PTS-STARTPTS[head];"
                + push
                + f"[post]trim=start={start},setpts=PTS-STARTPTS[tail];"
                "[head][held][tail]concat=n=3:v=1:a=0[base];"
            )
    elif freeze and start == 0:
        base = f"[0:v]{filters},tpad=start_mode=clone:start_duration={duration}[base];"
    elif freeze:
        base = (
            f"[0:v]{filters},split[pre][post];"
            f"[pre]trim=end={start},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={duration}[held];"
            f"[post]trim=start={start},setpts=PTS-STARTPTS[tail];"
            "[held][tail]concat=n=2:v=1:a=0[base];"
        )
    if freeze:
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
    visible = banner_media.get("visible")
    banner_crop = f"crop={visible[2]}:{visible[3]}:{visible[0]}:{visible[1]}," if visible else ""
    graph = base + (
        f"[1:v]format=rgba,{banner_crop}{banner_scale},format=rgba,"
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
    args += decoder_options(banner_media)
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
        f"comment=FrameFlow {output.stem}",
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

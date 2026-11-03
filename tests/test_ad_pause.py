from array import array

import pytest
from PIL import Image

from backend.buba import report
from backend.config import DATA, binary
from backend.media import command, probe, render, speech_pauses
from backend.schemas import Edit, ad_start


def test_ad_start_prefers_the_pause_closest_to_the_middle():
    # 60 s: the middle (30) falls inside speech; pauses at 28.3–28.9 and 31.5–32.0.
    start, found = ad_start(60, [[10, 11], [28.3, 28.9], [31.5, 32.0]])
    assert found and start == pytest.approx(28.82)
    # A pause that covers the middle keeps the exact middle.
    assert ad_start(60, [[29.5, 30.5]]) == (30, True)


def test_ad_start_keeps_the_middle_without_a_pause_nearby():
    assert ad_start(60, [[10, 11], [40, 41]]) == (30, False)
    assert ad_start(60, [[29.9, 30.0]]) == (30, False)  # too short to cut inside
    assert ad_start(60, []) == (30, False)


def test_effective_uses_pauses_only_when_enabled():
    pauses = [[31.2, 31.9]]
    assert Edit(duration=5).effective(60, pauses)["start"] == 30
    config = Edit(duration=5, ad_pause=True).effective(60, pauses)
    assert config["start"] == pytest.approx(31.28) and config["pause_found"] is True
    assert Edit(duration=5, ad_pause=True, timing="manual", start=7).effective(60, pauses)["start"] == 7


def test_speech_pauses_found_under_background_noise():
    source = DATA / "pauses.wav"
    # Loud tone with two 0.5 s gaps at 2.0 and 4.5 s, over quiet noise (ambience under a voice).
    tone = "if(between(t,2,2.5)+between(t,4.5,5),0,0.5*sin(2*PI*440*t))"
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{tone}':s=44100:d=7",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=a=0.01:d=7",
            "-filter_complex",
            "amix=inputs=2:normalize=0",
            str(source),
        ]
    )
    pauses = speech_pauses(source)
    assert len(pauses) == 2
    for (begin, end), expected in zip(pauses, (2, 4.5), strict=True):
        assert abs(begin - expected) < 0.1 and abs(end - expected - 0.5) < 0.1


def frame(path, second):
    return command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=36:64",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )


def difference(a, b):
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def loudness(path, begin, end):
    raw = command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-ss",
            str(begin),
            "-t",
            str(end - begin),
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "pipe:1",
        ]
    )
    samples = array("h", raw)
    return max(abs(s) for s in samples) if samples else 0


def test_freeze_motion_moves_the_held_frame_and_keeps_the_speech():
    source = DATA / "motion-source.mp4"
    output = DATA / "motion-result.mp4"
    banner = DATA / "motion-banner.png"
    Image.new("RGB", (100, 40), "red").save(banner)
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=600:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    config = Edit(
        format="720",
        timing="manual",
        start=2,
        duration=2,
        freeze_video=True,
        freeze_motion=True,
        width=20,
        y=90,
    ).effective(4)
    render(source, banner, {"image": True}, output, config, 4, lambda _: None, lambda: False)
    info = probe(output)
    assert abs(info["duration"] - 6) < 0.25 and info["has_audio"]
    # The held picture changes (push-in) but stays the same scene: far less than live testsrc2.
    held = difference(frame(output, 2.2), frame(output, 3.8))
    assert 0.5 < held
    # Speech is paused, not lost: silence during the hold, the tone returns after it.
    assert loudness(output, 2.2, 3.8) < 300
    assert loudness(output, 4.3, 5.6) > 3000


def base_config(**extra):
    return {
        "buba_mode": True,
        "format": "1080",
        "width": 100,
        "banner_height": 26,
        "x": 50,
        "y": 50,
        "timing": "middle",
        "duration": 5,
        "banner_audio": True,
        "opacity": 100,
        "visibility": "public",
        "caption": "#бубавпн",
        "buba_review": {},
        **extra,
    }


BANNER = {"width": 960, "height": 250, "duration": 5, "has_audio": True, "animated": True}
MEDIA = {"duration": 60, "has_audio": True}


def codes(result):
    return {i["code"]: i["level"] for i in result["issues"]}


def test_buba_shift_to_pause_is_a_warning_not_an_error():
    shifted = codes(report(MEDIA, BANNER, base_config(ad_pause=True, start=31.3, freeze_video=True)))
    assert shifted.get("middle_shift") == "warning" and "middle" not in shifted
    # Beyond the window the middle rule is still an error.
    far = codes(report(MEDIA, BANNER, base_config(ad_pause=True, start=35, freeze_video=True)))
    assert far.get("middle") == "error"
    # Without the option nothing changes.
    plain = codes(report(MEDIA, BANNER, base_config(start=31.3, freeze_video=True)))
    assert "middle" not in plain and "middle_shift" not in plain


def test_buba_warns_when_the_ad_sound_mutes_speech():
    assert codes(report(MEDIA, BANNER, base_config(freeze_video=False))).get("speech_muted") == "warning"
    assert "speech_muted" not in codes(report(MEDIA, BANNER, base_config(freeze_video=True)))

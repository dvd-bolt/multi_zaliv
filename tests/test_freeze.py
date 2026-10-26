from array import array

import pytest
from PIL import Image

from backend.config import DATA, binary
from backend.media import command, probe, render
from backend.schemas import Edit


@pytest.mark.parametrize("start,audio", [(0, True), (1, True), (2.8, True), (1, False)])
def test_freeze_inserts_time_and_silence(start, audio):
    source = DATA / f"freeze-source-{start}-{audio}.mp4"
    output = DATA / f"freeze-result-{start}-{audio}.mp4"
    banner = DATA / "freeze-banner.png"
    Image.new("RGB", (100, 40), "red").save(banner)
    args = [
        binary("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=90x160:rate=10:duration=3",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=600:duration=3", "-c:a", "aac"]
    command(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)])
    config = Edit(
        format="720", timing="manual", start=start, duration=1, freeze_video=True, width=20, y=90
    ).effective(3)
    render(source, banner, {"image": True}, output, config, 3, lambda _: None, lambda: False)
    info = probe(output)
    assert abs(info["duration"] - 4) < 0.15
    assert info["has_audio"] == audio

    def frame(second):
        return command(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-ss",
                str(second),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "crop=720:600:0:100,scale=36:30",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )

    def difference(a, b):
        assert len(a) == len(b) and a
        return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)

    assert difference(frame(start + 0.2), frame(start + 0.7)) < 2
    if start < 2:
        assert difference(frame(start + 0.7), frame(start + 1.5)) > 3
    if audio:

        def rms(second):
            raw = command(
                [
                    binary("ffmpeg"),
                    "-v",
                    "error",
                    "-ss",
                    str(second),
                    "-i",
                    str(output),
                    "-t",
                    "0.15",
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
            assert samples
            return (sum(v * v for v in samples) / len(samples)) ** 0.5

        assert rms(start + 0.3) < 10
        if start < 2:
            assert rms(start + 1.3) > 100


def test_freeze_validation_and_legacy():
    assert Edit(timing="manual", start=2.9, duration=2, freeze_video=True).effective(3)
    with pytest.raises(ValueError):
        Edit(timing="manual", start=3, freeze_video=True).effective(3)
    with pytest.raises(ValueError):
        Edit(timing="manual", start=2.9, duration=2).effective(3)
    assert not Edit().freeze_video


@pytest.mark.parametrize(
    "freeze,source_audio,loop",
    [(True, True, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_banner_audio_replaces_source_and_resumes(freeze, source_audio, loop):
    import math

    source = DATA / f"audio-source-{freeze}-{source_audio}-{loop}.mp4"
    banner = DATA / "audio-banner.mp4"
    output = DATA / f"audio-result-{freeze}-{source_audio}-{loop}.mp4"
    args = [
        binary("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=90x160:rate=10:duration=3",
    ]
    if source_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=600:duration=3", "-c:a", "aac"]
    command(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)])
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=100x40:rate=10:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1200:duration=0.5",
            "-c:a",
            "aac",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(banner),
        ]
    )
    config = Edit(
        format="720",
        timing="manual",
        start=1,
        duration=1,
        freeze_video=freeze,
        banner_audio=True,
        banner_end="loop" if loop else "hold",
    ).effective(3)
    render(source, banner, probe(banner), output, config, 3, lambda _: None, lambda: False)
    info = probe(output)
    assert abs(info["duration"] - (4 if freeze else 3)) < 0.15
    assert info["has_audio"]

    def energies(second):
        samples = array(
            "h",
            command(
                [
                    binary("ffmpeg"),
                    "-v",
                    "error",
                    "-ss",
                    str(second),
                    "-i",
                    str(output),
                    "-t",
                    "0.1",
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-f",
                    "s16le",
                    "pipe:1",
                ]
            ),
        )
        assert samples

        def power(hz):
            return abs(
                sum(
                    v * complex(math.cos(2 * math.pi * hz * i / 8000), math.sin(2 * math.pi * hz * i / 8000))
                    for i, v in enumerate(samples)
                )
            ) / len(samples)

        return power(600), power(1200)

    original, advert = energies(1.2)
    assert advert > 500 and advert > original * 8
    if loop:
        assert energies(1.8)[1] > 500
    else:
        assert max(energies(1.8)) < 10
    original, advert = energies(2.3)
    assert original > 500 if source_audio else original < 10
    assert advert < 10

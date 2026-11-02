import pytest
from PIL import Image, ImageDraw

from backend.config import binary
from backend.media import command, inspect, probe, render
from backend.schemas import Edit


@pytest.mark.parametrize("encoder", ["libvpx", "libvpx-vp9"])
def test_webm_alpha_survives_thumbnail_and_export_with_legacy_metadata(tmp_path, encoder):
    source, png, banner, output = [
        tmp_path / name for name in ("source.mp4", "banner.png", "banner.webm", "output.mp4")
    ]
    image = Image.new("RGBA", (160, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((60, 20, 100, 60), fill=(255, 0, 0, 255))
    image.save(png)
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x280:r=10:d=1.2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    command(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(png),
            "-t",
            "0.6",
            "-r",
            "10",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuva420p",
            "-auto-alt-ref",
            "0",
            str(banner),
        ]
    )
    info = probe(banner)
    assert info["has_alpha"]
    # The UI thumbnail should preserve transparency too.
    from backend.config import DATA
    from backend.db import uid

    asset_id = uid()
    thumbnail = DATA / "thumbnails" / f"{asset_id}.png"
    try:
        inspect(banner, "banner", asset_id)
        with Image.open(thumbnail) as preview:
            assert preview.convert("RGBA").getpixel((5, 5))[3] == 0
    finally:
        thumbnail.unlink(missing_ok=True)
    info.pop("has_alpha")  # Existing imports only saved the codec, not the alpha flag.
    edit = Edit(
        format="720",
        fit="crop",
        warmth=0,
        timing="manual",
        start=0.2,
        duration=0.6,
        width=50,
        banner_height=20,
        x=50,
        y=50,
    )
    render(source, banner, info, output, edit.effective(1.2), 1.2, lambda _: None, lambda: False)

    def pixel(x, y, second=0.4):
        data = command(
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
                f"crop=2:2:{x}:{y}",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        return tuple(data[:3])

    # Transparent corner remains the blue source; opaque center is the red banner.
    r, g, b = pixel(200, 530)
    assert b > 180 and r < 40 and g < 40
    r, g, b = pixel(360, 640)
    assert r > 180 and g < 40 and b < 40
    r, g, b = pixel(360, 640, 1.0)
    assert b > 180 and r < 40 and g < 40

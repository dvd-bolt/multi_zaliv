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


def _red_share(path, second, width=720, height=1280):
    raw = command(
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
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )
    red = sum(raw[i] > 180 and raw[i + 1] < 70 and raw[i + 2] < 70 for i in range(0, len(raw), 3))
    return 100 * red / (width * height)


@pytest.mark.parametrize("kind", ["png", "webm"])
def test_transparent_margins_are_cropped_so_visible_area_matches_the_check(tmp_path, kind):
    """Real banners ship with transparent padding: 26% of frame used to be ~21% visible."""
    from backend.buba import REVIEW, report

    source, png, webm, output = [
        tmp_path / name for name in ("source.mp4", "banner.png", "banner.webm", "output.mp4")
    ]
    # 200×80 frame, opaque red only in the middle 160×64 → 80% × 80% visible.
    image = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 8, 179, 71), fill=(255, 0, 0, 255))
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
            "color=c=blue:s=90x160:r=10:d=2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    banner = png
    if kind == "webm":
        banner = webm
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
                "1",
                "-r",
                "10",
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                str(webm),
            ]
        )
    from backend.config import DATA
    from backend.db import uid

    asset_id = uid()
    try:
        info = inspect(banner, "banner", asset_id)
    finally:
        (DATA / "thumbnails" / f"{asset_id}.png").unlink(missing_ok=True)
    left, top, width, height = info["visible"]
    assert abs(left - 20) <= 2 and abs(top - 8) <= 2
    assert abs(width - 160) <= 4 and abs(height - 64) <= 4

    edit = Edit(
        buba_mode=True,
        format="720",
        timing="manual",
        start=0.5,
        duration=1,
        width=100,
        banner_height=28,
        x=50,
        y=50,
        visibility="public",
        buba_review={k: True for k in REVIEW},
    )
    config = edit.effective(2)
    checked = report({"duration": 2, "has_audio": True}, info, config)
    assert checked["area_percent"] == pytest.approx(28, abs=0.2)
    assert not any(i["code"] in ("area", "area_margin", "visible_unknown") for i in checked["issues"])
    render(source, banner, info, output, config, 2, lambda _: None, lambda: False)
    # Before the fix only ~80% × 80% of the 28% box was red (≈18%).
    assert 27 < _red_share(output, 1.0) < 29
    assert _red_share(output, 1.8) < 0.5


def test_opaque_banner_has_no_visible_box_and_old_imports_get_a_warning(tmp_path):
    from backend.buba import REVIEW, report
    from backend.media import visible_box

    png = tmp_path / "solid.png"
    Image.new("RGB", (120, 40), "red").save(png)
    assert visible_box(png, {"width": 120, "height": 40, "image": True}) is None
    config = Edit(
        buba_mode=True,
        banner_height=26,
        width=100,
        x=50,
        y=50,
        visibility="public",
        buba_review={k: True for k in REVIEW},
    ).effective(20)
    legacy = {"width": 1374, "height": 552, "duration": 5, "image": False, "has_audio": False}
    codes = {
        i["code"]: i["level"] for i in report({"duration": 20, "has_audio": True}, legacy, config)["issues"]
    }
    assert codes["visible_unknown"] == "warning"
    assert codes["area_margin"] == "warning"
    assert "area" not in codes

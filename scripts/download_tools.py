"""Install a checksum-verified FFmpeg build inside the project's ignored data folder."""

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "data" / "tools"
root.mkdir(parents=True, exist_ok=True)
if (root / "ffmpeg.exe").exists() and (root / "ffprobe.exe").exists():
    print("FFmpeg/ffprobe already installed")
    raise SystemExit(0)
url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
archive = root / "ffmpeg.zip"
with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
    shutil.copyfileobj(response, output)
expected = urllib.request.urlopen(url + ".sha256", timeout=30).read().decode().strip().split()[0]
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if expected.lower() != actual:
    archive.unlink()
    raise RuntimeError("FFmpeg checksum mismatch")
with zipfile.ZipFile(archive) as package:
    for name in package.namelist():
        if name.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe", "/LICENSE")):
            with package.open(name) as source, (root / Path(name).name).open("wb") as target:
                shutil.copyfileobj(source, target)
archive.unlink()
(root / "SOURCE.txt").write_text(f"{url}\nSHA256 {actual}\n", encoding="utf-8")
print("Verified FFmpeg and ffprobe installed")

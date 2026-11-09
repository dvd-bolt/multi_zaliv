import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = (ROOT / "frameflow-build.json").is_file()
DEFAULT_DATA = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
                / "FrameFlow" / "data") if PACKAGED else ROOT / "data"
DATA = Path(os.environ.get("SURVIVALFLOW_DATA", str(DEFAULT_DATA))).resolve()
PORT = int(os.environ.get("SURVIVALFLOW_PORT", "8765"))


def initialize_dirs():
    for name in ("uploads", "thumbnails", "exports", "temporary", "secrets", "logs", "backups", "tools"):
        (DATA / name).mkdir(parents=True, exist_ok=True)


def binary(name: str) -> str:
    explicit = os.environ.get(f"SURVIVALFLOW_{name.upper()}")
    local = next((p for p in (DATA / "tools" / f"{name}.exe",
                             ROOT / "resources" / "tools" / f"{name}.exe",
                             ROOT / "data" / "tools" / f"{name}.exe") if p.is_file()), None)
    result = explicit or (str(local) if local else shutil.which(name))
    if not result or not Path(result).is_file():
        raise RuntimeError(f"Не найден {name}. Запустите scripts/setup.ps1.")
    return result


def python_binary(windowed=False):
    name = "pythonw.exe" if windowed else "python.exe"
    return ROOT / "runtime" / name if PACKAGED else ROOT / ".venv" / "Scripts" / name


initialize_dirs()

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("SURVIVALFLOW_DATA", str(ROOT / "data"))).resolve()
PORT = int(os.environ.get("SURVIVALFLOW_PORT", "8765"))


def initialize_dirs():
    for name in ("uploads", "thumbnails", "exports", "temporary", "secrets", "logs", "backups", "tools"):
        (DATA / name).mkdir(parents=True, exist_ok=True)


def binary(name: str) -> str:
    explicit = os.environ.get(f"SURVIVALFLOW_{name.upper()}")
    local = ROOT / "data" / "tools" / f"{name}.exe"
    result = explicit or (str(local) if local.exists() else shutil.which(name))
    if not result or not Path(result).is_file():
        raise RuntimeError(f"Не найден {name}. Запустите scripts/setup.ps1.")
    return result


initialize_dirs()

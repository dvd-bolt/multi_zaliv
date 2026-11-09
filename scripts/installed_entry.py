"""Entry point of the relocatable Windows distribution; no dependency on a global Python."""

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--services", action="store_true")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        import clr  # noqa: F401
        import ctranslate2
        import numpy
        import onnxruntime
        import playwright.sync_api  # noqa: F401
        import webview  # noqa: F401
        from sqlalchemy import text

        from backend.config import DATA, PACKAGED, ROOT, binary, python_binary
        from backend.db import engine
        from backend.story_captions import model

        # Loading the bundled local model proves its native DLLs and files relocate.
        model()
        with engine.connect() as connection:
            schema = connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar()
        print(json.dumps({"packaged": PACKAGED, "root": str(ROOT), "data": str(DATA),
                          "python": str(python_binary()), "ffmpeg": binary("ffmpeg"), "schema": schema,
                          "python_prefix": sys.prefix, "sys_path": sys.path, "isolated": sys.flags.isolated,
                          "numpy": numpy.__version__, "ctranslate2": ctranslate2.__version__,
                          "onnxruntime": onnxruntime.__version__, "local_model": True}))
    elif args.stop:
        from scripts.stop import main as stop

        stop()
    elif args.services:
        from scripts.launch import ensure_services

        print(ensure_services())
    else:
        from scripts.launch import main as launch

        launch()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if sys.stderr:
            import traceback

            traceback.print_exc()
        else:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, str(error), "FrameFlow — запуск", 16)
        raise SystemExit(1) from None

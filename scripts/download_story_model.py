"""Install pinned public speech weights using bounded HTTP ranges on slow connections."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/"
SIZE = 145217532


def main():
    target = ROOT / "data/tools/whisper-base"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "tokenizer.json", "vocabulary.txt"):
        if not (target / name).exists():
            response = requests.get(URL + name, timeout=(15, 90))
            response.raise_for_status()
            (target / name).write_bytes(response.content)
    if (target / "model.bin").is_file() and (target / "model.bin").stat().st_size == SIZE:
        return
    folder = target / "chunks"
    folder.mkdir(exist_ok=True)
    chunk_size = 8 * 1024**2

    def download(start):
        end = min(SIZE, start + chunk_size) - 1
        path = folder / str(start)
        if path.exists() and path.stat().st_size == end-start+1:
            return path
        for _ in range(2):
            try:
                response = requests.get(URL + f"model.bin?download=true&chunk={start}",
                                        headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 90))
                response.raise_for_status()
                if (response.status_code != 206 or len(response.content) != end-start+1
                        or response.headers.get("Content-Range") != f"bytes {start}-{end}/{SIZE}"):
                    raise ValueError("Server did not return the requested model range")
                path.write_bytes(response.content)
                print(f"Model range ready: {start}-{end}", flush=True)
                return path
            except (requests.RequestException, ValueError):
                if _:
                    raise
        raise RuntimeError("Unreachable")

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(download, range(0, SIZE, chunk_size)))
    temporary = target / "model.download.bin"
    with temporary.open("wb") as out:
        for path in paths:
            out.write(path.read_bytes())
    assert temporary.stat().st_size == SIZE
    temporary.replace(target / "model.bin")
    # Keep the range files for resumability until installation has succeeded.
    for path in paths:
        path.unlink()
    folder.rmdir()
    print("Pinned local speech model installed", flush=True)


if __name__ == "__main__":
    main()

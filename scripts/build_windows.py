"""Build a relocatable Python distribution, native launcher and per-user Windows installer."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "windows"
STAGE = BUILD / "FrameFlow"
RELEASES = ROOT / "releases"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024**2):
            h.update(block)
    return h.hexdigest()


def ignore(directory, names):
    return [n for n in names if n in {"__pycache__", ".git", ".pytest_cache"}
            or n.endswith((".pyc", ".pyo", ".pth", ".egg-link"))
            or n in {"direct_url.json", "sitecustomize.py", "usercustomize.py"}]


def copy_tree(source, target, extra_ignore=()):
    def selected(directory, names):
        return ignore(directory, names) + [n for n in names if n in extra_ignore]

    shutil.copytree(source, target, ignore=selected, dirs_exist_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", required=True, type=Path)
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not args.skip_frontend:
        subprocess.run(["npm.cmd", "run", "build"], cwd=ROOT / "frontend", check=True)
    # Only our fixed build staging directory can ever be cleared.
    resolved = STAGE.resolve()
    assert resolved.is_relative_to((ROOT / "build").resolve()) and resolved.name == "FrameFlow"
    if resolved.exists():
        shutil.rmtree(resolved)
    STAGE.mkdir(parents=True)
    RELEASES.mkdir(exist_ok=True)
    runtime = STAGE / "runtime"
    runtime.mkdir()
    base = Path(sys.base_prefix)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python313.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll", "LICENSE.txt"):
        shutil.copy2(base / name, runtime / name)
    copy_tree(base / "DLLs", runtime / "DLLs")
    copy_tree(base / "Lib", runtime / "Lib", {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip"})
    copy_tree(ROOT / ".venv/Lib/site-packages", runtime / "Lib/site-packages")
    # The ._pth file isolates Python from registry, global modules and PYTHONPATH.
    (runtime / "python313._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n..\nimport site\n", encoding="utf-8")
    copy_tree(ROOT / "backend", STAGE / "backend")
    (STAGE / "scripts").mkdir()
    for name in ("__init__.py", "launch.py", "desktop.py", "stop.py", "installed_entry.py"):
        source = ROOT / "scripts" / name
        if source.exists():
            shutil.copy2(source, STAGE / "scripts" / name)
    copy_tree(ROOT / "frontend/dist", STAGE / "frontend/dist")
    copy_tree(ROOT / "assets/icons", STAGE / "assets/icons")
    copy_tree(ROOT / "browser-extension", STAGE / "browser-extension")
    for name in ("pyproject.toml", "requirements.lock.txt"):
        shutil.copy2(ROOT / name, STAGE / name)
    tools = STAGE / "resources/tools"
    tools.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE", "SOURCE.txt"):
        shutil.copy2(ROOT / "data/tools" / name, tools / name)
    model = STAGE / "resources/models/whisper-base"
    model.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        shutil.copy2(ROOT / "data/tools/whisper-base" / name, model / name)
    notices = STAGE / "ThirdPartyNotices"
    notices.mkdir()
    for package in ("react", "react-dom", "scheduler", "lucide-react", "@fontsource/manrope", "@fontsource/ibm-plex-mono"):
        source = ROOT / "frontend/node_modules" / package
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "OFL.txt"):
            if (source / name).is_file():
                shutil.copy2(source / name, notices / (package.replace("/", "-") + "-" + name))
    shutil.copy2(ROOT / "docs/SOURCES.md", notices / "SOURCES.md")
    shutil.copy2(ROOT / "packaging/INSTALLED_README.md", STAGE / "Прочитать перед запуском.md")
    shutil.copy2(ROOT / "data/tools/build/whisper-LICENSE", model / "LICENSE")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
                    "/reference:System.Windows.Forms.dll", f"/win32icon:{STAGE / 'assets/icons/survivalflow-v1.ico'}",
                    f"/out:{STAGE / 'FrameFlow.exe'}", str(ROOT / "packaging/Launcher.cs")], check=True)
    source_hash = hashlib.sha256()
    for folder in ("backend", "scripts", "frontend/dist"):
        for p in sorted((STAGE / folder).rglob("*")):
            if p.is_file():
                source_hash.update(p.relative_to(STAGE).as_posix().encode())
                source_hash.update(p.read_bytes())
    manifest = {"product": "FrameFlow", "version": version, "architecture": "x64",
                "built_at": datetime.now(UTC).isoformat(), "python": sys.version.split()[0],
                "source_sha256": source_hash.hexdigest(), "default_data": "%LOCALAPPDATA%/FrameFlow/data",
                "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}
    (STAGE / "frameflow-build.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    files = [p for p in STAGE.rglob("*") if p.is_file()]
    forbidden = [str(p.relative_to(STAGE)) for p in files if p.suffix in (".sqlite3", ".dpapi")
                 or (p.suffix in (".json", ".txt") and
                     p.name.startswith(("cookies", "storage_state", "client_secret")))]
    assert not forbidden, "Private data must never enter a distribution"
    (STAGE / "checksums.sha256").write_text("\n".join(
        digest(p) + "  " + p.relative_to(STAGE).as_posix() for p in sorted(files)) + "\n", encoding="utf-8")
    probe_env = {**os.environ, "LOCALAPPDATA": str(BUILD / "probe-profile"),
                 "SURVIVALFLOW_DATA": str(BUILD / "probe-data"),
                 "PATH": str(Path(os.environ["WINDIR"]) / "System32")}
    probe = subprocess.run([str(runtime / "python.exe"), "-B", str(STAGE / "scripts/installed_entry.py"),
                            "--probe"], env=probe_env, capture_output=True, text=True, encoding="utf-8")
    (BUILD / "probe.log").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    if probe.returncode:
        raise RuntimeError("Bundled Python/native dependencies failed; see build/windows/probe.log")
    (BUILD / "probe.json").write_text(probe.stdout, encoding="utf-8")
    subprocess.run([str(args.iscc), f"/DPayload={STAGE}", f"/DVersion={version}",
                    f"/DReleaseDir={RELEASES}", str(ROOT / "packaging/frameflow.iss")], cwd=ROOT, check=True)
    installer = RELEASES / f"FrameFlow-Setup-{version}-x64.exe"
    summary = {**manifest, "installer": str(installer), "installer_bytes": installer.stat().st_size,
               "installer_sha256": digest(installer), "payload_bytes": sum(p.stat().st_size for p in files),
               "payload_files": len(files), "private_files": forbidden}
    (RELEASES / "build-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (RELEASES / (installer.name + ".sha256")).write_text(summary["installer_sha256"] + "  " + installer.name + "\n", encoding="ascii")
    print(json.dumps({k: summary[k] for k in ("installer", "installer_bytes", "payload_bytes", "installer_sha256")}))


if __name__ == "__main__":
    main()

"""Install/update/uninstall only a disposable test instance; never touch the running project."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/validation/windows-installer"


def child():
    import webview

    from scripts import desktop, launch

    base = launch.ensure_services()
    result = {"checks": [], "errors": []}

    def exercise():
        window = webview.windows[0]
        try:
            assert window.events.loaded.wait(30)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if window.evaluate_js("document.querySelector('.e-source') !== null"):
                    break
                time.sleep(.2)
            assert webview.renderer == "edgechromium"
            result["checks"].append("Installed Python/pythonnet rendered real WebView2")
            for name in ["Истории", "Статистика", "Каналы", "Настройки", "Очередь", "Монтаж"]:
                window.evaluate_js("[...document.querySelectorAll('.e-nav button')].find(b=>b.textContent.trim()==="
                                   + json.dumps(name) + ").click()")
                time.sleep(.4)
                geometry = window.evaluate_js("({w:innerWidth,h:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight})")
                assert geometry["w"] == geometry["sw"] and geometry["h"] == geometry["sh"], geometry
                result["checks"].append("Installed UI: " + name)
        except Exception as error:
            result["errors"].append(str(error))
        finally:
            Path(os.environ["FRAMEFLOW_INSTALL_RESULT"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            window.destroy()

    original_start = webview.start

    def start(**kwargs):
        return original_start(exercise, **kwargs)

    webview.start = start
    with desktop.instance_lock() as primary:
        assert primary
        desktop.open_window(base)
    assert not result["errors"], result["errors"]


def run(command, env, timeout=120):
    result = subprocess.run([str(c) for c in command], env=env, capture_output=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8", errors="replace")


def parent(installer):
    OUT.mkdir(parents=True, exist_ok=True)
    sandbox = ROOT / "build" / ("installer-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    install = sandbox / "Установка FrameFlow"
    local = sandbox / "profile"
    data = local / "FrameFlow/data"
    env = {**os.environ, "LOCALAPPDATA": str(local), "SURVIVALFLOW_PORT": "8877",
           "FRAMEFLOW_INSTALL_RESULT": str(sandbox / "native.json"),
           "PATH": str(Path(os.environ["WINDIR"]) / "System32")}
    for key in ("SURVIVALFLOW_DATA", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    sandbox.mkdir(parents=True)
    report = {"installer": str(installer), "install": str(install), "data": str(data), "checks": []}
    python = install / "runtime/python.exe"
    entry = install / "scripts/installed_entry.py"
    installed = False
    try:
        run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TESTINSTALL=1",
             "/DIR=" + str(install), "/LOG=" + str(sandbox / "install.log")], env, 240)
        installed = True
        report["checks"].append("Per-user install, path contains spaces and Cyrillic, no shortcuts")
        probe = json.loads(run([python, "-B", entry, "--probe"], env))
        assert probe["packaged"] and Path(probe["data"]) == data and Path(probe["root"]) == install
        assert probe["local_model"] and probe["schema"] == 11
        assert probe["isolated"] == 1 and Path(probe["python_prefix"]).is_relative_to(install)
        framework = Path(os.environ["WINDIR"]) / "Microsoft.NET"
        assert all(Path(p).is_relative_to(install) or Path(p).is_relative_to(framework)
                   for p in probe["sys_path"])
        report["probe"] = probe
        report["checks"].append("Relocated Python/native libraries/model with global Python and Node absent from PATH")
        run([python, "-B", "-c", "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(channel='chrome',headless=True);page=b.new_page();page.set_content('<title>FrameFlow test</title>');assert page.title()=='FrameFlow test';b.close()"], env)
        report["checks"].append("Bundled Playwright Node driver opened an isolated Chrome context")
        run([install / "FrameFlow.exe", "--services"], env)
        report["checks"].append("Native FrameFlow.exe launcher handles the installed Unicode path")
        base = "http://127.0.0.1:8877/api"
        with httpx.Client(base_url=base, timeout=30) as api:
            health = api.get("/health").json()
            assert Path(health["data_directory"]) == data
            sample = sandbox / "sample.mp4"
            run([install / "resources/tools/ffmpeg.exe", "-v", "error", "-y", "-f", "lavfi", "-i",
                 "testsrc2=size=90x160:rate=10:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                 "-c:v", "libx264", "-c:a", "aac", sample], env)
            with sample.open("rb") as file:
                response = api.post("/assets?kind=video", files={"file": ("sample.mp4", file, "video/mp4")})
            response.raise_for_status()
            asset = response.json()
            account = api.post("/accounts", json={"name": "Installer local test", "platform": "youtube"}).json()
            api.patch("/settings", json={"buba_required": False, "one_per_platform": False,
                                         "project_name": "Installer preservation test", "paused": False}).raise_for_status()
            edit = {**asset["edit"], "duration": .5, "format": "720", "targets": [account["id"]]}
            api.put(f"/assets/{asset['id']}/edit", json=edit).raise_for_status()
            queued = api.post("/queue", json={"asset_ids": [asset["id"]], "account_ids": [account["id"]]})
            queued.raise_for_status()
            job_id = queued.json()["created"][0]
            api.get("/analytics/averages").raise_for_status()
            assert not api.get("/stories").json()["stories"]
            native = subprocess.run([str(python), "-B", str(Path(__file__).resolve()), "--child"], env=env,
                                     capture_output=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW)
            assert native.returncode == 0, native.stderr.decode("utf-8", errors="replace")
            report["native"] = json.loads((sandbox / "native.json").read_text(encoding="utf-8"))
            for _ in range(100):
                state = api.get("/state").json()
                job = next(j for j in state["jobs"] if j["id"] == job_id)
                assert job["status"] != "failed", job["error"]
                if job["status"] == "ready" and state["worker"]:
                    break
                time.sleep(.3)
            else:
                raise RuntimeError("Installed worker did not complete the render")
            assert not job["approved"] and not job["external_id"]
            assert api.get(f"/jobs/{job_id}/file", headers={"Range": "bytes=0-255"}).status_code == 206
            report["checks"].append("Installed API/worker rendered MP4, kept running after window close, no upload authorized")
        run([install / "FrameFlow.exe", "--stop"], env)
        marker = data / "uploads/preserve-user-file.txt"
        marker.write_text("Preserve on update and uninstall", encoding="utf-8")
        run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TESTINSTALL=1",
             "/DIR=" + str(install), "/LOG=" + str(sandbox / "upgrade.log")], env, 240)
        run([python, "-B", entry, "--services"], env)
        with httpx.Client(base_url=base, timeout=10) as api:
            state = api.get("/state").json()
            assert state["settings"]["project_name"] == "Installer preservation test"
            assert len(state["jobs"]) == 1 and len(state["accounts"]) == 1 and marker.is_file()
        report["checks"].append("Upgrade retained SQLite, account, job, settings and user file")
        run([python, "-B", entry, "--stop"], env)
        uninstall = install / "uninstall/unins000.exe"
        run([uninstall, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], env, 240)
        installed = False
        assert marker.is_file() and (data / "survivalflow.sqlite3").is_file()
        assert not (install / "FrameFlow.exe").exists()
        report["checks"].append("Uninstall removed program and preserved data outside the installation")
        report["ok"] = True
    finally:
        if python.exists():
            try:
                run([python, "-B", entry, "--stop"], env)
            except Exception:
                pass
        report["test_install_left"] = installed
        (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": report["checks"], "native_checks": len(report["native"]["checks"])}))


if __name__ == "__main__":
    if "--child" in sys.argv:
        child()
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("installer", type=Path)
        args = parser.parse_args()
        parent(args.installer.resolve())

"""Exercise the actual Windows/WebView2 shell in isolated data; never publish."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def wait_until(check, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError("Desktop check timed out")


def child():
    import webview

    from scripts import desktop, launch

    base = launch.ensure_services()
    api = httpx.Client(base_url=base + "/api", timeout=20)
    sample = Path(os.environ["SURVIVALFLOW_TEST_FIXTURES"]) / "sample-0.mp4"
    with sample.open("rb") as file:
        response = api.post("/assets?kind=video", files={"file": ("desktop-check.mp4", file, "video/mp4")})
    response.raise_for_status()
    asset = response.json()
    account = api.post("/accounts", json={"platform": "youtube", "name": "Локальная проверка окна"}).json()
    edit = {**asset["edit"], "duration": 0.5, "format": "720", "targets": [account["id"]]}
    api.put(f"/assets/{asset['id']}/edit", json=edit).raise_for_status()
    job = api.post("/queue", json={"asset_ids": [asset["id"]], "account_ids": [account["id"]]}).json()[
        "created"
    ][0]
    original_create = webview.create_window
    original_start = webview.start
    holder = {}
    report = {"checks": [], "job_id": job, "errors": []}

    def create(*args, **kwargs):
        holder["state"] = kwargs["js_api"]
        return original_create(*args, **kwargs)

    def exercise():
        window = webview.windows[0]
        try:
            assert window.events.loaded.wait(35)
            wait_until(lambda: window.evaluate_js("document.querySelector('.e-source') !== null"))
            assert webview.renderer == "edgechromium"
            assert window.evaluate_js("location.origin") == base
            assert webview.settings["ALLOW_DOWNLOADS"] is True
            report["checks"].append("actual WebView2 renderer and local API")
            window.resize(1040, 680)
            time.sleep(0.5)
            for view in ["Очередь", "Каналы", "Статистика", "Настройки", "Монтаж"]:
                window.evaluate_js(
                    "[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==="
                    + json.dumps(view)
                    + ").click()"
                )
                time.sleep(0.3)
                bounds = window.evaluate_js(
                    "({w:innerWidth,h:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,over:[...document.querySelectorAll('.e-content,.e-assets,.e-inspector')].some(e=>e.scrollHeight>e.clientHeight+2)})"
                )
                assert bounds["w"] >= 1024 and bounds["h"] >= 600, bounds
                assert bounds["w"] == bounds["sw"] and bounds["h"] == bounds["sh"] and not bounds["over"], (
                    bounds
                )
                report["checks"].append({"view": view, "bounds": bounds})
            # Exercise a real React change and its narrow native unsaved-state bridge.
            window.evaluate_js("""(() => {
                const label=[...document.querySelectorAll('label')].find(l=>l.textContent.trim()==='Длительность, с');
                const input=document.getElementById(label.htmlFor);
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'0.6');
                input.dispatchEvent(new Event('input',{bubbles:true}));
            })()""")
            wait_until(lambda: holder["state"]._dirty)
            window.evaluate_js(
                "[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Сохранить').click()"
            )
            wait_until(lambda: not holder["state"]._dirty)
            report["checks"].append("React edit/save synchronizes native unsaved guard")
            window.evaluate_js("""(() => {
                const video=document.querySelector('video'); video.muted=true;
                video.play().catch(e=>document.body.dataset.videoError=e.message);
            })()""")
            wait_until(lambda: window.evaluate_js("document.querySelector('video')?.currentTime > 0.05"))
            assert not window.evaluate_js("document.body.dataset.videoError || ''")
            report["checks"].append("source MP4 decodes and plays in WebView2")
            duplicate = subprocess.run(
                [sys.executable, "scripts/launch.py"], cwd=ROOT, timeout=25, capture_output=True
            )
            assert duplicate.returncode == 0, duplicate.stderr.decode(errors="replace")
            report["checks"].append("duplicate launch focuses existing window and exits")
            api.patch("/settings", json={"paused": False}).raise_for_status()
        except Exception as error:
            report["errors"].append(repr(error))
        finally:
            (Path(os.environ["SURVIVALFLOW_DATA"]) / "desktop-result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            window.destroy()

    def start(**kwargs):
        return original_start(exercise, **kwargs)

    webview.create_window, webview.start = create, start
    with desktop.instance_lock() as primary:
        assert primary
        desktop.open_window(base)
    api.close()
    assert not report["errors"], report["errors"]


def parent():
    folder = Path(tempfile.mkdtemp(prefix="survivalflow-desktop-"))
    env = {**os.environ, "SURVIVALFLOW_DATA": str(folder), "SURVIVALFLOW_PORT": "8772"}
    env["SURVIVALFLOW_TEST_FIXTURES"] = sys.argv[1]
    process = None
    try:
        with (folder / "desktop.log").open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, __file__, "--child"],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert process.wait(timeout=100) == 0, (folder / "desktop.log").read_text(errors="replace")
        report = json.loads((folder / "desktop-result.json").read_text(encoding="utf-8"))
        with httpx.Client(base_url="http://127.0.0.1:8772/api", timeout=10) as api:

            def ready():
                state = api.get("/state").json()
                job = next(j for j in state["jobs"] if j["id"] == report["job_id"])
                assert state["worker"]
                assert job["status"] != "failed", job["error"]
                return job if job["status"] == "ready" else None

            job = wait_until(ready, 60)
            assert not job["approved"] and job["has_output"]
            video = api.get(f"/jobs/{job['id']}/file", headers={"Range": "bytes=0-1023"})
            assert video.status_code == 206
        report["checks"].append(
            "window closed: API and worker survive, export ready and unapproved, MP4 range response"
        )
        out = ROOT / "docs" / "validation" / "desktop"
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True))
    finally:
        if process and process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        subprocess.run(
            [sys.executable, "scripts/stop.py"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


if __name__ == "__main__":
    child() if sys.argv[1] == "--child" else parent()

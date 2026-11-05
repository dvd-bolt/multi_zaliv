"""UI check of the growth tools at 1024×600 on a temporary database (no platform requests).

Covers: queue dialog with best time per channel, author comment and TikTok sound options, the sign-in
check before auto-publishing (a failed check must stop the launch), the channel card button and the job
details. Screenshots: docs/validation/growth/. Run from the repository root:
    SURVIVALFLOW_TEST_CHROMIUM=/path/to/chrome .venv/bin/python scripts/verify_growth_ui.py
Platform checks are replaced by fakes; this is not a test of real TikTok/YouTube accounts.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(tempfile.mkdtemp(prefix="frameflow-growth-ui-"))
os.environ["SURVIVALFLOW_DATA"] = str(DATA)
PORT = 8791
os.environ["SURVIVALFLOW_PORT"] = str(PORT)  # the app accepts writes only from its own origin
os.environ.setdefault("SURVIVALFLOW_FFMPEG", "/usr/bin/ffmpeg")
os.environ.setdefault("SURVIVALFLOW_FFPROBE", "/usr/bin/ffprobe")
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "validation" / "growth"
OUT.mkdir(parents=True, exist_ok=True)

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from starlette.routing import Mount  # noqa: E402

import backend.app as module  # noqa: E402
from backend import preflight  # noqa: E402
from backend.db import Account, ChannelVideo, Job, Session, Setting, settings  # noqa: E402

module.ensure_queue_worker = lambda: None
VERDICT = {"value": "ok"}
preflight.CHECKS.update(
    youtube=lambda account: (VERDICT["value"], "Синтетическая проверка: " + VERDICT["value"]),
    tiktok=lambda account: (VERDICT["value"], "Синтетическая проверка: " + VERDICT["value"]),
)
LONG = "Очень длинное название канала для проверки вёрстки " + "x" * 30
with Session.begin() as db:
    db.get(Setting, "application").value = {**settings(db), "buba_required": False, "paused": True}
    db.add(
        Account(
            id="yt1", platform="youtube", name="labs clode", external_id="UC1", connected=True, method="oauth"
        )
    )
    db.add(
        Account(
            id="tt1",
            platform="tiktok",
            name="@labsclode",
            external_id="labsclode",
            connected=True,
            method="browser_verified",
        )
    )
    for i in range(4):
        db.add(
            Account(
                id=f"x{i}",
                platform="youtube" if i < 2 else "tiktok",
                name=f"{LONG} {i}",
                external_id=f"UCX{i}" if i < 2 else f"long{i}",
                connected=True,
                method="oauth" if i < 2 else "browser_verified",
            )
        )
    start = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)  # 19:00 Moscow
    for d in range(10):
        published = start + timedelta(days=d, hours=0 if d % 2 else -10)
        views = 3000 if d % 2 else 900
        db.add(
            ChannelVideo(
                account_id="yt1",
                external_id=f"V{d}",
                details={
                    "title": f"Ролик {d}",
                    "published_at": published.isoformat(),
                    "visibility": "public",
                    "values": {},
                },
                history=[
                    {
                        "captured": (published + timedelta(hours=h)).isoformat(),
                        "values": {"views": views * h // 24},
                    }
                    for h in (20, 28)
                ],
            )
        )
app = module.app
app.router.routes.sort(key=lambda route: isinstance(route, Mount))
threading.Thread(
    target=lambda: uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error"), daemon=True
).start()
base = f"http://127.0.0.1:{PORT}"


def api(client, path, method="GET", body=None):
    response = client.fetch(
        base + "/api" + path,
        method=method,
        data=json.dumps(body) if body else None,
        headers={"Content-Type": "application/json"},
    )
    assert response.ok, response.text()
    return response.json()


results = {"synthetic": True, "checks": {}, "errors": []}
source = DATA / "source.mp4"
subprocess.run(
    [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=blue:size=90x160:rate=5:duration=3",
        "-c:v",
        "libx264",
        str(source),
    ],
    check=True,
)
with sync_playwright() as pw:
    executable = os.environ.get("SURVIVALFLOW_TEST_CHROMIUM")
    browser = pw.chromium.launch(
        **({"executable_path": executable} if executable else {"channel": "chrome"}), headless=True
    )
    page = browser.new_page(viewport={"width": 1024, "height": 600})
    page.on("pageerror", lambda e: results["errors"].append(str(e)))
    for _ in range(100):
        try:
            if page.request.get(base + "/api/health").ok:
                break
        except Exception:
            time.sleep(0.1)
    imported = page.request.post(
        base + "/api/assets?kind=video",
        multipart={"file": {"name": "source.mp4", "mimeType": "video/mp4", "buffer": source.read_bytes()}},
    )
    assert imported.ok, imported.text()
    imported = imported.json()
    edit = {
        **imported["edit"],
        "buba_mode": False,
        "format": "720",
        "duration": 0.5,
        "title": "Пельмень",
        "caption": "Пример",
        "visibility": "public",
        "targets": ["yt1", "tt1"],
    }
    assert page.request.put(base + f"/api/assets/{imported['id']}/edit", data=edit).ok
    page.goto(base)

    def shot(name):
        dialog = page.get_by_role("dialog")
        bounds = dialog.evaluate(
            "e => ({top: e.getBoundingClientRect().top, bottom: e.getBoundingClientRect().bottom,"
            " scroll: e.scrollHeight > e.clientHeight + 1})"
        )
        page.screenshot(path=str(OUT / f"{name}.png"))
        assert bounds["top"] >= 0 and bounds["bottom"] <= 600 and not bounds["scroll"], (name, bounds)
        results["checks"][name] = bounds

    page.get_by_role("button", name="В очередь · 2 вариантов", exact=True).click()
    shot("queue-dialog")
    page.get_by_role("button", name="Лучшее время", exact=True).click()
    page.get_by_role("list", name="Время по каналам").wait_for()
    times = page.get_by_role("list", name="Время по каналам").inner_text()
    assert "лучший час" in times and "по умолчанию" in times, times
    shot("queue-best-time")
    page.get_by_role("button", name="Комментарий и звук…", exact=True).click()
    page.get_by_label("Комментарий после публикации (необязательно)").fill("А вы бы открыли {название}?")
    page.get_by_label("TikTok · звук из «Избранного» (необязательно)").fill("Ночной холодильник")
    shot("queue-comment-sound")
    page.get_by_role("button", name="Готово", exact=True).click()
    page.get_by_text("комментарий 📌 · звук TikTok").wait_for()
    shot("queue-with-options")

    VERDICT["value"] = "auth"
    page.get_by_role("button", name="Запустить автозалив", exact=True).click()
    alert = page.get_by_role("alert")
    alert.wait_for()
    assert "Автозалив не запущен" in alert.inner_text(), alert.inner_text()
    shot("queue-sign-in-failed")
    with Session() as db:
        assert not db.query(Job).count(), "failed sign-in check must not queue jobs"
    page.get_by_role("button", name="К параметрам очереди", exact=True).click()
    VERDICT["value"] = "ok"
    page.get_by_role("button", name="Запустить автозалив", exact=True).click()
    page.get_by_role("dialog").wait_for(state="detached")
    with Session() as db:
        jobs = {j.extras.get("sound", {}).get("name"): j for j in db.query(Job).all()}
        assert len(jobs) == 2 and "Ночной холодильник" in jobs, jobs
        scheduled = sorted(j.scheduled for j in jobs.values())
        assert all(scheduled), scheduled
        for job in jobs.values():
            assert job.extras["comment"]["text"] == "А вы бы открыли {название}?"
        # Show the details of a "published" YouTube job with a comment waiting for a manual pin.
        target = next(j for j in jobs.values() if "sound" not in j.extras)
        target.status, target.external_id = "published", "VIDEO123456"
        target.url = "https://www.youtube.com/watch?v=VIDEO123456"
        target.extras = {
            **target.extras,
            "comment": {
                **target.extras["comment"],
                "status": "pin_manual",
                "pin_url": "https://www.youtube.com/watch?v=VIDEO123456&lc=C1",
                "detail": "Комментарий опубликован. Закрепите вручную.",
            },
        }
        db.commit()
    page.reload()
    page.get_by_role("button", name="Очередь", exact=True).click()
    details = page.get_by_role("button", name="Подробнее", exact=True)
    for index in range(details.count()):
        details.nth(index).click()
        if page.get_by_role("link", name="Открыть и закрепить ↗").count():
            break
        page.get_by_role("button", name="Закрыть диалог", exact=True).click()
    page.get_by_role("link", name="Открыть и закрепить ↗").wait_for()
    shot("job-comment-pin")
    page.get_by_role("button", name="Закрыть диалог", exact=True).click()

    # Worst case: six channels with long names, best time for all, options set.
    page.get_by_role("button", name="Монтаж", exact=True).click()
    assert page.request.put(
        base + f"/api/assets/{imported['id']}/edit",
        data={**edit, "targets": ["yt1", "tt1", "x0", "x1", "x2", "x3"]},
    ).ok
    with Session.begin() as db:
        db.get(Setting, "application").value = {**settings(db), "one_per_platform": False}
    page.reload()
    page.get_by_role("button", name="В очередь · 6 вариантов", exact=True).click()
    page.get_by_role("button", name="Лучшее время", exact=True).click()
    page.get_by_role("list", name="Время по каналам").wait_for()
    shot("queue-six-channels")
    page.get_by_role("button", name="Закрыть диалог", exact=True).click()

    page.get_by_role("button", name="Каналы", exact=True).click()
    page.get_by_role("button", name="✓ Вход").first.wait_for()
    page.screenshot(path=str(OUT / "channels-sign-in.png"))
    overflow = page.evaluate(
        "[...document.querySelectorAll('.e-channel')].some(e => e.scrollWidth > e.clientWidth + 1)"
    )
    assert not overflow, "channel card overflows"
    results["checks"]["channels"] = {"overflow": overflow}
    browser.close()
assert not results["errors"], results["errors"]
(OUT / "ui-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps(results, ensure_ascii=False))

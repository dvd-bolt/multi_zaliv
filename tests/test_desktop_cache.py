from fastapi.testclient import TestClient

from backend.app import app
from scripts import desktop


def test_api_and_entry_do_not_cache_stale_state():
    client = TestClient(app)
    for path in ("/", "/index.html", "/api/state"):
        assert client.get(path).headers["cache-control"] == "no-store"


def test_desktop_entry_url_changes_with_frontend_build(tmp_path, monkeypatch):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('script src="/assets/build-one.js"')
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    first = desktop.entry_url("http://127.0.0.1:8765")
    assert first == desktop.entry_url("http://127.0.0.1:8765/")
    (dist / "index.html").write_text('script src="/assets/build-two.js"')
    assert first != desktop.entry_url("http://127.0.0.1:8765")

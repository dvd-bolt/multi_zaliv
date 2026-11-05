"""Sound from Favorites and TikTok's copyright check on local pages: no TikTok requests."""

import os
import time

import pytest
from playwright.sync_api import sync_playwright

from backend import tiktok_upload
from backend.tiktok_upload import BeforePublishError, upload_on_page

COOKIE = {
    "name": "sessionid",
    "value": "synthetic",
    "domain": ".tiktok.com",
    "path": "/",
    "expires": time.time() + 3600,
    "secure": True,
}
VIDEO_ID = "7456123456789012345"
SOUND_PANEL = """
<button onclick="document.querySelector('#panel').hidden=false">Sounds</button>
<div id="panel" hidden>
  <button onclick="document.querySelector('#list').hidden=false">Favorites</button>
  <div id="list" hidden>
    <div class="MusicPanelMusicItem__content-x">Lo-fi night <span>artist</span><button>Preview</button>
      <button onclick="window.used='lofi';document.querySelector('#vol').hidden=false">Use</button></div>
    <div class="MusicPanelMusicItem__content-x">Ночной холодильник <span>FrameFlow</span><button>Preview</button>
      <button onclick="window.used='fridge';document.querySelector('#vol').hidden=false">Use</button></div>
  </div>
  <div id="vol" hidden>
    <input class="scaleInput" type="range" style="width:200px;display:block">
    <input class="scaleInput" id="added" type="range" style="width:200px;display:block">
  </div>
  <button onclick="window.saved=window.used;document.querySelector('#panel').hidden=true">Save</button>
  <button onclick="window.cancelled=true;document.querySelector('#panel').hidden=true">Cancel</button>
</div>"""
COPYRIGHT = """<div data-e2e="copyright_container"><span data-part="thumb" onclick="setTimeout(()=>{
  const r=document.createElement('span');r.textContent=window.verdict;if(window.verdict)document.body.appendChild(r)},300)">o</span></div>"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        executable = os.environ.get("SURVIVALFLOW_TEST_CHROMIUM")
        b = pw.chromium.launch(
            **({"executable_path": executable} if executable else {"channel": "chrome"}), headless=True
        )
        yield b
        b.close()


def run(browser, tmp_path, verdict="", sound=None, copyright="off"):
    context = browser.new_context()
    context.add_cookies([COOKIE])
    page = context.new_page()
    posts = []
    upload = f"""<input type="file" accept="video/mp4"><div contenteditable="true"></div>
    <div data-e2e="video_visibility_container"><button role="combobox" onclick="document.querySelector('#options').hidden=false">Everyone</button></div>
    <div id="options" hidden><div role="option" onclick="choose('Everyone')">Everyone</div></div>
    {SOUND_PANEL}{COPYRIGHT}
    <button data-e2e="post_video_button" onclick="fetch('/api/v1/web/project/post/',{{method:'POST'}}).catch(()=>{{}})">Post</button>
    <script>window.verdict={verdict!r};function choose(t){{document.querySelector('[role=combobox]').textContent=t;document.querySelector('#options').hidden=true}}</script>"""

    def route(r):
        path = r.request.url.split("tiktok.com", 1)[-1]
        if r.request.method == "POST":
            posts.append(path)
            r.fulfill(json={"status_code": 0, "data": {"item_id": VIDEO_ID}})
        elif path.startswith("/tiktokstudio/upload"):
            r.fulfill(body=upload, content_type="text/html; charset=utf-8")
        else:
            r.fulfill(
                body='<a data-e2e="nav-profile" href="/@cartoon.test">Own</a>', content_type="text/html"
            )

    context.route("**/*", route)
    source = tmp_path / "dummy.mp4"
    source.write_bytes(b"synthetic video input")
    page.goto("https://www.tiktok.com/")
    config = {"visibility": "public", "caption": "Тест"}
    try:
        result = upload_on_page(
            page, source, config, "cartoon.test", timeout=2, sound=sound, copyright=copyright
        )
        state = page.evaluate("({used: window.used, saved: window.saved, cancelled: !!window.cancelled})")
        level = page.evaluate("Number(document.querySelector('#added').value)")
        return result, posts, state, level
    except BeforePublishError as error:
        return error, posts, None, None
    finally:
        context.close()


def test_favorite_sound_is_added_quietly(browser, tmp_path):
    result, posts, state, level = run(browser, tmp_path, sound="ночной холодильник")
    assert state["saved"] == "fridge" and level <= 20
    assert result.checks["sound"] == "added" and len(posts) == 1


def test_missing_sound_is_cancelled_and_video_still_posts(browser, tmp_path):
    result, posts, state, _ = run(browser, tmp_path, sound="Нет такого трека")
    assert state["cancelled"] and not state["saved"]
    assert result.checks["sound"] == "skipped" and len(posts) == 1


def test_copyright_ok_is_recorded(browser, tmp_path):
    result, posts, *_ = run(browser, tmp_path, verdict="No issues found.", copyright="warn")
    assert result.checks["copyright"] == "ok" and len(posts) == 1


def test_copyright_issue_stops_before_post(browser, tmp_path):
    error, posts, *_ = run(browser, tmp_path, verdict="Copyright issues detected", copyright="warn")
    assert isinstance(error, BeforePublishError) and "авторскими" in str(error) and not posts


def test_unfinished_check_warns_or_stops(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(tiktok_upload, "COPYRIGHT_WAIT", 3)
    result, posts, *_ = run(browser, tmp_path, verdict="", copyright="warn")
    assert result.checks["copyright"] == "timeout" and len(posts) == 1
    error, posts, *_ = run(browser, tmp_path, verdict="", copyright="strict")
    assert isinstance(error, BeforePublishError) and not posts


def test_off_touches_nothing(browser, tmp_path):
    result, posts, state, _ = run(browser, tmp_path, verdict="Copyright issues detected")
    assert result.checks is None and not state["used"] and len(posts) == 1


COMMENTS = """
<div data-e2e="comment-input"><div contenteditable="true" id="box"></div></div>
<button data-e2e="comment-post" onclick="add(document.querySelector('#box').innerText)">Post</button>
<div id="list"></div>
<div id="menu" hidden><div onclick="window.pinned=true;document.querySelector('#menu').hidden=true">Pin</div></div>
<script>
window.posted=0;
function add(text){window.posted++;const d=document.createElement('div');d.setAttribute('data-e2e','comment-level-1');
d.innerHTML='<a href="/@cartoon.test">cartoon.test</a> <p></p><span data-e2e="comment-more-icon" onclick="document.querySelector(\\'#menu\\').hidden=false">…</span>';
d.querySelector('p').textContent=text;document.querySelector('#list').appendChild(d)}
</script>"""


def comment_page(browser):
    from backend import author_comment

    page = browser.new_page()
    page.set_content(COMMENTS)
    box = page.locator('[data-e2e="comment-input"] [contenteditable="true"]')
    box.click()
    page.keyboard.insert_text("Кто съел пельмень?")
    page.locator('[data-e2e="comment-post"]').click()
    found = author_comment.tiktok_find(page, "cartoon.test", "Кто съел пельмень?")
    return page, found, author_comment


def test_tiktok_comment_is_found_by_own_handle_and_pinned(browser):
    page, found, author_comment = comment_page(browser)
    assert found is not None
    assert author_comment.tiktok_find(page, "someone.else", "Кто съел пельмень?") is None
    assert author_comment.tiktok_pin(page, found)
    assert page.evaluate("window.pinned") and page.evaluate("window.posted") == 1
    page.close()

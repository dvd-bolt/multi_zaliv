"""Confirm a known TikTok publication from its logged-out first-party page."""

import json
import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit


class ItemScript(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.value = ""

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("id") == "__UNIVERSAL_DATA_FOR_REHYDRATION__"

    def handle_data(self, data):
        if self.active:
            self.value += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False


def verified_item(html, handle, video_id):
    try:
        parser = ItemScript()
        parser.feed(html)
        page = json.loads(parser.value)["__DEFAULT_SCOPE__"]["webapp.video-detail"]
        if page.get("statusCode") != 0:
            return False
        item = page["itemInfo"]["itemStruct"]
        valid = (
            str(item.get("id")) == video_id
            and str(item.get("author", {}).get("uniqueId", "")).casefold() == handle.casefold()
            and item.get("privateItem") is False
            and bool(item.get("video", {}).get("playAddr"))
        )
        return item if valid else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def public_item(html, handle, video_id):
    return bool(verified_item(html, handle, video_id))


def fetch_item(handle, video_id):
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", handle or "") or not re.fullmatch(
        r"[0-9]{15,25}", video_id or ""
    ):
        return False
    try:
        request = urllib.request.Request(
            f"https://www.tiktok.com/@{handle}/video/{video_id}", headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.build_opener().open(request, timeout=15) as response:
            if urlsplit(response.url).scheme != "https" or urlsplit(response.url).hostname not in (
                "www.tiktok.com",
                "tiktok.com",
            ):
                return False
            html = response.read(2500000).decode("utf-8", errors="replace")
        return verified_item(html, handle, video_id)
    except Exception:
        return False


def available(handle, video_id):
    return bool(fetch_item(handle, video_id))


def item_metrics(item):
    """Only exact nonnegative counters; missing/invalid fields stay unavailable."""
    stats = item.get("statsV2") or item.get("stats") or {}
    values = {}
    for key, remote in (
        ("views", "playCount"),
        ("likes", "diggCount"),
        ("comments", "commentCount"),
        ("shares", "shareCount"),
    ):
        raw = stats.get(remote)
        valid = (type(raw) is int and raw >= 0) or (
            isinstance(raw, str) and re.fullmatch(r"[0-9]{1,20}", raw) is not None
        )
        values[key] = int(raw) if valid else None
    if all(value is None for value in values.values()):
        raise ValueError("TikTok не вернул публичные счётчики")
    return values


def metrics(handle, video_id):
    item = fetch_item(handle, video_id)
    if not item:
        raise ValueError("Публичная статистика TikTok недоступна")
    return item_metrics(item)

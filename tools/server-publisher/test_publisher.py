"""Queue logic of publisher.py with Telegram and YouTube replaced by fakes. Run: python3 -m unittest test_publisher"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publisher as p


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.video = root / "ep.mp4"
        self.video.write_bytes(b"0" * 1000)
        cfg = root / "config.json"
        cfg.write_text(json.dumps({"chat_id": 1, "telegram_env": "/dev/null", "channels": {
            "labs": {"title": "labs clode", "channel_id": "UC1", "stats_account": "labs clode", "credentials": "/dev/null"}}}))
        patches = [mock.patch.object(p, "DATA", root), mock.patch.object(p, "QUEUE", root / "q.json"),
                   mock.patch.object(p, "DECISIONS", root / "d"), mock.patch.object(p, "CONFIG", cfg),
                   mock.patch.object(p, "channel_history", return_value=[]),
                   mock.patch.object(p, "telegram", side_effect=self.fake_telegram)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        (root / "d").mkdir()
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_telegram(self, method, fields, files=None):
        self.sent.append((method, fields))
        return {"message_id": 7, "video": {}}

    def add(self, **extra):
        slot, reason = p.pick_slot(p.load(), "labs")
        job = {"id": "abc123", "video": str(self.video), "sha256": "x", "channel": "labs", "title": "Серия",
               "slot": slot.isoformat(), "slot_reason": reason, "status": "new", **extra}
        p.save({"jobs": [job]})

    def press(self, action):
        (p.DECISIONS / "1-abc123.json").write_text(json.dumps({"id": "abc123", "action": action}))
        p.process(p.load())
        return p.load()["jobs"][0]

    def test_nothing_uploads_without_button(self):
        self.add()
        with mock.patch.object(p, "upload") as up:
            p.process(p.load())
            p.process(p.load())
        up.assert_not_called()
        self.assertEqual(p.load()["jobs"][0]["status"], "asked")
        self.assertEqual(self.sent[0][0], "sendVideo")
        keys = json.loads(self.sent[0][1]["reply_markup"])["inline_keyboard"]
        self.assertEqual(keys[0][0]["callback_data"], "ffp:abc123:approve")

    def test_approve_schedules_future_slot(self):
        self.add()
        p.process(p.load())
        with mock.patch.object(p, "upload", return_value=("VID", "private")) as up:
            job = self.press("approve")
        up.assert_called_once()
        self.assertEqual(job["status"], "scheduled")
        self.assertEqual(job["url"], "https://youtube.com/shorts/VID")

    def test_now_and_cancel(self):
        self.add()
        p.process(p.load())
        with mock.patch.object(p, "upload", return_value=("VID", "public")):
            job = self.press("now")
        self.assertEqual(job["status"], "published")
        self.assertTrue(job["immediate"])
        self.assertIn("уже", p.decide(p.load(), job, "cancel"))  # a published job cannot be cancelled by a late press

    def test_postpone_moves_slot_later(self):
        self.add()
        p.process(p.load())
        before = p.parse_time(p.load()["jobs"][0]["slot"])
        job = self.press("postpone")
        self.assertGreater(p.parse_time(job["slot"]), before)
        self.assertEqual(job["status"], "asked")

    def test_interrupted_upload_is_unknown_not_repeated(self):
        self.add(status="uploading")
        with mock.patch.object(p, "upload") as up:
            p.process(p.load())
        up.assert_not_called()
        self.assertEqual(p.load()["jobs"][0]["status"], "unknown")

    def test_dry_run_never_uploads(self):
        self.add(dry_run=True)
        p.process(p.load())
        with mock.patch.object(p, "upload") as up:
            job = self.press("approve")
        up.assert_not_called()
        self.assertEqual(job["status"], "cancelled")

    def test_slots_keep_three_hours_apart(self):
        q = {"jobs": []}
        first, _ = p.pick_slot(q, "labs")
        q["jobs"].append({"id": "a", "channel": "labs", "slot": first.isoformat(), "status": "asked"})
        second, _ = p.pick_slot(q, "labs")
        self.assertGreaterEqual(abs(second - first), dt.timedelta(hours=3))


if __name__ == "__main__":
    unittest.main()

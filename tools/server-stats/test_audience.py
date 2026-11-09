"""Vote counting and digest sections. Run: .venv/bin/python -m unittest test_audience"""
import unittest

import audience as a


def c(i, author, text, likes=0, reply=False, posted="2026-11-09T10:00:00+00:00"):
    return str(i), {"id": str(i), "author": author, "text": text, "likes": likes, "reply": reply,
                    "posted": posted, "seen": posted}


class VoteTest(unittest.TestCase):
    poll = {"question": "?", "options": {"Лампочку": ["лампоч"], "Сосиски": ["сосис"]}}

    def test_one_vote_per_author_latest_wins_disputed_and_replies_skipped(self):
        saved = {"comments": dict([
            c(1, "u1", "Лампочку!", 10, posted="2026-11-09T10:00:00+00:00"),
            c(2, "u1", "нет, сосиски", 0, posted="2026-11-09T11:00:00+00:00"),
            c(3, "u2", "ЛАМПОЧКА", 5),
            c(4, "u3", "лампочку и сосиски"),
            c(5, "u4", "сосиски", reply=True)])}
        result, disputed, total = a.count(self.poll, saved)
        self.assertEqual(result["Сосиски"]["votes"], 1)
        self.assertEqual(result["Лампочку"], {"votes": 1, "support": 5})
        self.assertEqual(len(disputed), 1)
        self.assertEqual(total, 2)

    def test_patterns(self):
        self.assertTrue(a.QUESTION.search("Что за красная баночка"))
        self.assertTrue(a.REQUEST.search("Добавьте пожалуйста котлетки"))
        self.assertTrue(a.REQUEST.search("когда следующая серия"))
        self.assertFalse(a.REQUEST.search("Лампочку"))


if __name__ == "__main__":
    unittest.main()

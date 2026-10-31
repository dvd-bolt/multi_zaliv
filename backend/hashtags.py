"""Hashtags by genre; independent of any AI provider.

A few precise tags work better than a fixed generic set: 3–5 tags in total, the most specific first.
The genre is chosen in the editor or detected from the title and description ("auto").
"""

import re

GENRES = {
    "horror": ("Хоррор", ("#хоррор", "#страшилки", "#страшныеистории", "#анимация")),
    "mystery": ("Мистика", ("#мистика", "#загадка", "#страшныеистории", "#анимация")),
    "comedy": ("Юмор", ("#юмор", "#смешно", "#анимация", "#мультик")),
    "drama": ("Драма", ("#драма", "#жизненно", "#история", "#анимация")),
    "adventure": ("Приключения", ("#приключения", "#выживание", "#история", "#анимация")),
    "scifi": ("Фантастика", ("#фантастика", "#будущее", "#история", "#анимация")),
    "story": ("История", ("#история", "#анимация", "#короткометражка")),
}
KEYWORDS = {
    "horror": r"страш|ужас|хоррор|монстр|крик|кров|тьм|темнот|мертв|призрак|шахт|подвал|не свети|не смотри",
    "mystery": r"мистик|тайн|загад|странн|исчез|проклят|сверхъестеств",
    "comedy": r"смешн|юмор|прикол|шутк|ржак|комеди",
    "drama": r"грустн|слёз|слез|прощ|одиноч|семь|сестр|брат|мам|пап|больниц",
    "adventure": r"выжи|спаст|побег|путешеств|приключ|опасн|лифт",
    "scifi": r"космос|робот|будущ|инопланет|технолог|вирус|эпидем",
}
MAX_TAGS = 5
# Earlier versions always appended these. They are kept in old captions, but no longer added automatically.
DEFAULT_HASHTAGS = GENRES["story"][1]


def detect(text):
    """Genre with the most keyword hits; 'story' if nothing matches."""
    text = (text or "").lower()
    scores = {genre: len(re.findall(pattern, text)) for genre, pattern in KEYWORDS.items()}
    best = max(scores, key=lambda g: scores[g])
    return best if scores[best] else "story"


def present(caption):
    return re.findall(r"(?<!\w)#[\w\d_]+", caption or "")


def tags_for(genre, text=""):
    key = detect(text) if genre in (None, "", "auto") else genre
    return list(GENRES.get(key, GENRES["story"])[1])


def with_defaults(caption, genre="auto", title=""):
    """Append genre tags that are not present yet, keeping the total at MAX_TAGS (mandatory tags stay)."""
    existing = {tag.lower() for tag in present(caption)}
    room = max(0, MAX_TAGS - len(existing))
    missing = [t for t in tags_for(genre, f"{title}\n{caption}") if t.lower() not in existing][:room]
    if not missing:
        return caption
    return caption.rstrip() + ("\n" if caption.strip() else "") + " ".join(missing)


def youtube_tags(caption, genre="auto", title=""):
    """Video tags for YouTube: hashtags of the description plus the genre set, without '#', ≤ 500 chars."""
    words = []
    for tag in present(caption) + tags_for(genre, f"{title}\n{caption}"):
        word = tag.lstrip("#")
        if word.lower() not in (w.lower() for w in words):
            words.append(word)
    result, size = [], 0
    for word in words:
        if size + len(word) + 1 > 480:
            break
        result.append(word)
        size += len(word) + 1
    return result


def title_from(caption, fallback=""):
    """A readable YouTube title from the description when the title field is empty (never 'Видео')."""
    text = re.sub(r"(?<!\w)#[\w\d_]+|https?://\S+", " ", caption or "")
    text = " ".join(text.split())
    sentence = re.split(r"(?<=[.!?…])\s", text, maxsplit=1)[0].strip()
    if len(sentence) > 95:
        sentence = sentence[:95].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    if sentence:
        return sentence
    fallback = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", fallback or "").replace("_", " ").strip()
    return fallback[:95]

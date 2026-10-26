"""Small fixed topical set; independent of any AI provider."""

import re

DEFAULT_HASHTAGS = ("#мультики", "#мультфильмы", "#юмор")


def with_defaults(caption):
    missing = [
        tag
        for tag in DEFAULT_HASHTAGS
        if not re.search(r"(?<!\w)" + re.escape(tag) + r"(?!\w)", caption, re.IGNORECASE)
    ]
    if not missing:
        return caption
    return caption.rstrip() + ("\n" if caption.strip() else "") + " ".join(missing)

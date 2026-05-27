"""Plain-text helpers for Around the League / headlines."""
from __future__ import annotations

HEADLINES_PER_PAGE = 20
HEADLINES_COMMENTS_PER_ARTICLE = 15
HOMEPAGE_AROUND_COUNT = 5
HOMEPAGE_BODY_EXCERPT_LEN = 420


def news_body_excerpt(body: str | None, *, max_len: int = HOMEPAGE_BODY_EXCERPT_LEN) -> str:
    """First paragraph-ish slice for list views (no HTML)."""
    text = str(body or "").strip().replace("\r\n", "\n")
    if not text:
        return ""
    lim = max(80, int(max_len or HOMEPAGE_BODY_EXCERPT_LEN))
    if len(text) <= lim:
        return text
    cut = text[:lim]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"

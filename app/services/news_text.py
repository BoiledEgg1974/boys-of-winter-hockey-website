"""Plain-text helpers for Around the League / headlines."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.site_models import NewsArticle

HEADLINES_PER_PAGE = 10
HEADLINES_COMMENTS_PER_ARTICLE = 15
HOMEPAGE_AROUND_COUNT = 5
HOMEPAGE_BODY_EXCERPT_LEN = 420
GM_ARTICLE_BODY_MAX_LEN = 1500


def headlines_page_for_article_id(
    session: Session,
    league_slug: str,
    article_id: int,
    *,
    per_page: int = HEADLINES_PER_PAGE,
) -> int | None:
    """1-based headlines page containing ``article_id``, or ``None`` if not published/visible."""
    from app.services.news_retention import published_news_age_filter

    slug = str(league_slug or "").strip()
    if not slug:
        return None
    filters = (
        NewsArticle.league_slug == slug,
        NewsArticle.status == "published",
        published_news_age_filter(NewsArticle),
    )
    ids = session.scalars(
        select(NewsArticle.id)
        .where(*filters)
        .order_by(NewsArticle.published_at.desc(), NewsArticle.id.desc())
    ).all()
    try:
        idx = ids.index(int(article_id))
    except ValueError:
        return None
    per = max(1, int(per_page))
    return (idx // per) + 1


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

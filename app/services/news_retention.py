"""Published news retention: cap public headlines at a rolling window and purge older rows."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app, has_request_context
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.site_models import NewsArticle, NewsArticleComment, NewsArticleVote

DEFAULT_PUBLISHED_NEWS_RETENTION_DAYS = 45

_LAST_PURGE_AT_BY_LEAGUE: dict[str, float] = {}
_PURGE_INTERVAL_SEC = 3600.0


def published_news_cutoff_utc(*, days: int | None = None) -> datetime:
    """UTC datetime: articles with ``published_at`` on or after this are shown publicly."""
    n = int(days if days is not None else DEFAULT_PUBLISHED_NEWS_RETENTION_DAYS)
    n = max(1, min(3650, n))
    return datetime.utcnow() - timedelta(days=n)


def published_news_age_filter(article_model=NewsArticle, *, days: int | None = None):
    """SQLAlchemy filter: published articles within the retention window."""
    cutoff = published_news_cutoff_utc(days=days)
    return (article_model.published_at.is_(None)) | (article_model.published_at >= cutoff)


def _delete_article_image(rel_path: str | None) -> None:
    rel = (rel_path or "").strip().lstrip("/")
    if not rel or not has_request_context():
        return
    static_root = Path(current_app.root_path) / "static"
    path = static_root / rel
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def purge_old_published_news(
    session: Session,
    *,
    league_slug: str,
    days: int | None = None,
) -> dict[str, int]:
    """Permanently delete published articles older than ``days`` and related engagement rows."""
    slug = str(league_slug or "").strip()
    if not slug:
        return {"articles": 0, "comments": 0, "votes": 0}
    cutoff = published_news_cutoff_utc(days=days)
    stale = session.scalars(
        select(NewsArticle).where(
            NewsArticle.league_slug == slug,
            NewsArticle.status == "published",
            NewsArticle.published_at.isnot(None),
            NewsArticle.published_at < cutoff,
        )
    ).all()
    if not stale:
        return {"articles": 0, "comments": 0, "votes": 0}
    ids = [int(a.id) for a in stale]
    for art in stale:
        _delete_article_image(getattr(art, "image_rel_path", None))
    votes_deleted = session.execute(
        delete(NewsArticleVote).where(NewsArticleVote.article_id.in_(ids))
    )
    comments_deleted = session.execute(
        delete(NewsArticleComment).where(NewsArticleComment.article_id.in_(ids))
    )
    articles_deleted = session.execute(delete(NewsArticle).where(NewsArticle.id.in_(ids)))
    session.commit()
    return {
        "articles": int(getattr(articles_deleted, "rowcount", 0) or 0),
        "comments": int(getattr(comments_deleted, "rowcount", 0) or 0),
        "votes": int(getattr(votes_deleted, "rowcount", 0) or 0),
    }


def maybe_purge_old_published_news(session: Session, *, league_slug: str) -> None:
    """Throttled purge (at most once per hour per league) during web requests."""
    slug = str(league_slug or "").strip()
    if not slug:
        return
    now = time.time()
    last = _LAST_PURGE_AT_BY_LEAGUE.get(slug, 0.0)
    if now - last < _PURGE_INTERVAL_SEC:
        return
    try:
        purge_old_published_news(session, league_slug=slug)
        _LAST_PURGE_AT_BY_LEAGUE[slug] = now
    except Exception:
        session.rollback()
        if has_request_context():
            current_app.logger.exception("Published news retention purge failed for %s", slug)

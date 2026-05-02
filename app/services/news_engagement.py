"""Comments and thumbs up/down for Around the League (published news) articles."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth_login import active_membership_for_league
from app.site_models import NewsArticle, NewsArticleComment, NewsArticleVote, User


def viewer_can_react_on_news(viewer: Any, league_slug: str) -> bool:
    if not viewer or not getattr(viewer, "is_authenticated", False):
        return False
    if getattr(viewer, "is_admin", False):
        return True
    return active_membership_for_league(viewer, league_slug) is not None


def _published_article_for_league(
    session: Session, article_id: int, league_slug: str
) -> NewsArticle | None:
    return session.scalar(
        select(NewsArticle).where(
            NewsArticle.id == int(article_id),
            NewsArticle.league_slug == league_slug,
            NewsArticle.status == "published",
        )
    )


def engagement_bundle_for_articles(
    session: Session,
    league_slug: str,
    article_ids: list[int],
    viewer: Any | None,
    *,
    comments_per_article: int = 80,
) -> dict[int, dict[str, Any]]:
    """Counts, recent comments, and current viewer's vote per article id."""
    if not article_ids:
        return {}
    ids = sorted({int(i) for i in article_ids})
    out: dict[int, dict[str, Any]] = {
        i: {"thumbs_up": 0, "thumbs_down": 0, "my_vote": None, "comments": []} for i in ids
    }

    rows = session.execute(
        select(NewsArticleVote.article_id, NewsArticleVote.value, func.count(NewsArticleVote.id))
        .where(NewsArticleVote.article_id.in_(ids))
        .group_by(NewsArticleVote.article_id, NewsArticleVote.value)
    ).all()
    for aid, val, n in rows:
        aid = int(aid)
        if aid not in out:
            continue
        c = int(n or 0)
        if int(val) == 1:
            out[aid]["thumbs_up"] = c
        elif int(val) == -1:
            out[aid]["thumbs_down"] = c

    uid = int(viewer.id) if viewer and getattr(viewer, "is_authenticated", False) else None
    if uid:
        for v in session.scalars(
            select(NewsArticleVote).where(
                NewsArticleVote.article_id.in_(ids),
                NewsArticleVote.user_id == uid,
            )
        ).all():
            out[int(v.article_id)]["my_vote"] = int(v.value)

    lim = max(1, min(int(comments_per_article or 80), 200))
    comments = session.scalars(
        select(NewsArticleComment)
        .options(joinedload(NewsArticleComment.user))
        .where(NewsArticleComment.article_id.in_(ids))
        .order_by(NewsArticleComment.created_at.asc())
    ).all()
    grouped: dict[int, list[NewsArticleComment]] = defaultdict(list)
    for c in comments:
        grouped[int(c.article_id)].append(c)
    from app.services.gm_messaging import gm_display_name

    for aid in ids:
        lst = grouped.get(aid, [])
        if len(lst) > lim:
            lst = lst[-lim:]
        out[aid]["comments"] = [
            {
                "id": c.id,
                "author_label": gm_display_name(c.user),
                "body": (c.body or "").strip(),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in lst
        ]

    return out


def set_article_vote(
    session: Session,
    *,
    league_slug: str,
    article_id: int,
    user_id: int,
    value: int,
) -> dict[str, Any]:
    """value is 1, -1, or 0 (remove vote). Returns thumbs_up, thumbs_down, my_vote."""
    art = _published_article_for_league(session, article_id, league_slug)
    if art is None:
        return {"error": "not_found"}
    v = int(value)
    if v not in (-1, 0, 1):
        return {"error": "bad_value"}
    row = session.scalar(
        select(NewsArticleVote).where(
            NewsArticleVote.article_id == art.id,
            NewsArticleVote.user_id == int(user_id),
        )
    )
    if v == 0:
        if row:
            session.delete(row)
    elif row:
        row.value = v
    else:
        session.add(
            NewsArticleVote(
                article_id=art.id,
                user_id=int(user_id),
                value=v,
                created_at=datetime.utcnow(),
            )
        )
    session.commit()
    thumbs_up = int(
        session.scalar(
            select(func.count(NewsArticleVote.id)).where(
                NewsArticleVote.article_id == art.id,
                NewsArticleVote.value == 1,
            )
        )
        or 0
    )
    thumbs_down = int(
        session.scalar(
            select(func.count(NewsArticleVote.id)).where(
                NewsArticleVote.article_id == art.id,
                NewsArticleVote.value == -1,
            )
        )
        or 0
    )
    my_row = session.scalar(
        select(NewsArticleVote).where(
            NewsArticleVote.article_id == art.id,
            NewsArticleVote.user_id == int(user_id),
        )
    )
    my_vote = int(my_row.value) if my_row else None
    return {
        "ok": True,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "my_vote": my_vote,
    }


def add_article_comment(
    session: Session,
    *,
    league_slug: str,
    article_id: int,
    user_id: int,
    body: str,
) -> dict[str, Any]:
    text = (body or "").strip()
    if not text:
        return {"error": "empty"}
    if len(text) > 2000:
        return {"error": "too_long"}
    art = _published_article_for_league(session, article_id, league_slug)
    if art is None:
        return {"error": "not_found"}
    c = NewsArticleComment(
        article_id=art.id,
        user_id=int(user_id),
        body=text,
        created_at=datetime.utcnow(),
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    u = session.get(User, int(user_id))
    from app.services.gm_messaging import gm_display_name

    return {
        "ok": True,
        "comment": {
            "id": c.id,
            "author_label": gm_display_name(u),
            "body": text,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        },
    }

"""Trade log: optional CSV import plus published site transaction articles."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team, TradeLogEntry
from app.site_models import NewsArticle

_TRADE_TITLE_RE = re.compile(r"^Trade:\s*(.+?)\s*↔\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TradeLogRow:
    """One trade line for templates (league page or team panel)."""

    sort_at: datetime
    trade_date: date | None
    team_a: Team | None
    team_b: Team | None
    title: str
    body: str
    source: str
    article_id: int | None = None


def _team_by_display_label(session: Session, label: str, teams: list[Team]) -> Team | None:
    key = (label or "").strip()
    if not key:
        return None
    for t in teams:
        if (t.full_display_name() or "").strip() == key:
            return t
    low = key.lower()
    for t in teams:
        fn = (t.full_display_name() or "").strip().lower()
        if fn == low:
            return t
    for t in teams:
        if (t.name or "").strip().lower() == low:
            return t
    return None


def _teams_from_trade_title(session: Session, title: str) -> tuple[Team | None, Team | None]:
    m = _TRADE_TITLE_RE.match((title or "").strip())
    if not m:
        return None, None
    teams = session.scalars(select(Team)).all()
    return (
        _team_by_display_label(session, m.group(1), teams),
        _team_by_display_label(session, m.group(2), teams),
    )


def _dedupe_site_transaction_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Commissioner publish creates two articles (one per team); keep one per trade."""
    seen: set[tuple[str, str | None, str]] = set()
    out: list[NewsArticle] = []
    for a in articles:
        pub = a.published_at.isoformat() if a.published_at else ""
        key = (a.title, pub, (a.body or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _row_involves_team(row: TradeLogRow, team_id: int) -> bool:
    if row.team_a and int(row.team_a.id) == team_id:
        return True
    if row.team_b and int(row.team_b.id) == team_id:
        return True
    return False


def trade_log_rows(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    team_id: int | None = None,
    limit: int = 200,
) -> list[TradeLogRow]:
    """Merged trade history newest first."""
    rows: list[TradeLogRow] = []

    for ent in league_session.scalars(
        select(TradeLogEntry).order_by(
            TradeLogEntry.trade_date.desc().nulls_last(), TradeLogEntry.id.desc()
        )
    ).all():
        ta = league_session.get(Team, ent.team_a_id)
        tb = league_session.get(Team, ent.team_b_id)
        sort_at = datetime.combine(ent.trade_date, datetime.min.time()) if ent.trade_date else datetime.min
        title = (
            f"Trade: {ta.full_display_name() if ta else ent.team_a_id} "
            f"↔ {tb.full_display_name() if tb else ent.team_b_id}"
        )
        row = TradeLogRow(
            sort_at=sort_at,
            trade_date=ent.trade_date,
            team_a=ta,
            team_b=tb,
            title=title,
            body=(ent.summary or "").strip(),
            source=ent.source or "csv",
        )
        if team_id is None or _row_involves_team(row, team_id):
            rows.append(row)

    if league_slug:
        articles = site_session.scalars(
            select(NewsArticle)
            .where(
                NewsArticle.league_slug == league_slug,
                NewsArticle.status == "published",
                NewsArticle.category == "transactions",
            )
            .order_by(NewsArticle.published_at.desc().nulls_last(), NewsArticle.id.desc())
            .limit(500)
        ).all()
        for a in _dedupe_site_transaction_articles(list(articles)):
            ta, tb = _teams_from_trade_title(league_session, a.title)
            sort_at = a.published_at or a.created_at or datetime.min
            trade_d = sort_at.date() if isinstance(sort_at, datetime) else None
            row = TradeLogRow(
                sort_at=sort_at if isinstance(sort_at, datetime) else datetime.min,
                trade_date=trade_d,
                team_a=ta,
                team_b=tb,
                title=a.title,
                body=(a.body or "").strip(),
                source="site",
                article_id=int(a.id),
            )
            if team_id is None or _row_involves_team(row, team_id):
                rows.append(row)

    rows.sort(key=lambda r: (r.sort_at, r.article_id or 0), reverse=True)
    cap = max(1, min(500, int(limit)))
    return rows[:cap]

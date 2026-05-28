"""Trade log: Trade Tool publications plus admin-entered historical trades."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team, TradeLogEntry
from app.site_models import NewsArticle
from app.services.franchise_identities import team_identity_for_season

_TRADE_TITLE_RE = re.compile(r"^Trade:\s*(.+?)\s*↔\s*(.+?)\s*$", re.IGNORECASE)


def _trade_team_labels(
    session: Session, team_a: Team | None, team_b: Team | None, trade_d: date | None
) -> tuple[str, str]:
    sy = int(trade_d.year) if trade_d is not None else None
    ia = team_identity_for_season(session, team_a, sy) if sy is not None else None
    ib = team_identity_for_season(session, team_b, sy) if sy is not None else None
    la = ia.display_name if ia else (team_a.full_display_name() if team_a else "—")
    lb = ib.display_name if ib else (team_b.full_display_name() if team_b else "—")
    return la, lb


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
    team_a_label: str | None = None
    team_b_label: str | None = None
    article_id: int | None = None
    entry_id: int | None = None
    log_key: str = ""


def trade_log_source_label(source: str) -> str:
    """Human label for trade log provenance."""
    s = (source or "").strip().lower()
    if s == "manual":
        return "Manual"
    if s == "csv":
        return "CSV import"
    if s == "site":
        return "Trade Tool"
    return (source or "Unknown").strip() or "Unknown"


def trade_log_row_key(*, source: str, entry_id: int | None = None, article_id: int | None = None) -> str:
    s = (source or "").strip().lower()
    if s == "site" and article_id:
        return f"site:{int(article_id)}"
    if entry_id is not None:
        return f"{s}:{int(entry_id)}"
    return ""


def format_recent_trades_for_prompt(rows: list[TradeLogRow], *, limit: int = 12) -> str:
    """Condensed recent-trade block for hypothetical AI prompts."""
    cap = max(1, min(30, int(limit)))
    lines = ["Recent league trades (newest first, for pattern context only):"]
    for row in rows[:cap]:
        when = ""
        if row.trade_date:
            when = row.trade_date.isoformat()
        elif row.sort_at and row.sort_at != datetime.min:
            when = row.sort_at.strftime("%Y-%m-%d")
        ta = row.team_a_label or (row.team_a.full_display_name() if row.team_a else "?")
        tb = row.team_b_label or (row.team_b.full_display_name() if row.team_b else "?")
        body = (row.body or "").strip().replace("\n", " ")[:240]
        src = trade_log_source_label(row.source)
        lines.append(f"  • [{src}] {when} — {ta} ↔ {tb}: {body or row.title}")
    if len(lines) == 1:
        lines.append("  • (none on record)")
    return "\n".join(lines)


def resolve_trade_log_row(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    source: str,
    row_id: int,
) -> TradeLogRow | None:
    """Resolve a trade-log row by ``source`` + id (``entry_id`` or ``article_id``)."""
    src = (source or "").strip().lower()
    rid = int(row_id)
    if src == "manual":
        ent = league_session.get(TradeLogEntry, rid)
        if not ent or (ent.source or "").strip().lower() != src:
            return None
        ta = league_session.get(Team, ent.team_a_id)
        tb = league_session.get(Team, ent.team_b_id)
        sort_at = datetime.combine(ent.trade_date, datetime.min.time()) if ent.trade_date else datetime.min
        trade_d = ent.trade_date
        label_a, label_b = _trade_team_labels(league_session, ta, tb, trade_d)
        title = f"Trade: {label_a if ta else ent.team_a_id} ↔ {label_b if tb else ent.team_b_id}"
        return TradeLogRow(
            sort_at=sort_at,
            trade_date=trade_d,
            team_a=ta,
            team_b=tb,
            title=title,
            body=(ent.summary or "").strip(),
            source=src,
            team_a_label=label_a,
            team_b_label=label_b,
            entry_id=int(ent.id),
            log_key=trade_log_row_key(source=src, entry_id=int(ent.id)),
        )
    if src == "site":
        art = site_session.get(NewsArticle, rid)
        if not art or art.league_slug != league_slug or art.status != "published":
            return None
        if (art.category or "").strip().lower() != "transactions":
            return None
        if not _TRADE_TITLE_RE.match((art.title or "").strip()):
            return None
        ta, tb = _teams_from_trade_title(league_session, art.title)
        sort_at = art.published_at or art.created_at or datetime.min
        trade_d = sort_at.date() if isinstance(sort_at, datetime) else None
        label_a, label_b = _trade_team_labels(league_session, ta, tb, trade_d)
        return TradeLogRow(
            sort_at=sort_at if isinstance(sort_at, datetime) else datetime.min,
            trade_date=trade_d,
            team_a=ta,
            team_b=tb,
            title=art.title,
            body=(art.body or "").strip(),
            source="site",
            team_a_label=label_a,
            team_b_label=label_b,
            article_id=int(art.id),
            log_key=trade_log_row_key(source="site", article_id=int(art.id)),
        )
    return None


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
        # Only Trade Tool articles use the canonical "Trade: Team ↔ Team" headline.
        # Other transaction news, such as waivers, belongs in league headlines only.
        if not _TRADE_TITLE_RE.match((a.title or "").strip()):
            continue
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
    """Trade Tool history plus manual admin entries, newest first."""
    rows: list[TradeLogRow] = []

    for ent in league_session.scalars(
        select(TradeLogEntry).where(TradeLogEntry.source == "manual").order_by(
            TradeLogEntry.trade_date.desc().nulls_last(), TradeLogEntry.id.desc()
        )
    ).all():
        ta = league_session.get(Team, ent.team_a_id)
        tb = league_session.get(Team, ent.team_b_id)
        sort_at = datetime.combine(ent.trade_date, datetime.min.time()) if ent.trade_date else datetime.min
        trade_d = ent.trade_date
        label_a, label_b = _trade_team_labels(league_session, ta, tb, trade_d)
        title = f"Trade: {label_a if ta else ent.team_a_id} ↔ {label_b if tb else ent.team_b_id}"
        src = (ent.source or "csv").strip().lower() or "csv"
        row = TradeLogRow(
            sort_at=sort_at,
            trade_date=trade_d,
            team_a=ta,
            team_b=tb,
            title=title,
            body=(ent.summary or "").strip(),
            source=src,
            team_a_label=label_a,
            team_b_label=label_b,
            entry_id=int(ent.id),
            log_key=trade_log_row_key(source=src, entry_id=int(ent.id)),
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
            label_a, label_b = _trade_team_labels(league_session, ta, tb, trade_d)
            row = TradeLogRow(
                sort_at=sort_at if isinstance(sort_at, datetime) else datetime.min,
                trade_date=trade_d,
                team_a=ta,
                team_b=tb,
                title=a.title,
                body=(a.body or "").strip(),
                source="site",
                team_a_label=label_a,
                team_b_label=label_b,
                article_id=int(a.id),
                log_key=trade_log_row_key(source="site", article_id=int(a.id)),
            )
            if team_id is None or _row_involves_team(row, team_id):
                rows.append(row)

    rows.sort(key=lambda r: (r.sort_at, r.article_id or 0), reverse=True)
    cap = max(1, min(500, int(limit)))
    return rows[:cap]

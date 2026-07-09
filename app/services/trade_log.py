"""Trade log: Trade Tool publications plus admin-entered historical trades."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Season, Team, TradeLogEntry
from app.site_models import NewsArticle
from app.services.franchise_identities import team_identity_for_season

_TRADE_TITLE_RE = re.compile(r"^Trade:\s*(.+?)\s*↔\s*(.+?)\s*$", re.IGNORECASE)
_ERA_YEAR_IN_TEXT_RE = re.compile(r"(?<!\d)((?:19|20|21)\d{2})(?!\d)")


def current_league_season_start_year(session: Session) -> int | None:
    season = session.scalar(
        select(Season)
        .where(Season.start_year.is_not(None))
        .order_by(Season.is_current.desc(), Season.start_year.desc(), Season.id.desc())
        .limit(1)
    )
    return int(season.start_year) if season and season.start_year is not None else None


def trade_log_era_start_year(
    session: Session,
    *,
    source: str,
    trade_date: date | None,
    body: str = "",
) -> int | None:
    """Sim season year for era-correct franchise names and logos (not real-world publish dates)."""
    src = (source or "").strip().lower()
    league_year = current_league_season_start_year(session)

    if src == "manual":
        cutoff = int(trade_date.year) if trade_date is not None else None
        years = [int(m.group(1)) for m in _ERA_YEAR_IN_TEXT_RE.finditer(body or "")]
        if cutoff is not None:
            viable = [y for y in years if 1900 <= y <= cutoff]
        else:
            viable = [y for y in years if 1900 <= y <= 2100]
        if viable:
            return min(viable)
        if trade_date is not None:
            return int(trade_date.year)
        return league_year

    if src == "site":
        return league_year

    if trade_date is not None:
        yr = int(trade_date.year)
        if league_year is not None and yr > league_year + 1:
            return league_year
        return yr
    return league_year


def _trade_team_labels(
    session: Session, team_a: Team | None, team_b: Team | None, era_year: int | None
) -> tuple[str, str]:
    sy = int(era_year) if era_year is not None else None
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
    era_start_year: int | None = None
    article_id: int | None = None
    entry_id: int | None = None
    log_key: str = ""


@dataclass(frozen=True)
class TradeLogCardSide:
    team_label: str
    acquired: tuple[str, ...]


@dataclass(frozen=True)
class TradeLogCardView:
    team_a: TradeLogCardSide
    team_b: TradeLogCardSide
    supplemental: str
    fallback_body: str


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


def _clean_trade_asset_line(line: str) -> str:
    text = (line or "").strip()
    for prefix in ("•", "-", "*"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def _parse_sent_block(block: str) -> tuple[str, tuple[str, ...]] | None:
    lines = [line.rstrip() for line in (block or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) < 2:
        return None
    heading = lines[0].strip()
    heading_lower = heading.lower()
    if heading_lower.endswith(" sends:"):
        label = heading[: -len(" sends:")].strip()
    elif " sends to " in heading_lower and heading.endswith(":"):
        marker_at = heading_lower.index(" sends to ")
        label = heading[:marker_at].strip()
    else:
        return None
    items = tuple(
        item
        for item in (_clean_trade_asset_line(line) for line in lines[1:])
        if item and item.lower() != "(none)"
    )
    return label, items


def trade_log_card_view(row: TradeLogRow) -> TradeLogCardView:
    """Split trade text into FHM-style acquired columns plus optional condition banner."""
    label_a = row.team_a_label or (row.team_a.full_display_name() if row.team_a else "Team A")
    label_b = row.team_b_label or (row.team_b.full_display_name() if row.team_b else "Team B")
    body = (row.body or "").strip()
    if not body:
        return TradeLogCardView(
            team_a=TradeLogCardSide(label_a, ()),
            team_b=TradeLogCardSide(label_b, ()),
            supplemental="",
            fallback_body="",
        )

    paragraphs = re.split(r"\n\s*\n", body)
    left_sent = _parse_sent_block(paragraphs[0]) if len(paragraphs) >= 1 else None
    right_sent = _parse_sent_block(paragraphs[1]) if len(paragraphs) >= 2 else None
    if left_sent and right_sent:
        supplemental = "\n\n".join(p.strip() for p in paragraphs[2:] if p.strip())
        return TradeLogCardView(
            team_a=TradeLogCardSide(left_sent[0] or label_a, right_sent[1]),
            team_b=TradeLogCardSide(right_sent[0] or label_b, left_sent[1]),
            supplemental=supplemental,
            fallback_body="",
        )

    return TradeLogCardView(
        team_a=TradeLogCardSide(label_a, ()),
        team_b=TradeLogCardSide(label_b, ()),
        supplemental="",
        fallback_body=body,
    )


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
    if src == "manual" or src == "csv":
        ent = league_session.get(TradeLogEntry, rid)
        if not ent or (ent.source or "").strip().lower() != src:
            return None
        ta = league_session.get(Team, ent.team_a_id)
        tb = league_session.get(Team, ent.team_b_id)
        sort_at = datetime.combine(ent.trade_date, datetime.min.time()) if ent.trade_date else datetime.min
        trade_d = ent.trade_date
        src = (ent.source or "manual").strip().lower() or "manual"
        era_y = trade_log_era_start_year(
            league_session, source=src, trade_date=trade_d, body=(ent.summary or "").strip()
        )
        label_a, label_b = _trade_team_labels(league_session, ta, tb, era_y)
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
            era_start_year=era_y,
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
        body = (art.body or "").strip()
        era_y = trade_log_era_start_year(league_session, source="site", trade_date=trade_d, body=body)
        label_a, label_b = _trade_team_labels(league_session, ta, tb, era_y)
        return TradeLogRow(
            sort_at=sort_at if isinstance(sort_at, datetime) else datetime.min,
            trade_date=trade_d,
            team_a=ta,
            team_b=tb,
            title=art.title,
            body=body,
            source="site",
            team_a_label=label_a,
            team_b_label=label_b,
            era_start_year=era_y,
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
        select(TradeLogEntry).where(TradeLogEntry.source.in_(("manual", "csv"))).order_by(
            TradeLogEntry.trade_date.desc().nulls_last(), TradeLogEntry.id.desc()
        )
    ).all():
        ta = league_session.get(Team, ent.team_a_id)
        tb = league_session.get(Team, ent.team_b_id)
        sort_at = datetime.combine(ent.trade_date, datetime.min.time()) if ent.trade_date else datetime.min
        trade_d = ent.trade_date
        src = (ent.source or "csv").strip().lower() or "csv"
        body = (ent.summary or "").strip()
        era_y = trade_log_era_start_year(league_session, source=src, trade_date=trade_d, body=body)
        label_a, label_b = _trade_team_labels(league_session, ta, tb, era_y)
        title = f"Trade: {label_a if ta else ent.team_a_id} ↔ {label_b if tb else ent.team_b_id}"
        row = TradeLogRow(
            sort_at=sort_at,
            trade_date=trade_d,
            team_a=ta,
            team_b=tb,
            title=title,
            body=body,
            source=src,
            team_a_label=label_a,
            team_b_label=label_b,
            era_start_year=era_y,
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
            .order_by(NewsArticle.published_at.desc(), NewsArticle.id.desc())
            .limit(500)
        ).all()
        for a in _dedupe_site_transaction_articles(list(articles)):
            ta, tb = _teams_from_trade_title(league_session, a.title)
            sort_at = a.published_at or a.created_at or datetime.min
            trade_d = sort_at.date() if isinstance(sort_at, datetime) else None
            body = (a.body or "").strip()
            era_y = trade_log_era_start_year(league_session, source="site", trade_date=trade_d, body=body)
            label_a, label_b = _trade_team_labels(league_session, ta, tb, era_y)
            row = TradeLogRow(
                sort_at=sort_at if isinstance(sort_at, datetime) else datetime.min,
                trade_date=trade_d,
                team_a=ta,
                team_b=tb,
                title=a.title,
                body=body,
                source="site",
                team_a_label=label_a,
                team_b_label=label_b,
                era_start_year=era_y,
                article_id=int(a.id),
                log_key=trade_log_row_key(source="site", article_id=int(a.id)),
            )
            if team_id is None or _row_involves_team(row, team_id):
                rows.append(row)

    rows.sort(key=lambda r: (r.sort_at, r.article_id or 0), reverse=True)
    cap = max(1, min(500, int(limit)))
    return rows[:cap]

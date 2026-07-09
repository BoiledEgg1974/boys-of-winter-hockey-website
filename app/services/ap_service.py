"""Action points: ledger balance, catalog seed, redemption approval."""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from math import ceil
from typing import Any

from sqlalchemy import and_, func, select

from app.config import league_group_for_slug
from app.league_db import db
from app.sqlite_retry import commit_with_sqlite_retry, write_with_sqlite_retry
from app.models import Team
from app.site_models import ApLedgerEntry, ApRedemptionCatalog, ApRedemptionRequest, NewsArticle, User


def load_ap_redemption_parties(
    session,
    rows: list[ApRedemptionRequest],
) -> tuple[dict[int, Team], dict[int, User]]:
    """Batch-load teams and submitting users for redemption request rows."""
    team_ids = {int(r.team_id) for r in rows if r.team_id}
    user_ids = {int(r.user_id) for r in rows if r.user_id}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        teams_by_id = {
            t.id: t for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        }
    users_by_id: dict[int, User] = {}
    if user_ids:
        users_by_id = {
            u.id: u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()
        }
    return teams_by_id, users_by_id


def ap_redemption_party_display(
    req: ApRedemptionRequest,
    *,
    team: Team | None,
    user: User | None,
) -> dict[str, str]:
    """Human-readable GM and team labels for admin redemption views."""
    from app.services.gm_messaging import gm_display_name

    team_name = team.full_display_name() if team else f"Team {int(req.team_id)}"
    if user is not None:
        gm_name = gm_display_name(user)
    else:
        gm_name = f"User #{int(req.user_id)}"
    return {
        "gm_name": gm_name,
        "team_name": team_name,
        "gm_email": str(getattr(user, "email", "") or "").strip(),
    }


def parse_redemption_line_labels(lines_json: str) -> list[str]:
    """Display strings for each catalog line on a redemption request."""
    titles: list[str] = []
    try:
        from app.services.ap_redemption_forms import line_item_display_title

        items = json.loads(lines_json or "[]")
        if not isinstance(items, list):
            return titles
        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            cost = it.get("cost")
            details = it.get("details")
            label = line_item_display_title(
                title, details if isinstance(details, dict) else None
            )
            if cost is None:
                titles.append(label)
            else:
                titles.append(f"{label} ({cost} AP)")
    except Exception:
        pass
    return titles


def team_ap_balance(league_slug: str, team_id: int) -> int:
    """Sum of ledger deltas for this team."""
    with db.session.no_autoflush:
        total = db.session.scalar(
            select(func.coalesce(func.sum(ApLedgerEntry.delta), 0)).where(
                ApLedgerEntry.league_slug == league_slug,
                ApLedgerEntry.team_id == team_id,
            )
        )
    return int(total or 0)


def add_ledger_entry(
    *,
    league_slug: str,
    team_id: int,
    delta: int,
    reason_code: str,
    meta: dict[str, Any] | None = None,
    created_by_user_id: int | None = None,
    source_ref: str | None = None,
) -> ApLedgerEntry | None:
    """Insert ledger row. If source_ref is set and already exists, returns None (idempotent)."""
    if source_ref:
        with db.session.no_autoflush:
            existing = db.session.scalar(
                select(ApLedgerEntry.id).where(ApLedgerEntry.source_ref == source_ref).limit(1)
            )
        if existing is not None:
            return None
    row = ApLedgerEntry(
        league_slug=league_slug,
        team_id=team_id,
        delta=delta,
        reason_code=reason_code,
        meta_json=json.dumps(meta or {}),
        created_by_user_id=created_by_user_id,
        source_ref=source_ref,
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    return row


def active_redemption_items(league_slug: str) -> list[ApRedemptionCatalog]:
    group = league_group_for_slug(league_slug)
    return list(
        db.session.scalars(
            select(ApRedemptionCatalog)
            .where(
                ApRedemptionCatalog.league_group == group,
                ApRedemptionCatalog.is_active.is_(True),
            )
            .order_by(ApRedemptionCatalog.cost_ap, ApRedemptionCatalog.sort_order, ApRedemptionCatalog.id)
        ).all()
    )


def _reconcile_ap_catalog_defaults() -> None:
    """Normalize catalog text/costs and retire deprecated items across all leagues."""
    rows = list(db.session.scalars(select(ApRedemptionCatalog)).all())
    if not rows:
        return
    changed = False
    remove_titles = {"Premium Perk (55 AP)", "Waive NTC / Silver Draft Boost"}
    for row in rows:
        if row.title in remove_titles:
            db.session.delete(row)
            changed = True
            continue
        if row.title in {"Financial Starting Points +15%", "Financial Boost"}:
            if int(row.cost_ap or 0) != 15:
                row.cost_ap = 15
                changed = True
            if (row.description or "").strip() != "Stackable":
                row.description = "Stackable"
                changed = True
        if row.title == "Market / Fan / Media +1":
            txt = "Increase Market Size, Fan Loyalty, Media Coverage, Base Media Attitude by 1 Setting"
            if (row.description or "").strip() != txt:
                row.description = txt
                changed = True
        if row.title in {"Supplemental Staff Hiring", "Supplemental Staff"}:
            txt = "+1 Supplemental Staff Hiring above the free signing per sim"
            if (row.description or "").strip() != txt:
                row.description = txt
                changed = True
    if changed:
        commit_with_sqlite_retry(db.session)


def seed_ap_catalog_if_empty() -> None:
    """Insert starter redemption rows when catalog is empty, then reconcile defaults."""
    n = db.session.scalar(select(func.count()).select_from(ApRedemptionCatalog))
    if not (n and int(n) > 0):
        cap_rows = [
            (0, "Change a Rival", "League perk — adjust rival designation.", 5),
            (1, "Retire a Number", "Retire a jersey number for your franchise.", 5),
            (2, "Change Goal Horn", "Customize goal horn.", 10),
            (3, "Change Rink", "Name, look, or branding of your rink.", 10),
            (4, "Change Staff Name", "Rename a staff member.", 10),
            (5, "Change Jersey / Logo", "Visual identity update.", 10),
            (6, "Supplemental Staff Hiring", "+1 Supplemental Staff Hiring above the free signing per sim", 15),
            (7, "Financial Starting Points +15%", "Stackable", 15),
            (8, "Market / Fan / Media +1", "Increase Market Size, Fan Loyalty, Media Coverage, Base Media Attitude by 1 Setting", 30),
            (9, "Division Draft Veto", "Veto being drafted to a division.", 35),
        ]
        for order, title, desc, cost in cap_rows:
            db.session.add(
                ApRedemptionCatalog(
                    league_group="cap_historical",
                    sort_order=order,
                    title=title,
                    description=desc,
                    cost_ap=cost,
                    is_active=True,
                )
            )
        fantasy_rows = [
            (0, "Change a Rival", "Relegation league — adjust rival.", 5),
            (1, "Change Goal Horn", "Customize goal horn.", 10),
            (2, "Change Jersey / Logo", "Visual identity update.", 10),
            (3, "Supplemental Staff", "+1 Supplemental Staff Hiring above the free signing per sim", 15),
            (4, "Financial Boost", "Stackable", 15),
            (5, "Development / Market Package", "League-approved attribute or market tweak.", 30),
            (6, "Major Customization", "Premium Relegation perk — confirm with commissioner.", 55),
        ]
        for order, title, desc, cost in fantasy_rows:
            db.session.add(
                ApRedemptionCatalog(
                    league_group="fantasy",
                    sort_order=order,
                    title=title,
                    description=desc,
                    cost_ap=cost,
                    is_active=True,
                )
            )
        commit_with_sqlite_retry(db.session)
    _reconcile_ap_catalog_defaults()
    _reconcile_fantasy_ap_catalog()


def _normalize_catalog_title(title: str) -> str:
    return " ".join(str(title or "").strip().lower().split())


# (sort_order, title, description, cost_ap) — added when missing from Relegation catalog.
_FANTASY_CATALOG_DEFAULTS: tuple[tuple[int, str, str, int], ...] = (
    (0, "Change a Rival", "Designate a league rival team.", 5),
    (1, "Retire a Number", "Retire a jersey number for your franchise.", 5),
    (2, "Supplemental Staff Hiring", "+1 supplemental staff hire above the free signing per sim.", 15),
    (3, "Market / Fan / Media +1", "Increase Market, Fan Loyalty, or Media Coverage by 1.", 30),
    (4, "Change Injury Proneness", "Adjust injury proneness for a body part or general.", 55),
    (5, "Re-Allocate 1 Point from Any Attribute", "Move one attribute point to another.", 55),
    (6, "Add 2 Points to a Position", "Add 2 points to a skater position rating.", 55),
    (7, "Add 2 Points to Coach's Attribute", "Add 2 points to a GM or coach attribute.", 55),
    (8, "Purchase a Silver Boost for one of your Draftees.", "Silver draft boost for one draftee.", 200),
    (9, "Purchase a Gold Boost for one of your Draftees.", "Gold draft boost for one draftee.", 300),
    (10, "Relocate Your Team", "Move your franchise — commissioner completes after approval.", 100),
    (11, "Retire Your Created Player", "Retire a created player from your roster.", 55),
    (12, "Reclassify Your Created Player", "Change created player position (FROM → TO).", 55),
    (13, "Create a 3-Star Potential Player", "Commissioner creates a 3-star potential player.", 300),
    (14, "Create a 4-Star Potential Player", "Commissioner creates a 4-star potential player.", 400),
    (15, "Create a 5-Star Potential Player", "Commissioner creates a 5-star potential player.", 500),
)


def _reconcile_fantasy_ap_catalog() -> None:
    """Ensure Relegation redemption catalog includes standard perks (by title)."""
    existing = list(
        db.session.scalars(
            select(ApRedemptionCatalog).where(ApRedemptionCatalog.league_group == "fantasy")
        ).all()
    )
    by_title = {_normalize_catalog_title(r.title): r for r in existing}
    changed = False
    legacy_descriptions = {
        "change a rival": "Relegation league — adjust rival.",
        "major customization": "Premium Relegation perk — confirm with commissioner.",
    }
    for key, desc in legacy_descriptions.items():
        row = by_title.get(key)
        if row is not None and row.description != desc:
            row.description = desc
            changed = True
    for order, title, desc, cost in _FANTASY_CATALOG_DEFAULTS:
        key = _normalize_catalog_title(title)
        row = by_title.get(key)
        if row is None:
            db.session.add(
                ApRedemptionCatalog(
                    league_group="fantasy",
                    sort_order=order,
                    title=title,
                    description=desc,
                    cost_ap=cost,
                    is_active=True,
                )
            )
            changed = True
    if changed:
        commit_with_sqlite_retry(db.session)


def maybe_credit_daily_export_for_team(
    league_slug: str,
    team_id: int,
    *,
    raw_import_dir_mtime: float | None = None,
) -> bool:
    """
    If raw import data looks fresh (mtime), credit +1 AP once per UTC calendar day per team.
    Call from import CLI or scheduled task. Returns True if a new row was inserted.
    """
    if raw_import_dir_mtime is None:
        return False
    day_key = datetime.utcnow().date().isoformat()
    source_ref = f"daily_export:{league_slug}:{team_id}:{day_key}"
    row = add_ledger_entry(
        league_slug=league_slug,
        team_id=team_id,
        delta=1,
        reason_code="daily_export",
        meta={"day": day_key},
        source_ref=source_ref,
    )
    if row is None:
        return False
    commit_with_sqlite_retry(db.session)
    return True


def approve_redemption_request(req: ApRedemptionRequest, admin_user_id: int) -> bool:
    """Deduct AP if still affordable; mark approved. Returns False if balance insufficient."""
    req_id = int(req.id)
    admin_id = int(admin_user_id)

    def _approve() -> bool:
        row = db.session.get(ApRedemptionRequest, req_id)
        if row is None or row.status != "pending":
            return False
        bal = team_ap_balance(row.league_slug, int(row.team_id))
        if bal < int(row.total_cost):
            return False
        add_ledger_entry(
            league_slug=row.league_slug,
            team_id=int(row.team_id),
            delta=-int(row.total_cost),
            reason_code="redemption",
            meta={"request_id": row.id, "lines": json.loads(row.lines_json or "[]")},
            created_by_user_id=admin_id,
        )
        row.status = "approved"
        row.processed_at = datetime.utcnow()
        return True

    return bool(write_with_sqlite_retry(db.session, _approve))


def publish_news_and_maybe_award_ap(article: NewsArticle, *, points: int) -> None:
    """Set published, insert AP ledger once if configured points > 0."""
    article_id = int(article.id)
    award_points = int(points)

    def _publish() -> None:
        from app.services.discord_events import resolve_news_article_team

        art = db.session.get(NewsArticle, article_id)
        if art is None:
            return
        art.status = "published"
        art.published_at = datetime.utcnow()
        team = resolve_news_article_team(db.session, art)
        award_team_id = int(team.id) if team is not None else art.team_id
        if team is not None and art.team_id != int(team.id):
            art.team_id = int(team.id)
        if award_points > 0 and award_team_id is not None and not art.ap_awarded:
            add_ledger_entry(
                league_slug=art.league_slug,
                team_id=int(award_team_id),
                delta=award_points,
                reason_code="news_article",
                meta={"article_id": art.id},
                created_by_user_id=art.author_user_id,
                source_ref=f"news_ap:{art.id}",
            )
            art.ap_awarded = True

    write_with_sqlite_retry(db.session, _publish)


def new_redemption_token() -> str:
    return secrets.token_urlsafe(32)


LEDGER_KIND_EARNED = "earned"
LEDGER_KIND_PENALIZED = "penalized"
LEDGER_KIND_REDEEMED = "redeemed"
LEDGER_PER_PAGE = 50
LEDGER_KINDS = frozenset({LEDGER_KIND_EARNED, LEDGER_KIND_PENALIZED, LEDGER_KIND_REDEEMED})

_REASON_LABELS: dict[str, str] = {
    "manual": "Manual adjustment",
    "batch_all_star": "All-Star",
    "batch_skills": "Skills competition",
    "batch_award": "Award",
    "batch_predictions": "Playoff prediction",
    "batch_penalties": "Penalty",
    "daily_export": "Daily export",
    "news_article": "News article",
    "redemption": "Redemption",
    "bowl_six_slate_prize": "BOWL Six prize",
    "bowl_six_slate_prize_reversal": "BOWL Six prize reversal",
}


def ledger_entry_kind(delta: int, reason_code: str) -> str:
    if reason_code == "redemption":
        return LEDGER_KIND_REDEEMED
    if delta > 0:
        return LEDGER_KIND_EARNED
    return LEDGER_KIND_PENALIZED


def ledger_kind_sql_filter(kind: str | None):
    """SQLAlchemy clause for earned / penalized / redeemed filters."""
    if kind == LEDGER_KIND_EARNED:
        return ApLedgerEntry.delta > 0
    if kind == LEDGER_KIND_REDEEMED:
        return ApLedgerEntry.reason_code == "redemption"
    if kind == LEDGER_KIND_PENALIZED:
        return and_(ApLedgerEntry.delta < 0, ApLedgerEntry.reason_code != "redemption")
    return None


def parse_ledger_list_params(
    raw,
    *,
    locked_team_id: int | None = None,
) -> tuple[int, int | None, str | None]:
    """Parse ledger_page, ledger_team, and ledger_kind from query args."""
    try:
        page = max(1, int((raw.get("ledger_page") if raw else None) or 1))
    except (TypeError, ValueError):
        page = 1
    kind = str((raw.get("ledger_kind") if raw else None) or "").strip().lower()
    if kind not in LEDGER_KINDS:
        kind = None
    if locked_team_id is not None:
        return page, int(locked_team_id), kind
    team_id: int | None = None
    raw_tid = str((raw.get("ledger_team") if raw else None) or "").strip()
    if raw_tid.isdigit():
        team_id = int(raw_tid)
    return page, team_id, kind


def _shape_ledger_rows(entries: list[ApLedgerEntry]) -> list[dict[str, Any]]:
    from app.site_models import meta_dict

    team_ids = {int(e.team_id) for e in entries}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        teams_by_id = {
            t.id: t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        }
    rows: list[dict[str, Any]] = []
    for entry in entries:
        meta = meta_dict(entry)
        delta = int(entry.delta)
        reason_code = str(entry.reason_code or "")
        rows.append(
            {
                "entry": entry,
                "team": teams_by_id.get(int(entry.team_id)),
                "delta": delta,
                "kind": ledger_entry_kind(delta, reason_code),
                "description": ledger_entry_description(reason_code, meta),
                "reason_code": reason_code,
            }
        )
    return rows


def league_ledger_page(
    league_slug: str,
    *,
    page: int = 1,
    per_page: int = LEDGER_PER_PAGE,
    team_id: int | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Paginated ledger rows for a league, shaped for AP page templates."""
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or LEDGER_PER_PAGE))
    clauses = [ApLedgerEntry.league_slug == league_slug]
    if team_id is not None:
        clauses.append(ApLedgerEntry.team_id == int(team_id))
    kind_clause = ledger_kind_sql_filter(kind)
    if kind_clause is not None:
        clauses.append(kind_clause)
    where = and_(*clauses)
    total_count = int(
        db.session.scalar(select(func.count()).select_from(ApLedgerEntry).where(where)) or 0
    )
    total_pages = max(1, ceil(total_count / per_page)) if total_count else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    entries = list(
        db.session.scalars(
            select(ApLedgerEntry)
            .where(where)
            .order_by(ApLedgerEntry.created_at.desc(), ApLedgerEntry.id.desc())
            .offset(offset)
            .limit(per_page)
        ).all()
    )
    return {
        "rows": _shape_ledger_rows(entries),
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "per_page": per_page,
        "team_id": team_id,
        "kind": kind,
    }


def league_ledger_display_rows(
    league_slug: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Recent ledger rows (unpaginated) — prefer league_ledger_page for UI."""
    result = league_ledger_page(league_slug, page=1, per_page=max(1, int(limit)))
    return result["rows"]


def ledger_entry_description(reason_code: str, meta: dict[str, Any]) -> str:
    note = str(meta.get("note") or "").strip()
    if note:
        return note
    batch = str(meta.get("batch") or "").strip()
    if batch:
        return batch
    label = _REASON_LABELS.get(reason_code, reason_code.replace("_", " ").title())
    if reason_code == "news_article" and meta.get("article_id"):
        return f"{label} #{meta['article_id']}"
    if reason_code == "redemption":
        lines = meta.get("lines")
        if isinstance(lines, list) and lines:
            from app.services.ap_redemption_forms import line_item_display_title

            parts: list[str] = []
            for it in lines:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or "").strip()
                if not title:
                    continue
                details = it.get("details")
                parts.append(
                    line_item_display_title(
                        title, details if isinstance(details, dict) else None
                    )
                )
            if parts:
                return "; ".join(parts)
        if meta.get("request_id"):
            return f"Redemption request #{meta['request_id']}"
    if reason_code == "daily_export" and meta.get("day"):
        return f"Export on {meta['day']}"
    return label


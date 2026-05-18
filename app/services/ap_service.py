"""Action points: ledger balance, catalog seed, redemption approval."""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import league_group_for_slug
from app.league_db import db
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
        db.session.commit()


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
            (0, "Change a Rival", "Fantasy league — adjust rival.", 5),
            (1, "Change Goal Horn", "Customize goal horn.", 10),
            (2, "Change Jersey / Logo", "Visual identity update.", 10),
            (3, "Supplemental Staff", "+1 Supplemental Staff Hiring above the free signing per sim", 15),
            (4, "Financial Boost", "Stackable", 15),
            (5, "Development / Market Package", "League-approved attribute or market tweak.", 30),
            (6, "Major Customization", "Premium fantasy perk — confirm with commissioner.", 55),
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
        db.session.commit()
    _reconcile_ap_catalog_defaults()
    _reconcile_fantasy_ap_catalog()


def _normalize_catalog_title(title: str) -> str:
    return " ".join(str(title or "").strip().lower().split())


# (sort_order, title, description, cost_ap) — added when missing from fantasy catalog.
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
    """Ensure fantasy redemption catalog includes standard perks (by title)."""
    existing = list(
        db.session.scalars(
            select(ApRedemptionCatalog).where(ApRedemptionCatalog.league_group == "fantasy")
        ).all()
    )
    by_title = {_normalize_catalog_title(r.title): r for r in existing}
    changed = False
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
        db.session.commit()


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
    day_key = date.utcnow().isoformat()
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
    db.session.commit()
    return True


def approve_redemption_request(req: ApRedemptionRequest, admin_user_id: int) -> bool:
    """Deduct AP if still affordable; mark approved. Returns False if balance insufficient."""
    bal = team_ap_balance(req.league_slug, req.team_id)
    if bal < req.total_cost:
        return False
    add_ledger_entry(
        league_slug=req.league_slug,
        team_id=req.team_id,
        delta=-int(req.total_cost),
        reason_code="redemption",
        meta={"request_id": req.id, "lines": json.loads(req.lines_json or "[]")},
        created_by_user_id=admin_user_id,
    )
    req.status = "approved"
    req.processed_at = datetime.utcnow()
    db.session.commit()
    return True


def publish_news_and_maybe_award_ap(article: NewsArticle, *, points: int) -> None:
    """Set published, insert AP ledger once if configured points > 0."""
    article.status = "published"
    article.published_at = datetime.utcnow()
    if points > 0 and article.team_id is not None and not article.ap_awarded:
        add_ledger_entry(
            league_slug=article.league_slug,
            team_id=int(article.team_id),
            delta=points,
            reason_code="news_article",
            meta={"article_id": article.id},
            created_by_user_id=article.author_user_id,
            source_ref=f"news_ap:{article.id}",
        )
        article.ap_awarded = True
    db.session.commit()


def new_redemption_token() -> str:
    return secrets.token_urlsafe(32)

"""Action points: ledger balance, catalog seed, redemption approval."""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import league_group_for_slug
from app.league_db import db
from app.site_models import ApLedgerEntry, ApRedemptionCatalog, ApRedemptionRequest, NewsArticle


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

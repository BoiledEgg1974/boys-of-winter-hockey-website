"""Import and query draft-pick ownership from draft_pick_ownership.csv (site DB)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Team
from app.site_models import TradeMarketDraftPickOwnership
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

DRAFT_PICK_DRAG_PREFIX = "dpick"
DRAFT_PICK_CSV_NAME = "draft_pick_ownership.csv"

_ROUND_COLUMN_PATTERN = re.compile(r"^(\d+)(?:st|nd|rd|th)?[\s_]*round$", re.I)


def draft_pick_drag_key(row_id: int) -> str:
    return f"{DRAFT_PICK_DRAG_PREFIX}:{int(row_id)}"


def parse_draft_pick_drag_key(key: str) -> int | None:
    if not str(key or "").startswith(f"{DRAFT_PICK_DRAG_PREFIX}:"):
        return None
    try:
        return int(str(key).split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _round_from_column(header: str) -> int | None:
    h = str(header or "").strip().replace("_", " ")
    if not h:
        return None
    match = _ROUND_COLUMN_PATTERN.match(h)
    if not match:
        return None
    rnd = to_int(match.group(1))
    return rnd if rnd and rnd > 0 else None


def fhm_team_id_to_db_id(league_session: Session) -> dict[int, int]:
    out: dict[int, int] = {}
    for tm in league_session.scalars(select(Team)).all():
        raw = str(getattr(tm, "fhm_team_id", None) or "").strip()
        if raw.isdigit():
            out[int(raw)] = int(tm.id)
    return out


def _team_label(tm: Team | None, fhm_id: int) -> str:
    if tm is not None:
        abbr = (tm.abbreviation or "").strip()
        if abbr:
            return abbr
        return tm.full_display_name()
    return f"Team {fhm_id}"


def describe_draft_pick_row(
    row: TradeMarketDraftPickOwnership,
    *,
    original_team: Team | None = None,
    owner_team: Team | None = None,
) -> str:
    orig = _team_label(original_team, int(row.original_team_fhm_id))
    owner = _team_label(owner_team, int(row.owner_team_fhm_id))
    rnd = int(row.round)
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(rnd, "th")
    year = int(row.draft_year)
    if int(row.original_team_fhm_id) == int(row.owner_team_fhm_id):
        return f"{year} {owner} {rnd}{suffix} Round pick"
    return f"{year} {rnd}{suffix} Round ({orig}) — held by {owner}"


def import_draft_pick_ownership_csv(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    raw_dir: Path | None,
) -> int:
    """Replace all ownership rows for *league_slug* from CSV. Returns rows imported."""
    slug = str(league_slug or "").strip()
    if not slug or raw_dir is None:
        return 0
    path = Path(raw_dir) / DRAFT_PICK_CSV_NAME
    if not path.is_file():
        return 0
    df = read_csv_normalized(path)
    if df.empty:
        return 0
    col_rounds: list[tuple[int, str]] = []
    for col in df.columns:
        rnd = _round_from_column(str(col))
        if rnd is not None:
            col_rounds.append((rnd, str(col)))
    if not col_rounds:
        return 0
    fhm_map = fhm_team_id_to_db_id(league_session)
    site_session.execute(
        delete(TradeMarketDraftPickOwnership).where(
            TradeMarketDraftPickOwnership.league_slug == slug
        )
    )
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        year = to_int(cell_val(r, "year"))
        orig_fhm = to_int(cell_val(r, "team id", "team_id", "teamid"))
        if year is None or orig_fhm is None:
            continue
        for rnd, col_name in col_rounds:
            owner_fhm = to_int(cell_val(r, col_name))
            if owner_fhm is None:
                continue
            site_session.add(
                TradeMarketDraftPickOwnership(
                    league_slug=slug,
                    draft_year=int(year),
                    original_team_fhm_id=int(orig_fhm),
                    original_team_id=fhm_map.get(int(orig_fhm)),
                    round=int(rnd),
                    owner_team_fhm_id=int(owner_fhm),
                    owner_team_id=fhm_map.get(int(owner_fhm)),
                )
            )
            n += 1
    site_session.commit()
    return n


def owned_draft_picks_for_team(
    site_session: Session,
    *,
    league_slug: str,
    team_id: int,
) -> list[TradeMarketDraftPickOwnership]:
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    return list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership)
            .where(
                TradeMarketDraftPickOwnership.league_slug == slug,
                TradeMarketDraftPickOwnership.owner_team_id == int(team_id),
            )
            .order_by(
                TradeMarketDraftPickOwnership.draft_year.asc(),
                TradeMarketDraftPickOwnership.round.asc(),
                TradeMarketDraftPickOwnership.original_team_fhm_id.asc(),
            )
        ).all()
    )


def draft_pick_ownership_exists(site_session: Session, *, league_slug: str) -> bool:
    """Return True once draft_pick_ownership.csv has been imported for a league."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    row_id = site_session.scalar(
        select(TradeMarketDraftPickOwnership.id)
        .where(TradeMarketDraftPickOwnership.league_slug == slug)
        .limit(1)
    )
    return row_id is not None


def owned_draft_pick_drag_keys(
    site_session: Session, *, league_slug: str, team_id: int
) -> set[str]:
    return {draft_pick_drag_key(r.id) for r in owned_draft_picks_for_team(
        site_session, league_slug=league_slug, team_id=team_id
    )}


def draft_pick_asset_dicts(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    team_id: int,
) -> list[dict[str, Any]]:
    rows = owned_draft_picks_for_team(
        site_session, league_slug=league_slug, team_id=team_id
    )
    team_cache: dict[int, Team | None] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        oid = int(row.original_team_fhm_id)
        owid = int(row.owner_team_fhm_id)
        if oid not in team_cache:
            team_cache[oid] = (
                league_session.get(Team, int(row.original_team_id))
                if row.original_team_id
                else None
            )
        if owid not in team_cache:
            team_cache[owid] = (
                league_session.get(Team, int(row.owner_team_id)) if row.owner_team_id else None
            )
        label = describe_draft_pick_row(
            row,
            original_team=team_cache.get(oid),
            owner_team=team_cache.get(owid),
        )
        out.append(
            {
                "kind": "draft_pick",
                "id": int(row.id),
                "drag_key": draft_pick_drag_key(int(row.id)),
                "label": label,
                "draft_year": int(row.draft_year),
                "round": int(row.round),
                "original_team_fhm_id": oid,
                "section": "draft_pick",
            }
        )
    return out


def draft_pick_owned_by_team(
    site_session: Session,
    *,
    league_slug: str,
    team_id: int,
    drag_key: str,
) -> TradeMarketDraftPickOwnership | None:
    rid = parse_draft_pick_drag_key(drag_key)
    if rid is None:
        return None
    row = site_session.get(TradeMarketDraftPickOwnership, int(rid))
    if row is None:
        return None
    if str(row.league_slug) != str(league_slug):
        return None
    if int(row.owner_team_id or -1) != int(team_id):
        return None
    return row

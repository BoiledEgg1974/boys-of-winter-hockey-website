"""BOWL-Relegation public signing pools (radar, signable, overseas, free agents)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import league_raw_import_dir
from app.models import Player, Prospect, Team
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.draft_hub_eligibility import (
    DraftEligibilityParams,
    age_as_of,
    default_eligibility_for_league,
    draft_eligible_timeline_year_for_league,
    player_passes_age_rules,
)
from app.services.free_agents import (
    bowl_org_rights_player_ids_for_league,
    position_clause_for_role,
)
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.transfer_rules import (
    build_player_transfer_context,
    transfer_eligible_league_fhm_ids,
)

RADAR_MAX_AGE = 17


@dataclass(frozen=True)
class PlayerRightsInfo:
    holder_team: Team | None
    holder_label: str
    source: str


@dataclass(frozen=True)
class TransferListingExtra:
    external_league_fhm_id: int
    external_league_label: str
    external_team: Team
    pta_fee_usd: int
    transfer_eligible: bool
    blocked: bool
    block_reason: str
    rule_notes: tuple[str, ...]


def _raw_dir_for_league(league_slug: str) -> Path | None:
    try:
        return league_raw_import_dir(league_slug)
    except Exception:
        return None


def main_league_fhm_ids(session: Session) -> frozenset[int]:
    return frozenset(bowl_nhl_league_ids(session) or (0, 1))


def player_on_main_league_roster(player: Player, main_ids: frozenset[int]) -> bool:
    team = player.current_team
    if not team or team.fhm_league_id is None:
        return False
    return int(team.fhm_league_id) in main_ids


def _csv_delimiter_for(path: Path) -> str:
    try:
        sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:2048]
    except OSError:
        return ","
    return ";" if sample.count(";") >= sample.count(",") else ","


def _read_csv_rows(path: Path):
    delimiter = _csv_delimiter_for(path)
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f, delimiter=delimiter)
                yield from reader
            return
        except UnicodeDecodeError:
            continue
        except OSError:
            return


def _rights_holder_map_from_csv(
    session: Session,
    raw_dir: Path,
    main_ids: frozenset[int],
) -> dict[int, Team]:
    path = raw_dir / "player_rights.csv"
    if not path.is_file():
        return {}
    teams = list(session.scalars(select(Team)).all())
    team_by_fhm: dict[str, Team] = {}
    for t in teams:
        if t.fhm_team_id is None:
            continue
        if t.fhm_league_id is not None and int(t.fhm_league_id) not in main_ids:
            continue
        team_by_fhm[str(t.fhm_team_id).strip()] = t
    id_by_fhm_player: dict[str, int] = {}
    for pid, fhm_pid in session.execute(select(Player.id, Player.fhm_player_id)).all():
        if fhm_pid is None:
            continue
        fp = str(fhm_pid).strip()
        if fp:
            id_by_fhm_player[fp] = int(pid)
    out: dict[int, Team] = {}
    for row in _read_csv_rows(path):
        player_s = (row.get("PlayerId") or row.get("playerid") or "").strip()
        team_s = (row.get("Team") or row.get("team") or "").strip()
        if not player_s or not team_s:
            continue
        holder = team_by_fhm.get(team_s)
        if holder is None:
            continue
        pid = id_by_fhm_player.get(player_s)
        if pid is None:
            try:
                pid = int(player_s)
            except ValueError:
                continue
        out[int(pid)] = holder
    return out


def rights_holder_map(session: Session, league_slug: str) -> dict[int, Team]:
    main_ids = main_league_fhm_ids(session)
    out: dict[int, Team] = {}
    rows = session.scalars(
        select(Prospect)
        .options(joinedload(Prospect.team))
        .where(Prospect.player_id.isnot(None))
    ).all()
    for pr in rows:
        if pr.team_id and pr.team and pr.team.fhm_league_id is not None:
            if int(pr.team.fhm_league_id) in main_ids:
                out[int(pr.player_id)] = pr.team
    raw_dir = _raw_dir_for_league(league_slug)
    if raw_dir:
        out.update(_rights_holder_map_from_csv(session, raw_dir, main_ids))
    return out


def resolve_player_rights_info(
    session: Session,
    league_slug: str,
    player_id: int,
    holders: dict[int, Team] | None = None,
) -> PlayerRightsInfo:
    holders = holders if holders is not None else rights_holder_map(session, league_slug)
    team = holders.get(int(player_id))
    if team is None:
        return PlayerRightsInfo(None, "None", "")
    league_label = str(team.fhm_league_id or "")
    return PlayerRightsInfo(team, team.full_display_name(), "export")


def signable_age_params(session: Session, league_slug: str) -> DraftEligibilityParams:
    base = default_eligibility_for_league(league_slug)
    season = get_current_season(session)
    timeline_year = draft_eligible_timeline_year_for_league(
        league_slug,
        int(season.start_year) if season and season.start_year else None,
        int(season.end_year) if season and season.end_year else None,
        date.today().year,
    )
    return DraftEligibilityParams(
        timeline_year=timeline_year,
        min_age_years=base.min_age_years,
        min_anchor_month=base.min_anchor_month,
        min_anchor_day=base.min_anchor_day,
        max_age_years=base.max_age_years,
        max_anchor_month=base.max_anchor_month,
        max_anchor_day=base.max_anchor_day,
        pool_source=base.pool_source,
    )


def fetch_radar_prospect_players(session: Session) -> list[Player]:
    """Age 17 and under, not on a BLUP/BLOW roster (worldwide scouting board)."""
    main_ids = main_league_fhm_ids(session)
    season = get_current_season(session)
    age_ref = season_age_reference_date(season)
    q = (
        select(Player)
        .options(joinedload(Player.current_team))
        .where(Player.retired.is_(False), Player.birth_date.isnot(None))
    )
    out: list[Player] = []
    for pl in session.scalars(q).unique().all():
        age = age_as_of(pl.birth_date, age_ref)
        if age is None or age > RADAR_MAX_AGE:
            continue
        if player_on_main_league_roster(pl, main_ids):
            continue
        out.append(pl)
    return out


def fetch_signable_players(session: Session, league_slug: str) -> list[Player]:
    """Ages 18–20, no BOWL org rights, not on BLUP/BLOW roster."""
    main_ids = main_league_fhm_ids(session)
    params = signable_age_params(session, league_slug)
    rights_ids = bowl_org_rights_player_ids_for_league(session, league_slug)
    q = (
        select(Player)
        .options(joinedload(Player.current_team))
        .where(Player.retired.is_(False), Player.birth_date.isnot(None))
    )
    if rights_ids:
        q = q.where(Player.id.not_in(rights_ids))
    out: list[Player] = []
    for pl in session.scalars(q).unique().all():
        if not player_passes_age_rules(pl.birth_date, params):
            continue
        if player_on_main_league_roster(pl, main_ids):
            continue
        out.append(pl)
    return out


def fetch_overseas_transfer_players(
    session: Session,
    league_slug: str,
    *,
    league_fhm_id: int | None = None,
    eligible_only: bool = False,
) -> list[tuple[Player, TransferListingExtra]]:
    eligible_ids = transfer_eligible_league_fhm_ids(league_slug)
    if not eligible_ids:
        return []
    if league_fhm_id is not None and int(league_fhm_id) not in eligible_ids:
        return []
    filter_ids = frozenset({int(league_fhm_id)}) if league_fhm_id is not None else eligible_ids
    raw_dir = _raw_dir_for_league(league_slug)
    q = (
        select(Player)
        .join(Team, Player.current_team_id == Team.id)
        .options(joinedload(Player.current_team))
        .where(
            Player.retired.is_(False),
            Team.fhm_league_id.in_(tuple(filter_ids)),
        )
    )
    rows: list[tuple[Player, TransferListingExtra]] = []
    for pl in session.scalars(q).unique().all():
        team = pl.current_team
        if team is None:
            continue
        extra = transfer_listing_for_player(
            session,
            league_slug,
            pl,
            team,
            raw_dir=raw_dir,
        )
        if eligible_only and (extra.blocked or not extra.transfer_eligible):
            continue
        rows.append((pl, extra))
    return rows


def transfer_listing_for_player(
    session: Session,
    league_slug: str,
    player: Player,
    external_team: Team,
    *,
    raw_dir: Path | None = None,
) -> TransferListingExtra:
    ctx = build_player_transfer_context(
        session,
        player=player,
        external_team=external_team,
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    league_id = int(external_team.fhm_league_id or 0)
    eligible_ids = transfer_eligible_league_fhm_ids(league_slug)
    label = str(external_team.fhm_league_id or "")
    if external_team.fhm_league_id is not None:
        from app.models import LeagueMeta

        lm = session.scalar(
            select(LeagueMeta).where(LeagueMeta.fhm_league_id == int(external_team.fhm_league_id)).limit(1)
        )
        if lm and lm.abbreviation:
            label = lm.abbreviation
        elif lm and lm.name:
            label = lm.name
    transfer_eligible = league_id in eligible_ids and not ctx.blocked
    return TransferListingExtra(
        external_league_fhm_id=league_id,
        external_league_label=label,
        external_team=external_team,
        pta_fee_usd=int(ctx.pta_fee_usd),
        transfer_eligible=transfer_eligible,
        blocked=ctx.blocked,
        block_reason=ctx.block_reason,
        rule_notes=tuple(ctx.rule_notes),
    )


def fetch_relegation_free_agent_players(
    session: Session,
    league_slug: str,
    role: str,
) -> list[tuple[Player, dict[str, Any]]]:
    """Unsigned players (no team assignment) with rights and transfer-fee hints."""
    main_ids = main_league_fhm_ids(session)
    raw_dir = _raw_dir_for_league(league_slug)
    holders = rights_holder_map(session, league_slug)
    q = (
        select(Player)
        .options(joinedload(Player.contract), joinedload(Player.current_team))
        .where(
            Player.retired.is_(False),
            Player.current_team_id.is_(None),
            position_clause_for_role(role),
        )
    )
    out: list[tuple[Player, dict[str, Any]]] = []
    for pl in session.scalars(q).unique().all():
        rights = resolve_player_rights_info(session, league_slug, int(pl.id), holders=holders)
        meta: dict[str, Any] = {
            "rights_holder_team": rights.holder_team,
            "rights_holder_label": rights.holder_label,
            "rights_source": rights.source,
            "pta_fee_usd": 0,
            "fee_required": False,
            "transfer_blocked": False,
            "transfer_block_reason": "",
            "transfer_notes": (),
            "status_bucket": _fa_bucket(pl, rights),
        }
        if rights.holder_team is not None:
            listing = transfer_listing_for_player(
                session,
                league_slug,
                pl,
                rights.holder_team,
                raw_dir=raw_dir,
            )
            meta["pta_fee_usd"] = listing.pta_fee_usd
            meta["fee_required"] = listing.pta_fee_usd > 0 or listing.blocked
            meta["transfer_blocked"] = listing.blocked
            meta["transfer_block_reason"] = listing.block_reason
            meta["transfer_notes"] = listing.rule_notes
        out.append((pl, meta))
    return out


def _fa_bucket(pl: Player, rights: PlayerRightsInfo) -> str:
    if rights.holder_team is not None:
        return "rights_held"
    contract = getattr(pl, "contract", None)
    if contract is not None and not bool(getattr(contract, "is_ufa", False)):
        return "rfa"
    return "ufa"


def overseas_league_filter_options(session: Session, league_slug: str) -> list[dict[str, Any]]:
    from app.models import LeagueMeta

    eligible = transfer_eligible_league_fhm_ids(league_slug)
    rows = session.scalars(
        select(LeagueMeta).where(LeagueMeta.fhm_league_id.in_(tuple(eligible))).order_by(LeagueMeta.name)
    ).all()
    out: list[dict[str, Any]] = []
    for lm in rows:
        lid = int(lm.fhm_league_id)
        out.append(
            {
                "fhm_league_id": lid,
                "label": lm.abbreviation or lm.name or str(lid),
            }
        )
    return out

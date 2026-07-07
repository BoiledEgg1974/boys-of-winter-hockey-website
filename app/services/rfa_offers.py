"""RFA offer sheet rules, candidate list, compensation, and workflow helpers."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    DraftPick,
    Player,
    PlayerContract,
    PlayerGoalieCareerLine,
    PlayerSkaterCareerLine,
    Prospect,
    Team,
)
from app.services.draft_hub_eligibility import age_as_of
from app.services.draft_pick_ownership import describe_draft_pick_row, owned_draft_picks_for_team
from app.services.salary_cap_schedule import cap_for_season
from app.services.free_agents import player_ids_from_player_rights_csv_for_team
from app.services.player_contract_csv import (
    _contract_row_map,
    _contract_year_salary_int,
    contract_export_is_ufa,
    contract_export_row,
    contract_years_remaining_major,
)
from app.services.player_overall_score import build_overall_cell_map_from_players
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row, player_positions_display_label
from app.services.seasons import get_current_season, season_age_reference_date
from app.site_models import RfaOfferRequest

RFA_CATEGORIES = ("group_i", "group_ii", "group_iii", "group_iv")
HAPPINESS_LEVELS = (
    "super_happy",
    "very_happy",
    "happy",
    "okay",
    "unhappy",
    "angry",
)

CATEGORY_LABELS = {
    "group_i": "Group I",
    "group_ii": "Group II",
    "group_iii": "Group III",
    "group_iv": "Group IV",
}

CATEGORY_TOOLTIPS = {
    "group_i": "Under 25 with fewer than 5 pro seasons. Original team cannot match; equalization trade within 24 hours.",
    "group_ii": "Ages 25–30. Original team may match within 24 hours; predefined draft pick compensation if they decline.",
    "group_iii": "Age 31+. Player may allow or block matching; no compensation if match is declined when allowed.",
    "group_iv": "European draft pick unsigned 2+ years. Original team may match; no compensation if they decline.",
}

MIN_OFFER_MULTIPLIER = {
    "group_i": 1.10,
    "group_ii": 1.20,
    "group_iii": 1.00,
    "group_iv": 0.50,
}

# Accept-offer odds (% player accepts) by happiness for Groups I, II, IV.
ACCEPT_ODDS_GROUPS_I_II_IV = {
    "super_happy": 1,
    "very_happy": 10,
    "happy": 20,
    "okay": 35,
    "unhappy": 55,
    "angry": 75,
}

# Group III accept + allow-match odds.
GROUP_III_ACCEPT_ODDS = {
    "super_happy": 100,
    "very_happy": 99,
    "happy": 90,
    "okay": 80,
    "unhappy": 65,
    "angry": 45,
}
GROUP_III_ALLOW_MATCH_ODDS = GROUP_III_ACCEPT_ODDS

# Universal % of cap compensation tiers (Group II).
CAP_PERCENT_TIERS: list[tuple[float, float | None, str, str]] = [
    (0.0, 1.7, "none", "No Compensation"),
    (1.7, 2.6, "3rd", "1 Third Round Pick"),
    (2.6, 5.2, "2nd", "1 Second Round Pick"),
    (5.2, 7.8, "1st_3rd", "1 First Round Pick, 1 Third Round Pick"),
    (7.8, 10.4, "1st_2nd_3rd", "1 First Round Pick, 1 Second Round Pick, 1 Third Round Pick"),
    (10.4, 13.0, "two_1st_2nd_3rd", "2 First Round Picks, 1 Second Round Pick, 1 Third Round Pick"),
    (13.0, None, "three_1st_two_2nd", "3 First Round Picks, 2 Second Round Picks"),
]

COMP_PICK_REQUIREMENTS: dict[str, list[dict[str, int]]] = {
    "none": [],
    "3rd": [{"round": 3, "count": 1}],
    "2nd": [{"round": 2, "count": 1}],
    "1st_3rd": [{"round": 1, "count": 1}, {"round": 3, "count": 1}],
    "1st_2nd_3rd": [{"round": 1, "count": 1}, {"round": 2, "count": 1}, {"round": 3, "count": 1}],
    "two_1st_2nd_3rd": [{"round": 1, "count": 2}, {"round": 2, "count": 1}, {"round": 3, "count": 1}],
    "three_1st_two_2nd": [{"round": 1, "count": 3}, {"round": 2, "count": 2}],
}

EUROPEAN_NATIONALITIES = frozenset(
    {
        "SWE",
        "SWEDEN",
        "FIN",
        "FINLAND",
        "RUS",
        "RUSSIA",
        "CZE",
        "CZECH",
        "CZECHIA",
        "SVK",
        "SLOVAKIA",
        "GER",
        "GERMANY",
        "SUI",
        "SWITZERLAND",
        "AUT",
        "AUSTRIA",
        "NOR",
        "NORWAY",
        "DEN",
        "DENMARK",
        "LAT",
        "LATVIA",
        "BLR",
        "BELARUS",
        "UKR",
        "UKRAINE",
        "FRA",
        "FRANCE",
        "ITA",
        "ITALY",
        "SVN",
        "SLOVENIA",
        "CRO",
        "CROATIA",
        "EST",
        "ESTONIA",
        "KAZ",
        "KAZAKHSTAN",
    }
)

NA_NATIONALITIES = frozenset({"CAN", "CANADA", "USA", "UNITED STATES", "US"})


@dataclass(frozen=True)
class RfaCandidateRow:
    player: Player
    rights_team: Team
    category: str
    category_explanation: str
    previous_salary: int
    minimum_offer: int
    ovr: int | None
    abi: float | None
    pot: float | None
    position_label: str
    age: int | None
    previous_contract_label: str


@dataclass(frozen=True)
class CompensationPreview:
    tier_key: str
    label: str
    scaled_min: int
    scaled_max: int
    pick_requirements: list[dict[str, int]]
    draft_year: int
    picks_available: list[str]
    picks_missing: list[str]
    valid: bool
    cap_ceiling: int | None = None
    offer_pct_of_cap: float | None = None
    cap_missing: bool = False


def _raw_import_dir() -> Path:
    return Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))


def _season_start_year(session: Session) -> int | None:
    season = get_current_season()
    if season and season.start_year is not None:
        return int(season.start_year)
    return None


def league_salary_pool(session: Session) -> int:
    total = 0
    for c in session.scalars(select(PlayerContract)).all():
        try:
            total += max(0, int(c.average_salary or 0))
        except (TypeError, ValueError):
            continue
    return max(total, 1)


def _tier_for_pct(pct: float) -> tuple[str, str, float, float | None]:
    for lo, hi, key, label in CAP_PERCENT_TIERS:
        if hi is None:
            if pct >= lo:
                return key, label, lo, hi
        elif pct >= lo and pct < hi:
            return key, label, lo, hi
    return "none", "No Compensation", 0.0, 1.7


def _dollar_band_for_tier(
    cap_ceiling: int,
    lo_pct: float,
    hi_pct: float | None,
) -> tuple[int, int]:
    lo_dollars = 0 if lo_pct <= 0 else max(1, int(round(cap_ceiling * lo_pct / 100.0)))
    if hi_pct is None:
        return lo_dollars, cap_ceiling
    hi_dollars = max(lo_dollars, int(round(cap_ceiling * hi_pct / 100.0)) - 1)
    return lo_dollars, hi_dollars


def compensation_reference_rows(cap_ceiling: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lo, hi, key, label in CAP_PERCENT_TIERS:
        pct_label = (
            f"< {hi:g}%"
            if lo <= 0 and hi is not None
            else (f"> {lo:g}%" if hi is None else f"{lo:g}% – {hi:g}%")
        )
        lo_d = hi_d = None
        if cap_ceiling and cap_ceiling > 0:
            lo_d, hi_d = _dollar_band_for_tier(int(cap_ceiling), lo, hi)
        rows.append(
            {
                "tier_key": key,
                "label": label,
                "pct_lo": lo,
                "pct_hi": hi,
                "pct_label": pct_label,
                "lo_dollars": lo_d,
                "hi_dollars": hi_d,
                "pick_requirements": COMP_PICK_REQUIREMENTS.get(key, []),
            }
        )
    return rows


def _pro_seasons_before_from_db(session: Session, player: Player, season_start_year: int) -> int:
    """Distinct seasons with GP from imported career lines before *season_start_year*."""
    years: set[int] = set()
    for model in (PlayerSkaterCareerLine, PlayerGoalieCareerLine):
        rows = session.scalars(
            select(model.season_year)
            .where(
                model.player_id == player.id,
                model.season_year < int(season_start_year),
                model.gp > 0,
            )
            .distinct()
        ).all()
        years.update(int(y) for y in rows if y is not None)
    return len(years)


def _pro_seasons_before(session: Session, player: Player, season_start_year: int, raw_dir: Path) -> int:
    fhm = str(player.fhm_player_id or "").strip()
    count = 0
    if fhm:
        row = _contract_row_map(raw_dir / "player_contract.csv").get(fhm)
        if row:
            for y in range(1970, int(season_start_year)):
                major = _contract_year_salary_int(row, "major", y)
                minor = _contract_year_salary_int(row, "minor", y)
                if (major is not None and major >= 0) or (minor is not None and minor >= 0):
                    count += 1
    if count > 0:
        return count
    return _pro_seasons_before_from_db(session, player, season_start_year)


def _is_european(player: Player) -> bool:
    nat = str(player.nationality or "").strip().upper()
    if not nat:
        return False
    if nat in NA_NATIONALITIES:
        return False
    if nat in EUROPEAN_NATIONALITIES:
        return True
    return nat not in NA_NATIONALITIES and len(nat) >= 3


def _unsigned_european_draft_years(
    session: Session,
    player: Player,
    season_start_year: int,
    *,
    raw_dir: Path | None = None,
) -> int | None:
    if not _is_european(player):
        return None
    picks = list(
        session.scalars(
            select(DraftPick)
            .where(DraftPick.player_id == player.id)
            .order_by(DraftPick.draft_year.asc())
        ).all()
    )
    if not picks:
        return None
    draft_year = picks[0].draft_year
    if draft_year is None:
        return None
    base = raw_dir or _raw_import_dir()
    fhm = str(player.fhm_player_id or "").strip()
    row = contract_export_row(fhm, base) if fhm else None
    if row is not None and contract_export_is_ufa(fhm, base):
        return None
    if row:
        for y in range(int(draft_year), int(season_start_year) + 1):
            major = _contract_year_salary_int(row, "major", y)
            if major is not None and major >= 0:
                return None
    if _pro_seasons_before(session, player, season_start_year, base) > 0:
        return None
    years_since = int(season_start_year) - int(draft_year)
    return years_since if years_since >= 2 else None


def derive_rfa_category(
    session: Session,
    player: Player,
    *,
    season_start_year: int,
    age: int | None,
) -> tuple[str, str]:
    raw_dir = _raw_import_dir()
    unsigned_years = _unsigned_european_draft_years(
        session, player, season_start_year, raw_dir=raw_dir
    )
    if unsigned_years is not None:
        return (
            "group_iv",
            f"European prospect unsigned {unsigned_years} seasons after draft year.",
        )
    pro_seasons = _pro_seasons_before(session, player, season_start_year, raw_dir)
    if age is not None and age < 25 and pro_seasons < 5:
        return (
            "group_i",
            f"Under 25 ({age}) with {pro_seasons} pro season(s) on record (fewer than 5).",
        )
    if age is not None and 25 <= age <= 30:
        return ("group_ii", f"Age {age} (Group II: 25–30).")
    if age is not None and age >= 31:
        return ("group_iii", f"Age {age} (Group III: 31+).")
    if pro_seasons < 5:
        return ("group_i", f"Fewer than 5 pro seasons ({pro_seasons}); age unknown.")
    return ("group_ii", "Default Group II (age unavailable).")


def _previous_salary(
    session: Session,
    player: Player,
    contract: PlayerContract | None,
    season_start_year: int,
) -> int:
    raw_dir = _raw_import_dir()
    fhm = str(player.fhm_player_id or "").strip()
    row = _contract_row_map(raw_dir / "player_contract.csv").get(fhm) if fhm else None
    if row and season_start_year:
        prev_year = int(season_start_year) - 1
        major = _contract_year_salary_int(row, "major", prev_year)
        minor = _contract_year_salary_int(row, "minor", prev_year)
        if major is not None and major >= 0:
            return int(major)
        if minor is not None and minor >= 0:
            return int(minor)
        major_cur = _contract_year_salary_int(row, "major", int(season_start_year))
        if major_cur is not None and major_cur >= 0:
            return int(major_cur)
    if contract is not None:
        try:
            return max(0, int(contract.average_salary or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def minimum_offer_amount(category: str, previous_salary: int) -> int:
    mult = MIN_OFFER_MULTIPLIER.get(category, 1.0)
    return max(1, int(round(previous_salary * mult)))


def _rights_team_for_contract(session: Session, contract: PlayerContract) -> Team | None:
    fhm = str(contract.fhm_team_id or "").strip()
    if fhm:
        tm = session.scalar(select(Team).where(Team.fhm_team_id == fhm).limit(1))
        if tm:
            return tm
    pl = contract.player
    if pl and pl.current_team_id:
        return session.get(Team, int(pl.current_team_id))
    return None


def _rights_team_by_player_from_rights_csv(session: Session, raw_dir: Path) -> dict[int, Team]:
    out: dict[int, Team] = {}
    teams = list(
        session.scalars(
            select(Team)
            .where(Team.fhm_team_id.isnot(None))
            .order_by(Team.id.asc())
        ).all()
    )
    for team in teams:
        for pid in player_ids_from_player_rights_csv_for_team(session, raw_dir, team):
            out.setdefault(int(pid), team)
    return out


def _rights_team_by_player_from_prospects(session: Session) -> dict[int, Team]:
    out: dict[int, Team] = {}
    team_cache: dict[int, Team | None] = {}
    rows = list(
        session.scalars(
            select(Prospect)
            .where(Prospect.player_id.isnot(None), Prospect.team_id.isnot(None))
            .order_by(Prospect.id.asc())
        ).all()
    )
    for pr in rows:
        if pr.player_id is None or pr.team_id is None:
            continue
        tid = int(pr.team_id)
        if tid not in team_cache:
            team_cache[tid] = session.get(Team, tid)
        team = team_cache.get(tid)
        if team is not None:
            out.setdefault(int(pr.player_id), team)
    return out


def is_rfa_eligible(
    session: Session,
    player: Player,
    contract: PlayerContract,
    *,
    season_start_year: int | None,
    raw_dir: Path | None = None,
) -> bool:
    """True when the current FHM export still lists the player as a restricted free agent."""
    if season_start_year is None:
        return False
    base = raw_dir or _raw_import_dir()
    fhm = str(player.fhm_player_id or "").strip()
    export_ufa = contract_export_is_ufa(fhm, base) if fhm else None
    if export_ufa is None:
        # Game dropped the contract row — stale DB rows must not stay offer-sheet eligible.
        return False
    if export_ufa or bool(contract.is_ufa):
        return False
    yrs = contract_years_remaining_major(fhm, season_start_year, base)
    return yrs is None or int(yrs) <= 0


def compensation_for_offer(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    offering_team_id: int,
    offer_salary: int,
    category: str,
) -> CompensationPreview:
    sy = _season_start_year(session)
    draft_year = int(sy) + 1 if sy is not None else date.today().year + 1
    cap_ceiling: int | None = None
    cap_missing = False
    if sy is not None:
        cap_ceiling, _ = cap_for_season(site_session, league_slug, int(sy))
    if cap_ceiling is None or int(cap_ceiling) <= 0:
        cap_missing = True
        cap_ceiling = None

    tier_key = "none"
    label = "No Compensation"
    scaled_lo = 0
    scaled_hi = 0
    offer_pct: float | None = None

    if cap_ceiling and int(cap_ceiling) > 0:
        offer_pct = float(offer_salary) / float(cap_ceiling) * 100.0
        tier_key, label, lo_pct, hi_pct = _tier_for_pct(offer_pct)
        scaled_lo, scaled_hi = _dollar_band_for_tier(int(cap_ceiling), lo_pct, hi_pct)

    if category in ("group_i", "group_iii", "group_iv"):
        tier_key = "none"
        label = "No Compensation"

    requirements = COMP_PICK_REQUIREMENTS.get(tier_key, [])
    owned = owned_draft_picks_for_team(
        site_session, league_slug=league_slug, team_id=int(offering_team_id)
    )
    owned_next = [p for p in owned if int(p.draft_year) == int(draft_year)]
    picks_available: list[str] = []
    picks_missing: list[str] = []
    for req in requirements:
        rnd = int(req["round"])
        need = int(req["count"])
        have = [p for p in owned_next if int(p.round) == rnd]
        for p in have[:need]:
            orig = session.get(Team, int(p.original_team_id)) if p.original_team_id else None
            owner = session.get(Team, int(p.owner_team_id)) if p.owner_team_id else None
            picks_available.append(describe_draft_pick_row(p, original_team=orig, owner_team=owner))
        if len(have) < need:
            picks_missing.append(f"{need - len(have)}× {draft_year} {rnd}{'st' if rnd==1 else 'nd' if rnd==2 else 'rd' if rnd==3 else 'th'} round")
    valid = len(picks_missing) == 0 and not cap_missing
    return CompensationPreview(
        tier_key=tier_key,
        label=label,
        scaled_min=scaled_lo,
        scaled_max=scaled_hi,
        pick_requirements=requirements,
        draft_year=draft_year,
        picks_available=picks_available,
        picks_missing=picks_missing,
        valid=valid,
        cap_ceiling=cap_ceiling,
        offer_pct_of_cap=offer_pct,
        cap_missing=cap_missing,
    )


def list_rfa_candidates(
    session: Session,
    *,
    league_slug: str,
    offering_team_id: int | None = None,
) -> list[RfaCandidateRow]:
    raw_dir = _raw_import_dir()
    sy = _season_start_year(session)
    age_ref = season_age_reference_date(get_current_season())
    contracts = list(session.scalars(
        select(PlayerContract).join(Player, Player.id == PlayerContract.player_id)
    ).all())
    contract_by_player_id: dict[int, PlayerContract] = {}
    players_by_id: dict[int, Player] = {}
    rights_by_player_id: dict[int, Team] = {}

    for c in contracts:
        pl = c.player
        if pl is None:
            continue
        pid = int(pl.id)
        contract_by_player_id[pid] = c
        players_by_id[pid] = pl
        rights = _rights_team_for_contract(session, c)
        if rights is not None:
            rights_by_player_id.setdefault(pid, rights)

    # Some imports represent restricted rights outside PlayerContract. Add those so the page
    # remains complete after each league's CSV refresh, even when contract rows are sparse.
    rights_by_player_id.update({
        pid: team
        for pid, team in _rights_team_by_player_from_rights_csv(session, raw_dir).items()
        if pid not in rights_by_player_id
    })
    rights_by_player_id.update({
        pid: team
        for pid, team in _rights_team_by_player_from_prospects(session).items()
        if pid not in rights_by_player_id
    })

    players: list[Player] = []
    candidate_player_ids: set[int] = set()
    rows: list[RfaCandidateRow] = []
    for pid, rights in rights_by_player_id.items():
        pl = players_by_id.get(pid) or session.get(Player, pid)
        if pl is None or bool(getattr(pl, "retired", False)):
            continue
        contract = contract_by_player_id.get(pid)
        if contract is not None and not is_rfa_eligible(
            session, pl, contract, season_start_year=sy, raw_dir=raw_dir
        ):
            continue
        if offering_team_id is not None and int(rights.id) == int(offering_team_id):
            continue
        players_by_id[pid] = pl
        candidate_player_ids.add(pid)
        players.append(pl)
    overall_map = build_overall_cell_map_from_players(session, players)
    for pid in candidate_player_ids:
        pl = players_by_id.get(pid)
        rights = rights_by_player_id.get(pid)
        contract = contract_by_player_id.get(pid)
        if pl is None:
            continue
        if rights is None:
            continue
        age = age_as_of(pl.birth_date, age_ref) if pl.birth_date else None
        category, explanation = derive_rfa_category(session, pl, season_start_year=int(sy or 0), age=age)
        prev_sal = _previous_salary(session, pl, contract, int(sy or 0))
        min_offer = minimum_offer_amount(category, prev_sal)
        rr = get_player_ratings_row(pl.fhm_player_id)
        abi = fhm_abi_pot_float(rr.get("ability")) if rr else None
        pot = fhm_abi_pot_float(rr.get("potential")) if rr else None
        ov_cell = overall_map.get(int(pl.id), {})
        ovr = ov_cell.get("score")
        yrs_left = contract_years_remaining_major(pl.fhm_player_id, sy, raw_dir) if sy else None
        contract_label = f"${prev_sal:,} AAV"
        if yrs_left is not None:
            contract_label += f" · {yrs_left} yr left"
        rows.append(
            RfaCandidateRow(
                player=pl,
                rights_team=rights,
                category=category,
                category_explanation=explanation,
                previous_salary=prev_sal,
                minimum_offer=min_offer,
                ovr=int(ovr) if ovr is not None else None,
                abi=abi,
                pot=pot,
                position_label=player_positions_display_label(pl),
                age=age,
                previous_contract_label=contract_label,
            )
        )
    rows.sort(key=lambda r: (-(r.ovr or 0), r.player.full_name or ""))
    return rows


def accept_odds_percent(category: str, happiness: str) -> int:
    h = str(happiness or "").strip().lower()
    if category == "group_iii":
        return GROUP_III_ACCEPT_ODDS.get(h, 50)
    return ACCEPT_ODDS_GROUPS_I_II_IV.get(h, 50)


def allow_match_odds_percent(happiness: str) -> int:
    return GROUP_III_ALLOW_MATCH_ODDS.get(str(happiness or "").strip().lower(), 50)


def roll_player_accepts(category: str, happiness: str) -> tuple[bool, float]:
    odds = accept_odds_percent(category, happiness)
    roll = random.random() * 100.0
    return roll < float(odds), roll


def roll_group_iii_allows_match(happiness: str) -> tuple[bool, float]:
    odds = allow_match_odds_percent(happiness)
    roll = random.random() * 100.0
    return roll < float(odds), roll


def validate_offer_submission(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    offering_team_id: int,
    player_id: int,
    offer_salary: int,
    offer_years: int,
) -> tuple[RfaCandidateRow | None, CompensationPreview | None, str | None]:
    if offer_years < 1 or offer_years > 15:
        return None, None, "Offer years must be between 1 and 15."
    candidate = next(
        (r for r in list_rfa_candidates(session, league_slug=league_slug, offering_team_id=offering_team_id)
         if int(r.player.id) == int(player_id)),
        None,
    )
    if candidate is None:
        return None, None, "Player is not eligible for an offer sheet from your team."
    if int(offer_salary) < int(candidate.minimum_offer):
        return (
            None,
            None,
            f"Offer must be at least ${candidate.minimum_offer:,} for {CATEGORY_LABELS.get(candidate.category, candidate.category)}.",
        )
    comp = compensation_for_offer(
        session,
        site_session,
        league_slug=league_slug,
        offering_team_id=offering_team_id,
        offer_salary=int(offer_salary),
        category=candidate.category,
    )
    if candidate.category == "group_ii":
        if comp.cap_missing:
            return None, comp, "Current season salary cap ceiling is not set — ask your commissioner to configure it on Admin RFA."
        if not comp.valid:
            return None, comp, "You do not own the required following-year draft picks for this offer."
    return candidate, comp, None


def create_rfa_offer_request(
    site_session: Session,
    *,
    league_slug: str,
    offering_user_id: int,
    offering_team_id: int,
    candidate: RfaCandidateRow,
    offer_salary: int,
    offer_years: int,
    special_clauses: str,
    comp: CompensationPreview,
) -> RfaOfferRequest:
    req = RfaOfferRequest(
        league_slug=league_slug,
        offering_user_id=int(offering_user_id),
        offering_team_id=int(offering_team_id),
        player_id=int(candidate.player.id),
        player_fhm_id=str(candidate.player.fhm_player_id or "") or None,
        rights_team_id=int(candidate.rights_team.id),
        rfa_category=candidate.category,
        category_explanation=candidate.category_explanation,
        previous_contract_salary=int(candidate.previous_salary),
        minimum_offer_salary=int(candidate.minimum_offer),
        offer_salary=int(offer_salary),
        offer_years=int(offer_years),
        special_clauses=(special_clauses or "").strip(),
        compensation_tier_key=comp.tier_key,
        compensation_label=comp.label,
        compensation_picks_json=json.dumps(comp.pick_requirements),
        compensation_draft_year=int(comp.draft_year),
        compensation_valid=bool(comp.valid),
        status="pending_admin",
    )
    site_session.add(req)
    site_session.flush()
    return req


def compensation_panel_dict(comp: CompensationPreview, *, category: str) -> dict[str, Any]:
    return {
        "tier_key": comp.tier_key,
        "label": comp.label,
        "scaled_min": comp.scaled_min,
        "scaled_max": comp.scaled_max,
        "draft_year": comp.draft_year,
        "pick_requirements": comp.pick_requirements,
        "picks_available": comp.picks_available,
        "picks_missing": comp.picks_missing,
        "valid": comp.valid,
        "applies": category == "group_ii",
        "cap_ceiling": comp.cap_ceiling,
        "offer_pct_of_cap": comp.offer_pct_of_cap,
        "cap_missing": comp.cap_missing,
    }


def happiness_label(happiness: str | None) -> str:
    labels = {
        "super_happy": "Super Happy",
        "very_happy": "Very Happy",
        "happy": "Happy",
        "okay": "Okay",
        "unhappy": "Unhappy",
        "angry": "Angry",
    }
    return labels.get(str(happiness or "").strip().lower(), "—")


def status_label(status: str | None) -> str:
    labels = {
        "pending_admin": "Pending admin review",
        "player_rejected": "Player rejected offer",
        "awaiting_equalization": "Equalization required",
        "awaiting_original_match": "Awaiting original team match/reject",
        "original_matched": "Original team matched",
        "original_rejected": "Original team declined — proceed",
        "completed": "Completed",
        "cancelled": "Cancelled",
    }
    return labels.get(str(status or "").strip().lower(), str(status or "—"))

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
from app.models import DraftPick, Player, PlayerContract, Prospect, Team
from app.services.draft_hub_eligibility import age_as_of
from app.services.draft_pick_ownership import describe_draft_pick_row, owned_draft_picks_for_team
from app.services.free_agents import player_ids_from_player_rights_csv_for_team
from app.services.player_contract_csv import _contract_row_map, _contract_year_salary_int, contract_years_remaining_major
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

# Dollar tier templates from league PDF (scaled dynamically).
LEAGUE_TIER_TEMPLATES: dict[str, list[tuple[int, int, str, str]]] = {
    "bowl-fantasy": [
        (0, 99_999, "none", "No Compensation"),
        (100_000, 249_999, "3rd", "3rd Round Selection"),
        (250_000, 374_999, "2nd", "2nd Round Selection"),
        (375_000, 449_999, "1st_3rd", "1st & 3rd Round Selections"),
        (450_000, 574_999, "1st_2nd_3rd", "1st, 2nd & 3rd Round Selections"),
        (575_000, 699_999, "two_1st_2nd_3rd", "Two 1sts, 2nd & 3rd Round Selections"),
        (700_000, 9_999_999_999, "three_1st", "Three 1st Round Selections"),
    ],
    "bowl-historical": [
        (0, 97_999, "none", "No Compensation"),
        (98_000, 244_999, "3rd", "3rd Round Selection"),
        (245_000, 367_499, "2nd", "2nd Round Selection"),
        (367_500, 440_999, "1st_3rd", "1st & 3rd Round Selections"),
        (441_000, 563_499, "1st_2nd_3rd", "1st, 2nd & 3rd Round Selections"),
        (563_500, 685_999, "two_1st_2nd_3rd", "Two 1sts, 2nd & 3rd Round Selections"),
        (686_000, 9_999_999_999, "three_1st", "Three 1st Round Selections"),
    ],
    "bowl-cap": [
        (0, 312_572, "none", "No Compensation"),
        (312_573, 625_212, "3rd", "3rd Round Selection"),
        (625_213, 937_785, "2nd", "2nd Round Selection"),
        (937_786, 1_250_357, "1st_3rd", "1st & 3rd Round Selections"),
        (1_250_358, 1_562_930, "1st_2nd_3rd", "1st, 2nd & 3rd Round Selections"),
        (1_562_931, 1_875_502, "two_1st_2nd_3rd", "Two 1sts, 2nd & 3rd Round Selections"),
        (1_875_503, 9_999_999_999, "three_1st", "Three 1st Round Selections"),
    ],
}

COMP_PICK_REQUIREMENTS: dict[str, list[dict[str, int]]] = {
    "none": [],
    "3rd": [{"round": 3, "count": 1}],
    "2nd": [{"round": 2, "count": 1}],
    "1st_3rd": [{"round": 1, "count": 1}, {"round": 3, "count": 1}],
    "1st_2nd_3rd": [{"round": 1, "count": 1}, {"round": 2, "count": 1}, {"round": 3, "count": 1}],
    "two_1st_2nd_3rd": [{"round": 1, "count": 2}, {"round": 2, "count": 1}, {"round": 3, "count": 1}],
    "three_1st": [{"round": 1, "count": 3}],
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


def _baseline_salary_pool(league_slug: str) -> int:
    """Reference salary pool used to scale compensation tiers (approx. PDF era)."""
    defaults = {
        "bowl-fantasy": 12_000_000,
        "bowl-historical": 90_000_000,
        "bowl-cap": 55_000_000,
    }
    return defaults.get(str(league_slug or "").strip(), 50_000_000)


def _salary_scale(league_slug: str, session: Session) -> float:
    current = float(league_salary_pool(session))
    baseline = float(_baseline_salary_pool(league_slug))
    return max(0.25, min(4.0, current / baseline))


def _scaled_tiers(league_slug: str, session: Session) -> list[tuple[int, int, str, str]]:
    scale = _salary_scale(league_slug, session)
    template = LEAGUE_TIER_TEMPLATES.get(league_slug, LEAGUE_TIER_TEMPLATES["bowl-cap"])
    out: list[tuple[int, int, str, str]] = []
    for lo, hi, key, label in template:
        out.append((int(lo * scale), int(hi * scale), key, label))
    return out


def _pro_seasons_before(session: Session, player: Player, season_start_year: int, raw_dir: Path) -> int:
    fhm = str(player.fhm_player_id or "").strip()
    if not fhm:
        return 0
    row = _contract_row_map(raw_dir / "player_contract.csv").get(fhm)
    if not row:
        return 0
    count = 0
    for y in range(1970, int(season_start_year)):
        major = _contract_year_salary_int(row, "major", y)
        minor = _contract_year_salary_int(row, "minor", y)
        if (major is not None and major >= 0) or (minor is not None and minor >= 0):
            count += 1
    return count


def _is_european(player: Player) -> bool:
    nat = str(player.nationality or "").strip().upper()
    if not nat:
        return False
    if nat in NA_NATIONALITIES:
        return False
    if nat in EUROPEAN_NATIONALITIES:
        return True
    return nat not in NA_NATIONALITIES and len(nat) >= 3


def _unsigned_european_draft_years(session: Session, player: Player, season_start_year: int) -> int | None:
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
    raw_dir = _raw_import_dir()
    fhm = str(player.fhm_player_id or "").strip()
    row = _contract_row_map(raw_dir / "player_contract.csv").get(fhm) if fhm else None
    if row:
        for y in range(int(draft_year), int(season_start_year) + 1):
            major = _contract_year_salary_int(row, "major", y)
            if major is not None and major >= 0:
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
    unsigned_years = _unsigned_european_draft_years(session, player, season_start_year)
    if unsigned_years is not None:
        return (
            "group_iv",
            f"European prospect unsigned {unsigned_years} seasons after draft year.",
        )
    raw_dir = _raw_import_dir()
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
    if bool(contract.is_ufa):
        return False
    if season_start_year is None:
        return False
    yrs = contract_years_remaining_major(
        player.fhm_player_id,
        season_start_year,
        raw_dir or _raw_import_dir(),
    )
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
    tiers = _scaled_tiers(league_slug, session)
    tier_key = "none"
    label = "No Compensation"
    scaled_lo = 0
    scaled_hi = 0
    for lo, hi, key, lbl in tiers:
        if int(offer_salary) >= lo and int(offer_salary) <= hi:
            tier_key, label, scaled_lo, scaled_hi = key, lbl, lo, hi
            break
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
    valid = len(picks_missing) == 0
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
    if candidate.category == "group_ii" and not comp.valid:
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

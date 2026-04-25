"""Free agents: not on the sim's main NHL/BOWL roster; excludes undrafted-prospects pool."""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import free_agents_exclude_nhl_bowl_drafted_max_age
from app.models import Draft, DraftPick, Player, PlayerContract, Prospect, Team
from app.services.draft_history import nhl_bowl_draft_clause

FA_ROLES = ("fwd", "def", "g")

# (full label, column abbr, player_ratings.csv key)
FA_SKATER_OVERVIEW: tuple[tuple[str, str, str], ...] = (
    ("Skating", "SKT", "skating"),
    ("Shooting", "SHT", "shooting"),
    ("Playmaking", "PLM", "playmaking"),
    ("Defending", "DEF", "defending"),
    ("Physicality", "PHY", "physicality"),
    ("Conditioning", "CON", "conditioning"),
    ("Character", "CHR", "character"),
    ("Hockey sense", "HSN", "hockey_sense"),
)
FA_SKATER_OFFENSE: tuple[tuple[str, str, str], ...] = (
    ("Screening", "SCR", "screening"),
    ("Getting open", "GTO", "getting_open"),
    ("Passing", "PAS", "passing"),
    ("Puck handling", "PH", "puck_handling"),
    ("Shooting accuracy", "SAC", "shooting_accuracy"),
    ("Shooting range", "SRN", "shooting_range"),
    ("Offensive read", "ORD", "offensive_read"),
)
FA_SKATER_DEFENSE: tuple[tuple[str, str, str], ...] = (
    ("Checking", "CHK", "checking"),
    ("Faceoffs", "FO", "faceoffs"),
    ("Hitting", "HIT", "hitting"),
    ("Positioning", "POS", "positioning"),
    ("Shot blocking", "SB", "shot_blocking"),
    ("Stickchecking", "STC", "stickchecking"),
    ("Defensive read", "DRD", "defensive_read"),
)
FA_SKATER_MENTAL: tuple[tuple[str, str, str], ...] = (
    ("Aggression", "AGG", "aggression"),
    ("Bravery", "BRV", "bravery"),
    ("Determination", "DET", "determination"),
    ("Team Player", "TMP", "teamplayer"),
    ("Leadership", "LDR", "leadership"),
    ("Temperament", "TMPR", "temperament"),
    ("Professionalism", "PRO", "professionalism"),
)
FA_SKATER_PHYSICAL: tuple[tuple[str, str, str], ...] = (
    ("Acceleration", "ACC", "acceleration"),
    ("Agility", "AGI", "agility"),
    ("Balance", "BAL", "balance"),
    ("Speed", "SPD", "speed"),
    ("Stamina", "STA", "stamina"),
    ("Strength", "STR", "strength"),
    ("Fighting", "FGT", "fighting"),
)
FA_GOALIE_MAIN: tuple[tuple[str, str, str], ...] = (
    ("Positioning", "GPOS", "g_positioning"),
    ("Passing", "GPAS", "g_passing"),
    ("Pokecheck", "POK", "g_pokecheck"),
    ("Blocker", "BLK", "blocker"),
    ("Glove", "GLV", "glove"),
    ("Rebound", "REB", "rebound"),
    ("Recovery", "REC", "recovery"),
    ("Puckhandling", "GPU", "g_puckhandling"),
    ("Low Shots", "LOW", "low_shots"),
    ("Skating", "GSK", "g_skating"),
    ("Reflexes", "REF", "reflexes"),
)
FA_GOALIE_MENTAL: tuple[tuple[str, str, str], ...] = (
    ("Aggression", "AGG", "aggression"),
    ("Mental Toughness", "MTO", "mental_toughness"),
    ("Determination", "DET", "determination"),
    ("Team Player", "TMP", "teamplayer"),
    ("Leadership", "LDR", "leadership"),
    ("Stamina", "GST", "goalie_stamina"),
    ("Professionalism", "PRO", "professionalism"),
)

SKATER_VIEWS = ("overview", "offense", "defense", "mental", "physical")
GOALIE_VIEWS = ("overview", "mental")


def _player_age_years_on(birth: date, as_of: date) -> int:
    return as_of.year - birth.year - ((as_of.month, as_of.day) < (birth.month, birth.day))


def undrafted_prospects_player_ids(session: Session, age_ref: date, *, max_age: int) -> frozenset[int]:
    """Same eligibility as the Undrafted Prospects page for this league (no NHL/BOWL pick, age cap, no BOWL rights)."""
    drafted_subq = (
        select(DraftPick.player_id)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .where(DraftPick.player_id.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
    )
    rights_ids = bowl_nhl_org_rights_player_ids(session)
    where_clauses = [
        Player.retired.is_(False),
        Player.birth_date.isnot(None),
        Player.id.not_in(drafted_subq),
    ]
    if rights_ids:
        where_clauses.append(Player.id.not_in(rights_ids))
    rows = session.execute(select(Player.id, Player.birth_date).where(*where_clauses)).all()
    out: set[int] = set()
    for pid, bd in rows:
        if bd is None:
            continue
        if _player_age_years_on(bd, age_ref) <= max_age:
            out.add(int(pid))
    return frozenset(out)


def _nhl_bowl_drafted_player_ids_age_lte(session: Session, age_ref: date, *, max_age: int) -> frozenset[int]:
    """Player ids with an NHL/BOWL draft pick whose age (season reference) is at most ``max_age``."""
    drafted_subq = (
        select(DraftPick.player_id)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .where(DraftPick.player_id.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
    )
    rows = session.execute(
        select(Player.id, Player.birth_date).where(
            Player.retired.is_(False),
            Player.birth_date.isnot(None),
            Player.id.in_(drafted_subq),
        )
    ).all()
    out: set[int] = set()
    for pid, bd in rows:
        if bd is None:
            continue
        if _player_age_years_on(bd, age_ref) <= max_age:
            out.add(int(pid))
    return frozenset(out)


def _main_league_fhm_team_ids_subq():
    """FHM team ids for NHL/BOWL clubs (``fhm_league_id`` NULL or 0)."""
    return (
        select(Team.fhm_team_id)
        .where(Team.fhm_team_id.isnot(None))
        .where(or_(Team.fhm_league_id.is_(None), Team.fhm_league_id == 0))
    )


def _no_nhl_bowl_org_contract_clause():
    """True when the player is not under NHL/BOWL rights (RFA-style) via contract.

    UFAs may still carry a stale ``fhm_team_id`` in exports; ``is_ufa`` keeps them eligible here.
    """
    main_ids = _main_league_fhm_team_ids_subq()
    return or_(
        PlayerContract.id.is_(None),
        PlayerContract.is_ufa.is_(True),
        PlayerContract.fhm_team_id.is_(None),
        and_(PlayerContract.fhm_team_id.isnot(None), PlayerContract.fhm_team_id.not_in(main_ids)),
    )


def _prospect_rights_main_team_subq():
    """Player ids listed as prospects of an NHL/BOWL team (same rule as depth-chart org pool)."""
    return (
        select(Prospect.player_id)
        .join(Team, Prospect.team_id == Team.id)
        .where(Prospect.player_id.isnot(None))
        .where(or_(Team.fhm_league_id.is_(None), Team.fhm_league_id == 0))
    )


def bowl_nhl_org_rights_player_ids(session: Session) -> frozenset[int]:
    """Player ids with NHL/BOWL organizational rights (prospect row or non-UFA contract to a main club)."""
    main_ids = _main_league_fhm_team_ids_subq()
    out: set[int] = set()
    for pid in session.scalars(_prospect_rights_main_team_subq()).all():
        if pid is not None:
            out.add(int(pid))
    for pid in session.scalars(
        select(PlayerContract.player_id).where(
            PlayerContract.player_id.isnot(None),
            PlayerContract.fhm_team_id.in_(main_ids),
            or_(PlayerContract.is_ufa.is_(False), PlayerContract.is_ufa.is_(None)),
        )
    ).all():
        if pid is not None:
            out.add(int(pid))
    return frozenset(out)


def position_clause_for_role(role: str):
    if role == "fwd":
        return Player.position.in_(("C", "LW", "RW"))
    if role == "def":
        return Player.position.in_(("D", "LD", "RD"))
    if role == "g":
        pos = func.upper(func.trim(Player.position))
        return or_(pos == "G", pos.like("G %"), pos.like("G-%"))
    raise ValueError(role)


def fetch_free_agent_players(
    session: Session, role: str, *, age_ref: date, undrafted_max_age: int, league_slug: str = ""
) -> list[Player]:
    """Players not on an NHL/BOWL main roster who are also free of NHL/BOWL org rights.

    * **Assignment:** ``current_team`` is not a main-league club (same as before: NULL/0 league id).
    * **Rights:** no ``PlayerContract.fhm_team_id`` pointing at an NHL/BOWL team, and not a
      ``Prospect`` of such a team — matches who can appear on a franchise depth chart without being
      on the big club's active roster.
    * **Undrafted pool:** excludes same set as Undrafted Prospects for this league (age cap + no NHL/BOWL pick).
    * **Drafted juniors (Fantasy/Cap):** excludes players with an NHL/BOWL draft pick through a league-specific
      age ceiling when exports omit prospect/contract rows for rights-holders in other leagues.
    """
    if role not in FA_ROLES:
        role = "fwd"
    ct = Team.__table__.alias("fa_curr_team")
    not_on_main_nhl_bowl = or_(
        Player.current_team_id.is_(None),
        and_(ct.c.fhm_league_id.isnot(None), ct.c.fhm_league_id != 0),
    )
    skip_ud = undrafted_prospects_player_ids(session, age_ref, max_age=undrafted_max_age)
    prospect_main = _prospect_rights_main_team_subq()
    q = (
        select(Player)
        .outerjoin(PlayerContract, PlayerContract.player_id == Player.id)
        .outerjoin(ct, Player.current_team_id == ct.c.id)
        .options(joinedload(Player.contract), joinedload(Player.current_team))
        .where(
            Player.retired.is_(False),
            not_on_main_nhl_bowl,
            _no_nhl_bowl_org_contract_clause(),
            Player.id.not_in(prospect_main),
            position_clause_for_role(role),
        )
    )
    if skip_ud:
        q = q.where(Player.id.not_in(skip_ud))
    draft_hide_age = free_agents_exclude_nhl_bowl_drafted_max_age(league_slug)
    if draft_hide_age is not None:
        skip_drafted_young = _nhl_bowl_drafted_player_ids_age_lte(
            session, age_ref, max_age=draft_hide_age
        )
        if skip_drafted_young:
            q = q.where(Player.id.not_in(skip_drafted_young))
    return list(session.scalars(q).unique().all())
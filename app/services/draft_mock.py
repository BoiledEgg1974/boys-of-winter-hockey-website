"""Entertainment mock draft builder for the public Draft Eligible page."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Season, Team, TeamStanding
from app.services.draft_pick_ownership import draft_pick_teams_for_grid
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.site_models import TradeMarketDraftPickOwnership

MOCK_DRAFT_ROUNDS = 3
_NEED_WINDOW = 18

# Crease strength thresholds (composite 1–100 OVR from ABI/POT + goalie attributes).
_GOALIE_STRONG_STARTER_OVR = 56
_GOALIE_OK_BACKUP_OVR = 48
_GOALIE_WEAK_STARTER_OVR = 50
_GOALIE_WEAK_BACKUP_OVR = 44

# Per-round board-rank limits: avoid reaching for goalies while elite skaters remain.
_GOALIE_MAX_BOARD_RANK_BY_ROUND = {1: 8, 2: 14, 3: 22}


@dataclass(frozen=True)
class MockDraftRow:
    overall: int
    round: int
    round_pick: int
    original_team: Team
    owner_team: Team
    player: Player
    player_rank: int
    need_bucket: str
    need_summary: str
    traded: bool


@dataclass(frozen=True)
class TeamGoalieProfile:
    count: int
    best_ovr: int | None
    second_ovr: int | None


def _team_fhm_id(team: Team | None) -> int | None:
    raw = str(getattr(team, "fhm_team_id", None) or "").strip()
    return int(raw) if raw.isdigit() else None


def _position_bucket(player: Player) -> str:
    label = (player_positions_display_label(player) or player.position or "").upper()
    if "G" in label:
        return "G"
    if "D" in label:
        return "D"
    return "F"


def _goalie_ovr(player: Player) -> int | None:
    rr = get_player_ratings_row(player.fhm_player_id)
    return compute_player_overall_100(
        player.overall_ability,
        player.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(player),
    )


def _goalie_profile_from_ovrs(ovrs: list[int]) -> TeamGoalieProfile:
    ordered = sorted(ovrs, reverse=True)
    return TeamGoalieProfile(
        count=len(ordered),
        best_ovr=ordered[0] if ordered else None,
        second_ovr=ordered[1] if len(ordered) > 1 else None,
    )


def forces_goalie_need(profile: TeamGoalieProfile | None) -> bool:
    """Whether the mock should treat goalie as the primary positional need."""
    if profile is None or profile.count <= 0:
        return True
    if profile.count == 1:
        if profile.best_ovr is None or profile.best_ovr < _GOALIE_WEAK_STARTER_OVR:
            return True
        return False
    best = profile.best_ovr
    second = profile.second_ovr
    if best is None:
        return True
    if best >= _GOALIE_STRONG_STARTER_OVR and second is not None and second >= _GOALIE_OK_BACKUP_OVR:
        return False
    if best >= 54 and second is not None and second >= 46:
        return False
    if best < _GOALIE_WEAK_STARTER_OVR:
        return True
    if second is not None and second < _GOALIE_WEAK_BACKUP_OVR:
        return True
    return False


def _need_summary(counts: dict[str, int], goalie_profile: TeamGoalieProfile | None = None) -> str:
    base = f"F {counts.get('F', 0)}, D {counts.get('D', 0)}, G {counts.get('G', 0)}"
    if goalie_profile is None or goalie_profile.best_ovr is None:
        return base
    if goalie_profile.second_ovr is not None:
        return f"{base} · G {goalie_profile.best_ovr}/{goalie_profile.second_ovr}"
    return f"{base} · G {goalie_profile.best_ovr}"


def _skater_need_bucket(counts: dict[str, int]) -> str:
    targets = {"F": 14, "D": 7}
    return min(("F", "D"), key=lambda b: (counts.get(b, 0) / targets[b], counts.get(b, 0), b))


def _need_bucket(
    counts: dict[str, int],
    *,
    goalie_profile: TeamGoalieProfile | None = None,
) -> str:
    if forces_goalie_need(goalie_profile):
        return "G"
    targets = {"F": 14, "D": 7, "G": 2}
    return min(("F", "D", "G"), key=lambda b: (counts.get(b, 0) / targets[b], counts.get(b, 0), b))


def _roster_state(
    session: Session, team_ids: list[int]
) -> tuple[dict[int, dict[str, int]], dict[int, TeamGoalieProfile]]:
    counts = {int(tid): {"F": 0, "D": 0, "G": 0} for tid in team_ids}
    goalie_ovrs: dict[int, list[int]] = {int(tid): [] for tid in team_ids}
    if not team_ids:
        return counts, {}
    players = session.scalars(
        select(Player).where(
            Player.current_team_id.in_(team_ids),
            Player.retired.is_(False),
        )
    ).all()
    for player in players:
        tid = int(player.current_team_id or 0)
        if tid not in counts:
            continue
        bucket = _position_bucket(player)
        counts[tid][bucket] = counts[tid].get(bucket, 0) + 1
        if bucket == "G":
            ovr = _goalie_ovr(player)
            if ovr is not None:
                goalie_ovrs[tid].append(int(ovr))
    profiles = {tid: _goalie_profile_from_ovrs(goalie_ovrs[tid]) for tid in team_ids}
    return counts, profiles


def _original_pick_order(session: Session, season: Season | None) -> list[Team]:
    teams = draft_pick_teams_for_grid(session)
    by_id = {int(t.id): t for t in teams}
    if not season:
        return teams
    standings = list(
        session.scalars(
            select(TeamStanding).where(TeamStanding.season_id == int(season.id))
        ).all()
    )
    standings = [st for st in standings if int(st.team_id) in by_id]
    standings.sort(
        key=lambda st: (
            int(st.pts or 0),
            int(st.w or 0),
            int(st.gf or 0) - int(st.ga or 0),
            (by_id[int(st.team_id)].full_display_name() or "").casefold(),
        )
    )
    ordered: list[Team] = [by_id[int(st.team_id)] for st in standings]
    seen = {int(t.id) for t in ordered}
    ordered.extend(t for t in teams if int(t.id) not in seen)
    return ordered


def _ownership_lookup(
    site_session: Session,
    *,
    league_slug: str,
    draft_year: int,
) -> dict[tuple[int, int], int]:
    rows = list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership).where(
                TradeMarketDraftPickOwnership.league_slug == str(league_slug),
                TradeMarketDraftPickOwnership.draft_year == int(draft_year),
                TradeMarketDraftPickOwnership.round <= MOCK_DRAFT_ROUNDS,
            )
        ).all()
    )
    out: dict[tuple[int, int], int] = {}
    for row in rows:
        if row.owner_team_id is None:
            continue
        out[(int(row.original_team_fhm_id), int(row.round))] = int(row.owner_team_id)
    return out


def _should_take_goalie_now(
    pool: list[Player],
    rank_by_id: dict[int, int],
    *,
    round_no: int,
) -> bool:
    goalies = [p for p in pool if _position_bucket(p) == "G"]
    skaters = [p for p in pool if _position_bucket(p) != "G"]
    if not goalies:
        return False
    if not skaters:
        return True
    best_g = min(int(rank_by_id.get(int(p.id), 9999)) for p in goalies)
    best_s = min(int(rank_by_id.get(int(p.id), 9999)) for p in skaters)
    max_g_rank = _GOALIE_MAX_BOARD_RANK_BY_ROUND.get(int(round_no), 22)
    if best_g > max_g_rank:
        return False
    if best_s <= 5 and best_g - best_s >= 6:
        return False
    return True


def _choose_player(
    available: list[Player],
    rank_by_id: dict[int, int],
    need_bucket: str,
    *,
    counts: dict[str, int],
    round_no: int,
) -> Player | None:
    if not available:
        return None
    pool = available[:_NEED_WINDOW]
    effective_need = need_bucket
    if need_bucket == "G" and not _should_take_goalie_now(pool, rank_by_id, round_no=round_no):
        effective_need = _skater_need_bucket(counts)
    return min(
        pool,
        key=lambda p: (
            0 if _position_bucket(p) == effective_need else _NEED_WINDOW,
            int(rank_by_id.get(int(p.id), 9999)),
            (p.full_name or "").casefold(),
            int(p.id),
        ),
    )


def build_mock_draft(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    season: Season | None,
    draft_year: int,
    eligible_players: list[Player],
) -> list[MockDraftRow]:
    original_order = _original_pick_order(league_session, season)
    if not original_order or not eligible_players:
        return []
    team_by_id = {int(t.id): t for t in draft_pick_teams_for_grid(league_session)}
    owner_by_original_round = _ownership_lookup(
        site_session,
        league_slug=league_slug,
        draft_year=int(draft_year),
    )
    rank_by_id = {int(p.id): i for i, p in enumerate(eligible_players, start=1)}
    available = list(eligible_players)
    roster_counts, goalie_profiles = _roster_state(league_session, list(team_by_id))
    rows: list[MockDraftRow] = []
    overall = 1
    for rnd in range(1, MOCK_DRAFT_ROUNDS + 1):
        for round_pick, original_team in enumerate(original_order, start=1):
            original_fhm = _team_fhm_id(original_team)
            owner_id = (
                owner_by_original_round.get((original_fhm, rnd))
                if original_fhm is not None
                else None
            )
            owner_team = team_by_id.get(int(owner_id)) if owner_id else original_team
            owner_tid = int(owner_team.id)
            counts = roster_counts.setdefault(owner_tid, {"F": 0, "D": 0, "G": 0})
            goalie_profile = goalie_profiles.get(owner_tid)
            need = _need_bucket(counts, goalie_profile=goalie_profile)
            player = _choose_player(
                available,
                rank_by_id,
                need,
                counts=counts,
                round_no=rnd,
            )
            if player is None:
                return rows
            available.remove(player)
            bucket = _position_bucket(player)
            rows.append(
                MockDraftRow(
                    overall=overall,
                    round=rnd,
                    round_pick=round_pick,
                    original_team=original_team,
                    owner_team=owner_team,
                    player=player,
                    player_rank=int(rank_by_id.get(int(player.id), overall)),
                    need_bucket=need,
                    need_summary=_need_summary(counts, goalie_profile),
                    traded=int(owner_team.id) != int(original_team.id),
                )
            )
            counts[bucket] = counts.get(bucket, 0) + 1
            if bucket == "G":
                ovr = _goalie_ovr(player)
                if ovr is not None:
                    profile = goalie_profiles.get(owner_tid, _goalie_profile_from_ovrs([]))
                    ovrs = [x for x in (profile.best_ovr, profile.second_ovr) if x is not None]
                    ovrs.append(int(ovr))
                    goalie_profiles[owner_tid] = _goalie_profile_from_ovrs(ovrs)
            overall += 1
    return rows

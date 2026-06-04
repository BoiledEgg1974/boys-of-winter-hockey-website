"""Entertainment mock draft builder for the public Draft Eligible page."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Season, Team, TeamStanding
from app.services.draft_pick_ownership import draft_pick_teams_for_grid
from app.services.player_ratings_csv import player_positions_display_label
from app.site_models import TradeMarketDraftPickOwnership

MOCK_DRAFT_ROUNDS = 3
_NEED_WINDOW = 18


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


def _need_summary(counts: dict[str, int]) -> str:
    return f"F {counts.get('F', 0)}, D {counts.get('D', 0)}, G {counts.get('G', 0)}"


def _need_bucket(counts: dict[str, int]) -> str:
    if counts.get("G", 0) <= 2:
        return "G"
    targets = {"F": 14, "D": 7, "G": 2}
    return min(("F", "D", "G"), key=lambda b: (counts.get(b, 0) / targets[b], counts.get(b, 0), b))


def _roster_counts(session: Session, team_ids: list[int]) -> dict[int, dict[str, int]]:
    out = {int(tid): {"F": 0, "D": 0, "G": 0} for tid in team_ids}
    if not team_ids:
        return out
    players = session.scalars(
        select(Player).where(
            Player.current_team_id.in_(team_ids),
            Player.retired.is_(False),
        )
    ).all()
    for player in players:
        tid = int(player.current_team_id or 0)
        if tid not in out:
            continue
        bucket = _position_bucket(player)
        out[tid][bucket] = out[tid].get(bucket, 0) + 1
    return out


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


def _choose_player(
    available: list[Player],
    rank_by_id: dict[int, int],
    need_bucket: str,
) -> Player | None:
    if not available:
        return None
    pool = available[:_NEED_WINDOW]
    return min(
        pool,
        key=lambda p: (
            0 if _position_bucket(p) == need_bucket else _NEED_WINDOW,
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
    roster_counts = _roster_counts(league_session, list(team_by_id))
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
            counts = roster_counts.setdefault(int(owner_team.id), {"F": 0, "D": 0, "G": 0})
            need = _need_bucket(counts)
            player = _choose_player(available, rank_by_id, need)
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
                    need_summary=_need_summary(counts),
                    traded=int(owner_team.id) != int(original_team.id),
                )
            )
            counts[bucket] = counts.get(bucket, 0) + 1
            overall += 1
    return rows

"""JSON payload for team hover cards (standings rank, special teams, top roster OVR by position)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app, url_for
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, Team, TeamSeasonAggregate, TeamStanding
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.expansion_draft_state import player_is_defense, player_is_forward, player_is_goalie
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row, player_positions_display_label
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.season_team_logo_bundle import dashboard_team_logo_url
from app.services.seasons import season_age_reference_date, season_display_label
from app.services.standings import standings_for_season


def _player_ovr(pl: Player) -> int | None:
    rr = get_player_ratings_row(pl.fhm_player_id)
    return compute_player_overall_100(
        pl.overall_ability,
        pl.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(pl),
    )


def _best_in_category(players: list[Player], kind: str) -> Player | None:
    def pred(pl: Player) -> bool:
        if kind == "f":
            return player_is_forward(pl)
        if kind == "d":
            return player_is_defense(pl)
        return player_is_goalie(pl)

    best: Player | None = None
    best_o = -1
    for pl in players:
        if not pred(pl):
            continue
        o = _player_ovr(pl)
        if o is None:
            continue
        iv = int(o)
        if iv > best_o:
            best_o = iv
            best = pl
        elif iv == best_o and best is not None and pl.id < best.id:
            best = pl
        elif iv == best_o and best is None:
            best = pl
    return best


def _player_age(pl: Player, ref: date) -> int | None:
    bd = pl.birth_date
    if not bd:
        return None
    years = ref.year - bd.year
    if (ref.month, ref.day) < (bd.month, bd.day):
        years -= 1
    return years


def _pos_age_line(pl: Player, ref: date) -> str:
    pos = player_positions_display_label(pl)
    if pos and pos != "—" and " • " in pos:
        pos = pos.split(" • ")[0].strip()
    elif not pos or pos == "—":
        pos = ((pl.position or "") or "").strip().upper() or "—"
    age = _player_age(pl, ref)
    age_s = str(age) if age is not None else "—"
    return f"{pos} · Age {age_s}"


def _serialize_player(
    pl: Player | None,
    ref_date: date,
    *,
    role_label: str,
) -> dict[str, Any] | None:
    if pl is None:
        return None
    rr = get_player_ratings_row(pl.fhm_player_id)
    abi = fhm_abi_pot_float(pl.overall_ability)
    pot = fhm_abi_pot_float(pl.overall_potential)
    ovr = _player_ovr(pl)
    static_root = Path(current_app.root_path) / "static"
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    photo_url = url_for("static", filename=rel) if rel else ""
    return {
        "role": role_label,
        "name": pl.full_name,
        "pos_age": _pos_age_line(pl, ref_date),
        "ovr": ovr,
        "abi": round(abi, 1) if abi is not None else None,
        "pot": round(pot, 1) if pot is not None else None,
        "url": url_for("main.player_page", player_id=pl.id),
        "photo_url": photo_url,
    }


def build_team_hover_preview_payload(session: Session, team_slug: str, season: Any) -> dict[str, Any] | None:
    """Return JSON-serializable dict or None if team/season missing."""
    if not season:
        return None
    team = session.scalar(select(Team).where(Team.slug == team_slug.strip()).limit(1))
    if team is None:
        return None

    all_rows = standings_for_season(season)
    overall_rank: int | None = None
    standing: TeamStanding | None = None
    for idx, st in enumerate(all_rows, start=1):
        if st.team_id == team.id:
            overall_rank = idx
            standing = st
            break

    agg = session.scalar(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season.id,
            TeamSeasonAggregate.team_id == team.id,
            TeamSeasonAggregate.stat_segment == "rs",
        )
    )
    pp_pct = None
    pk_pct = None
    if agg and agg.pp_chances and agg.pp_chances > 0 and agg.pp_goals is not None:
        pp_pct = round(100.0 * float(agg.pp_goals) / float(agg.pp_chances), 1)
    if agg and agg.sh_chances and agg.sh_chances > 0 and agg.pk_goals_against is not None:
        pk_pct = round(100.0 - (100.0 * float(agg.pk_goals_against) / float(agg.sh_chances)), 1)

    league_ids = frozenset(bowl_nhl_league_ids(session))
    roster: list[Player] = []
    if team.fhm_league_id is not None and int(team.fhm_league_id) in league_ids:
        roster = list(
            session.scalars(
                select(Player)
                .options(joinedload(Player.current_team))
                .where(Player.retired.is_(False), Player.current_team_id == team.id)
            )
            .unique()
            .all()
        )

    ref_date = season_age_reference_date(season)
    top_f = _best_in_category(roster, "f")
    top_d = _best_in_category(roster, "d")
    top_g = _best_in_category(roster, "g")

    sy = getattr(season, "start_year", None)
    logo_url = dashboard_team_logo_url(team, int(sy) if sy is not None else None)

    conf = (standing.conference or "").strip() if standing else ""
    div_l = (standing.division or "").strip() if standing else ""
    conf_div = " · ".join(x for x in (conf, div_l) if x) or None

    w = l = t = otl = sow = sol = pts = gf = ga = 0
    streak = None
    if standing:
        w, l, t = int(standing.w or 0), int(standing.l or 0), int(standing.ties or 0)
        otl = int(standing.otl or 0)
        sow = int(standing.shootout_wins or 0)
        sol = int(standing.shootout_losses or 0)
        pts = int(standing.pts or 0)
        gf, ga = int(standing.gf or 0), int(standing.ga or 0)
        streak = (standing.streak or "").strip() or None

    players_out: list[dict[str, Any]] = []
    for role, pl, label in (
        ("forward", top_f, "Top forward"),
        ("defense", top_d, "Top defenseman"),
        ("goalie", top_g, "Top goalie"),
    ):
        ser = _serialize_player(pl, ref_date, role_label=label)
        if ser:
            ser["kind"] = role
            players_out.append(ser)

    return {
        "team_name": team.full_display_name(),
        "team_slug": team.slug,
        "abbreviation": (team.abbreviation or "").strip() or None,
        "logo_url": logo_url,
        "season_label": season_display_label(season),
        "conf_div": conf_div,
        "overall_rank": overall_rank,
        "n_teams": len(all_rows) if all_rows else None,
        "record": {"w": w, "l": l, "t": t, "otl": otl, "sow": sow, "sol": sol, "pts": pts, "gf": gf, "ga": ga},
        "streak": streak,
        "pp_pct": pp_pct,
        "pk_pct": pk_pct,
        "team_url": url_for("main.team_page", slug=team.slug),
        "players": players_out,
    }

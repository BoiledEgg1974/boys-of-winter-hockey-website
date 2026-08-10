"""Build side-by-side player comparison payloads for the Player Comparison page."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app, url_for
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import Config
from app.logo_urls import team_logo_url_for_team
from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    Player,
    PlayerContract,
    PlayerGoalieStat,
    PlayerSkaterStat,
    Season,
    Team,
)
from app.services.player_ability_potential import fhm_abi_pot_float
from app.services.player_analytics import build_player_analytics_panel
from app.services.player_career_bowl_lines import load_player_bowl_career_table_lines
from app.services.player_career_totals import goalie_career_lines_totals, skater_career_lines_totals
from app.services.player_contract_csv import (
    contract_final_season_label_from_remaining,
    contract_years_remaining_major,
    player_contract_salary_by_season,
)
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_overall_score import build_overall_cell_map_from_players
from app.services.player_percentiles import build_player_analytics_card
from app.services.player_rating_avgs import (
    _float_cell,
    goalie_category_averages,
    skater_category_averages,
)
from app.services.player_ratings_csv import (
    get_player_ratings_row,
    player_positions_display_label,
    position_ratings_display_list,
)
from app.services.roster_team import main_league_roster_team
from app.services.seasons import get_current_season, season_age_reference_date, season_display_label

_SKATER_SECTION_DEFS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Offense",
        (
            ("Screening", "screening"),
            ("Getting open", "getting_open"),
            ("Passing", "passing"),
            ("Puck handling", "puck_handling"),
            ("Shooting accuracy", "shooting_accuracy"),
            ("Shooting range", "shooting_range"),
            ("Offensive read", "offensive_read"),
        ),
    ),
    (
        "Defense",
        (
            ("Checking", "checking"),
            ("Faceoffs", "faceoffs"),
            ("Hitting", "hitting"),
            ("Positioning", "positioning"),
            ("Shot blocking", "shot_blocking"),
            ("Stickchecking", "stickchecking"),
            ("Defensive read", "defensive_read"),
        ),
    ),
    (
        "Physical",
        (
            ("Acceleration", "acceleration"),
            ("Agility", "agility"),
            ("Balance", "balance"),
            ("Speed", "speed"),
            ("Stamina", "stamina"),
            ("Strength", "strength"),
            ("Fighting", "fighting"),
        ),
    ),
    (
        "Mental",
        (
            ("Aggression", "aggression"),
            ("Bravery", "bravery"),
            ("Determination", "determination"),
            ("Team Player", "teamplayer"),
            ("Leadership", "leadership"),
            ("Temperament", "temperament"),
            ("Professionalism", "professionalism"),
        ),
    ),
)

_GOALIE_SECTION_DEFS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Goalie",
        (
            ("Positioning", "g_positioning"),
            ("Passing", "g_passing"),
            ("Pokecheck", "g_pokecheck"),
            ("Blocker", "blocker"),
            ("Glove", "glove"),
            ("Rebound", "rebound"),
            ("Recovery", "recovery"),
            ("Puckhandling", "g_puckhandling"),
            ("Low Shots", "low_shots"),
            ("Skating", "g_skating"),
            ("Reflexes", "reflexes"),
        ),
    ),
    (
        "Mental",
        (
            ("Aggression", "aggression"),
            ("Mental Toughness", "mental_toughness"),
            ("Determination", "determination"),
            ("Team Player", "teamplayer"),
            ("Leadership", "leadership"),
            ("Stamina", "goalie_stamina"),
            ("Professionalism", "professionalism"),
        ),
    ),
)

def _player_age_years(birth: date | None, as_of: date | None = None) -> int | None:
    if birth is None:
        return None
    ref = as_of if as_of is not None else date.today()
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


def _is_goalie(player: Player) -> bool:
    return (player.position or "").strip().upper().startswith("G")


def _fmt_toi(secs: int | None) -> str | None:
    if secs is None:
        return None
    return f"{secs // 60}:{secs % 60:02d}"


def _avg_goalie_toi(minutes_played: int | None, gp: int) -> str | None:
    if minutes_played is None or gp <= 0:
        return None
    try:
        secs = int(round(float(minutes_played) * 60.0 / float(gp)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return _fmt_toi(secs)


def _rating_rows(rr: dict | None, pairs: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, key in pairs:
        out.append({"label": label, "key": key, "value": _float_cell(rr.get(key)) if rr else None})
    return out


def _attribute_sections(is_goalie: bool, rr: dict | None) -> list[dict[str, Any]]:
    defs = _GOALIE_SECTION_DEFS if is_goalie else _SKATER_SECTION_DEFS
    return [{"title": title, "rows": _rating_rows(rr, pairs)} for title, pairs in defs]


def _latest_rs_season_stats(session, player_id: int, is_goalie: bool) -> dict[str, Any] | None:
    if is_goalie:
        row = session.execute(
            select(PlayerGoalieStat, Season)
            .join(Season, PlayerGoalieStat.season_id == Season.id)
            .where(PlayerGoalieStat.player_id == player_id, PlayerGoalieStat.stat_segment == "rs")
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
            .limit(1)
        ).first()
        if not row:
            return None
        st, sn = row
        gp = int(st.gp or 0)
        wins = int(st.wins or 0)
        losses = int(st.losses or 0)
        otl = int(st.otl or 0)
        sa = int(st.sa or 0)
        ga = int(st.ga or 0)
        sv = sa - ga if sa else None
        sv_pct = float(st.sv_pct) if st.sv_pct is not None else None
        if sv_pct is None and sa > 0 and sv is not None:
            sv_pct = float(sv) / float(sa)
        gaa = float(st.gaa) if st.gaa is not None else None
        mp = st.minutes_played
        if gaa is None and mp and float(mp) > 0:
            gaa = float(ga) * 60.0 / float(mp)
        gr = float(st.game_rating) if st.game_rating is not None else None
        return {
            "role": "goalie",
            "season": season_display_label(sn),
            "gp": gp,
            "record": f"{wins}-{losses}-{otl}",
            "wins": wins,
            "losses": losses,
            "otl": otl,
            "gaa": round(gaa, 2) if gaa is not None else None,
            "sv_pct": round(sv_pct, 3) if sv_pct is not None else None,
            "gr": round(gr, 1) if gr is not None else None,
            "so": int(st.so or 0),
            "toi_pg": _avg_goalie_toi(mp, gp),
            "sa": sa,
            "ga": ga,
        }

    row = session.execute(
        select(PlayerSkaterStat, Season)
        .join(Season, PlayerSkaterStat.season_id == Season.id)
        .where(PlayerSkaterStat.player_id == player_id, PlayerSkaterStat.stat_segment == "rs")
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    st, sn = row
    gp = int(st.gp or 0)
    goals = int(st.goals or 0)
    assists = int(st.assists or 0)
    pts = int(st.points) if st.points is not None else goals + assists
    toi_pg = None
    if st.toi_seconds and gp > 0:
        toi_pg = _fmt_toi(int(round(st.toi_seconds / gp)))
    gr = float(st.game_rating) if st.game_rating is not None else None
    return {
        "role": "skater",
        "season": season_display_label(sn),
        "gp": gp,
        "goals": goals,
        "assists": assists,
        "points": pts,
        "plus_minus": st.plus_minus,
        "pim": int(st.pim or 0),
        "shots": int(st.shots) if st.shots is not None else None,
        "hits": int(st.hits) if st.hits is not None else None,
        "blocked_shots": int(st.blocked_shots) if st.blocked_shots is not None else None,
        "toi_pg": toi_pg,
        "gr": round(gr, 1) if gr is not None else None,
    }


def _team_city_nick(team: Team | None) -> str:
    if not team:
        return ""
    if team.city:
        nick = team.nickname or team.name or ""
        return f"{team.city} {nick}".strip()
    return (team.name or "").strip()


def build_player_compare_side(session, player_id: int, season: Season | None = None) -> dict[str, Any] | None:
    """Load one player's comparison payload, or None if the id is missing."""
    player = session.get(Player, player_id)
    if not player:
        return None

    season = season or get_current_season()
    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    is_goalie = _is_goalie(player)
    ratings_row = get_player_ratings_row(player.fhm_player_id)

    career_rs_sk, _career_po_sk, career_rs_gk, _career_po_gk = load_player_bowl_career_table_lines(
        session, player.id
    )
    has_sk_career = bool(career_rs_sk)
    has_gk_career = bool(career_rs_gk)
    use_goalie_stats = is_goalie or (has_gk_career and not has_sk_career)

    current_team = session.get(Team, player.current_team_id) if player.current_team_id else None
    contract = session.scalars(
        select(PlayerContract).where(PlayerContract.player_id == player.id).limit(1)
    ).first()
    contract_team = None
    if contract and contract.fhm_team_id is not None:
        contract_team = session.scalars(
            select(Team).where(Team.fhm_team_id == str(contract.fhm_team_id)).limit(1)
        ).first()
    accent_team = contract_team or current_team
    roster_header_team = main_league_roster_team(contract_team, current_team)

    abi = float(player.overall_ability) if player.overall_ability is not None else None
    pot = float(player.overall_potential) if player.overall_potential is not None else None
    if ratings_row:
        if abi is None:
            abi = fhm_abi_pot_float(ratings_row.get("ability"))
        if pot is None:
            pot = fhm_abi_pot_float(ratings_row.get("potential"))

    ovr_map = build_overall_cell_map_from_players(session, [player])
    ova = ovr_map.get(player.id) or {}
    ovr_score = ova.get("score")
    ovr_int = int(ovr_score) if ovr_score is not None else None

    season_start_year = season.start_year if season else None
    contract_years_left = contract_years_remaining_major(player.fhm_player_id, season_start_year, raw_dir)
    contract_through = contract_final_season_label_from_remaining(contract_years_left, season_start_year)
    contract_salary_by_season = (
        player_contract_salary_by_season(player.fhm_player_id, raw_dir) if contract else []
    )

    position_ratings = position_ratings_display_list(ratings_row) if ratings_row else []
    if use_goalie_stats:
        category_avgs = goalie_category_averages(ratings_row)
    else:
        category_avgs = skater_category_averages(ratings_row)

    if player.retired:
        game_log: list[Any] = []
    elif use_goalie_stats:
        game_log = list(
            session.scalars(
                select(GameGoalieStat)
                .options(
                    joinedload(GameGoalieStat.game).joinedload(Game.home_team),
                    joinedload(GameGoalieStat.game).joinedload(Game.away_team),
                )
                .join(Game, GameGoalieStat.game_id == Game.id)
                .where(GameGoalieStat.player_id == player.id)
                .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
                .limit(20)
            ).all()
        )
    else:
        game_log = list(
            session.scalars(
                select(GameSkaterStat)
                .options(
                    joinedload(GameSkaterStat.game).joinedload(Game.home_team),
                    joinedload(GameSkaterStat.game).joinedload(Game.away_team),
                )
                .join(Game, GameSkaterStat.game_id == Game.id)
                .where(GameSkaterStat.player_id == player.id)
                .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
                .limit(20)
            ).all()
        )

    analytics = build_player_analytics_panel(
        session,
        player,
        ratings_row=ratings_row,
        season=season,
        is_goalie=is_goalie,
        use_goalie_game_log=use_goalie_stats,
        game_log=game_log,
        position_ratings_rows=position_ratings,
        hero_abi=abi,
        hero_pot=pot,
        player_ovr=ovr_int,
        season_trend_rows=[],
        goalie_trend_mode=use_goalie_stats,
        team_context=roster_header_team,
        retired=bool(player.retired),
    )

    static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
    headshot_rel = resolve_player_headshot_static_filename(
        static_root,
        player,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    photo_url = None
    if headshot_rel:
        try:
            photo_url = url_for("static", filename=headshot_rel)
        except RuntimeError:
            root = str(current_app.config.get("APPLICATION_ROOT") or "").rstrip("/")
            photo_url = f"{root}/static/{headshot_rel}"
    analytics_card = build_player_analytics_card(
        session,
        player,
        season,
        is_goalie=is_goalie,
        ratings_row=ratings_row,
        contract=contract,
        player_age=_player_age_years(player.birth_date, season_age_reference_date(season)),
        role_title=analytics.get("role_title") if analytics.get("enabled") else None,
        team=accent_team or current_team,
        years_left=contract_years_left if not player.retired else None,
        photo_url=photo_url,
        team_logo_url=team_logo_url_for_team(accent_team or current_team)
        if (accent_team or current_team)
        else None,
        raw_dir=raw_dir,
        retired=bool(player.retired),
    )

    if use_goalie_stats:
        career_totals = goalie_career_lines_totals(career_rs_gk) if career_rs_gk else None
        career_role = "goalie"
    else:
        career_totals = skater_career_lines_totals(career_rs_sk) if career_rs_sk else None
        career_role = "skater"

    latest_stats = None if player.retired else _latest_rs_season_stats(session, player.id, use_goalie_stats)

    return {
        "player": player,
        "is_goalie": is_goalie,
        "use_goalie_stats": use_goalie_stats,
        "age": _player_age_years(player.birth_date, season_age_reference_date(season)),
        "abi": abi,
        "pot": pot,
        "ovr": ovr_int,
        "ovr_cell": ova,
        "position_label": player_positions_display_label(player),
        "position_ratings": position_ratings,
        "attr_sections": _attribute_sections(use_goalie_stats, ratings_row),
        "category_avgs": category_avgs,
        "ratings_row": ratings_row,
        "contract": contract,
        "contract_team": contract_team,
        "contract_team_name": _team_city_nick(contract_team),
        "current_team": current_team,
        "roster_header_team": roster_header_team,
        "accent_team": accent_team,
        "contract_years_left": contract_years_left,
        "contract_through_season": contract_through,
        "contract_salary_by_season": contract_salary_by_season,
        "latest_season_stats": latest_stats,
        "career_totals": career_totals,
        "career_role": career_role,
        "analytics": analytics,
        "analytics_card": analytics_card,
        "role_title": analytics.get("role_title") if analytics.get("enabled") else None,
        "photo_url": photo_url,
        "team_logo_url": team_logo_url_for_team(accent_team or current_team)
        if (accent_team or current_team)
        else None,
    }


def merge_compare_attr_sections(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Union attribute sections/rows from both sides for the mirrored spine."""
    left_secs = (left or {}).get("attr_sections") or []
    right_secs = (right or {}).get("attr_sections") or []
    left_by_title = {s["title"]: s for s in left_secs}
    right_by_title = {s["title"]: s for s in right_secs}
    titles: list[str] = []
    for s in left_secs + right_secs:
        if s["title"] not in titles:
            titles.append(s["title"])

    merged: list[dict[str, Any]] = []
    for title in titles:
        lsec = left_by_title.get(title)
        rsec = right_by_title.get(title)
        left_rows = {r["label"]: r for r in (lsec["rows"] if lsec else [])}
        right_rows = {r["label"]: r for r in (rsec["rows"] if rsec else [])}
        labels: list[str] = []
        for r in (lsec["rows"] if lsec else []) + (rsec["rows"] if rsec else []):
            if r["label"] not in labels:
                labels.append(r["label"])
        merged.append(
            {
                "title": title,
                "rows": [
                    {
                        "label": lab,
                        "left": (left_rows.get(lab) or {}).get("value"),
                        "right": (right_rows.get(lab) or {}).get("value"),
                    }
                    for lab in labels
                ],
            }
        )
    return merged


def merge_compare_position_ratings(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> list[dict[str, Any]]:
    left_rows = (left or {}).get("position_ratings") or []
    right_rows = (right or {}).get("position_ratings") or []
    left_by = {r["abbr"]: r for r in left_rows}
    right_by = {r["abbr"]: r for r in right_rows}
    abbrs: list[str] = []
    labels: dict[str, str] = {}
    for r in left_rows + right_rows:
        abbr = str(r.get("abbr") or "")
        if not abbr or abbr in abbrs:
            continue
        abbrs.append(abbr)
        labels[abbr] = str(r.get("label") or abbr)
    return [
        {
            "abbr": abbr,
            "label": labels[abbr],
            "left": (left_by.get(abbr) or {}).get("value"),
            "right": (right_by.get(abbr) or {}).get("value"),
            "left_primary": bool((left_by.get(abbr) or {}).get("is_primary")),
            "right_primary": bool((right_by.get(abbr) or {}).get("is_primary")),
        }
        for abbr in abbrs
    ]

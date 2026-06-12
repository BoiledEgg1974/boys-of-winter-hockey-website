"""Upper / Lower tier resolution for BOWL-Relegation (bowl-fantasy mount)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from flask import current_app
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Game, LeagueMeta, Team, TeamStanding
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.playoff_seeding import standing_sort_key

RelegationScope = Literal["combined", "upper", "lower"]
RelegationTier = Literal["upper", "lower"]

_MAIN_LEAGUE_EXCLUDE = ("minor", "ahl", "ihl", "junior", "college", "european", "prospect")


@dataclass(frozen=True)
class RelegationTierConfig:
    """Resolved Upper/Lower split from imported FHM metadata."""

    mode: Literal["league_id", "conference_id"]
    upper_league_ids: frozenset[int]
    lower_league_ids: frozenset[int]
    upper_conference_ids: frozenset[int]
    lower_conference_ids: frozenset[int]
    upper_label: str
    lower_label: str
    combined_league_ids: tuple[int, ...]


def is_relegation_league(league_slug: str | None) -> bool:
    return (league_slug or "").strip() == "bowl-fantasy"


def relegation_features_enabled(league_slug: str | None = None) -> bool:
    """True when Upper/Lower scoped views and movement watch should be live."""
    slug = (league_slug or "").strip()
    if not is_relegation_league(slug):
        return False
    try:
        flag = current_app.config.get("RELEGATION_SPLIT_ACTIVE")
        if flag is not None:
            return bool(flag)
    except RuntimeError:
        pass
    from app.config import relegation_split_active

    return relegation_split_active(slug)


def relegation_under_construction(league_slug: str | None = None) -> bool:
    """BOWL-Relegation site is mounted but the Upper/Lower split is not live yet."""
    slug = (league_slug or "").strip()
    if not slug:
        try:
            slug = str(current_app.config.get("LEAGUE_SLUG") or "")
        except RuntimeError:
            return False
    return is_relegation_league(slug) and not relegation_features_enabled(slug)


def normalize_relegation_scope(raw: str | None) -> RelegationScope:
    s = (raw or "combined").strip().lower()
    if s in ("upper", "lower"):
        return s  # type: ignore[return-value]
    return "combined"


def _tier_from_league_name(name: str | None) -> RelegationTier | None:
    n = (name or "").lower()
    if any(k in n for k in ("upper", "premier", "top tier", "top league")):
        return "upper"
    if any(k in n for k in ("lower", "relegat", "second tier", "second league")):
        return "lower"
    return None


def _is_main_bowl_league_meta(row: LeagueMeta) -> bool:
    name = (row.name or "").lower()
    if any(k in name for k in _MAIN_LEAGUE_EXCLUDE):
        return False
    if int(row.fhm_league_id) == 0:
        return True
    abbr = (row.abbreviation or "").upper()
    if abbr in ("BOWL", "NHL"):
        return True
    if "bowl" in name and "fantasy" in name:
        return True
    if _tier_from_league_name(name):
        return True
    return False


def _load_conference_names(raw_import_dir: Path) -> dict[int, str]:
    conf_csv = raw_import_dir / "conferences.csv"
    if not conf_csv.is_file():
        return {}
    out: dict[int, str] = {}
    try:
        with conf_csv.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                lid = (row.get("League Id") or row.get("league_id") or "").strip()
                if lid and lid != "0":
                    continue
                rid = (row.get("Conference Id") or row.get("conference_id") or "").strip()
                nm = (row.get("Name") or row.get("name") or "").strip()
                if not rid or not nm:
                    continue
                try:
                    cid = int(rid)
                except ValueError:
                    continue
                label = nm.removesuffix(" Conference").strip()
                out[cid] = label or nm
    except OSError:
        return {}
    return out


def get_tier_config(session: Session, *, raw_import_dir: Path | None = None) -> RelegationTierConfig:
    """Resolve Upper/Lower tiers from LeagueMeta and team conference membership."""
    combined_ids = bowl_nhl_league_ids(session)
    metas = list(
        session.scalars(
            select(LeagueMeta).where(LeagueMeta.fhm_league_id.in_(combined_ids) if combined_ids else True)
        ).all()
    )
    main_metas = [m for m in metas if _is_main_bowl_league_meta(m)]

    upper_ids: set[int] = set()
    lower_ids: set[int] = set()
    for m in main_metas:
        tier = _tier_from_league_name(m.name)
        if tier == "upper":
            upper_ids.add(int(m.fhm_league_id))
        elif tier == "lower":
            lower_ids.add(int(m.fhm_league_id))

    if upper_ids and lower_ids:
        upper_label = next(
            (m.name for m in main_metas if int(m.fhm_league_id) in upper_ids),
            "Upper League",
        )
        lower_label = next(
            (m.name for m in main_metas if int(m.fhm_league_id) in lower_ids),
            "Lower League",
        )
        return RelegationTierConfig(
            mode="league_id",
            upper_league_ids=frozenset(upper_ids),
            lower_league_ids=frozenset(lower_ids),
            upper_conference_ids=frozenset(),
            lower_conference_ids=frozenset(),
            upper_label=upper_label,
            lower_label=lower_label,
            combined_league_ids=combined_ids,
        )

    # Conference fallback: two conferences among main-league teams (current BOWL-Relegation export).
    if raw_import_dir is None:
        try:
            raw_import_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR", "")))
        except RuntimeError:
            raw_import_dir = Path(".")
    conf_names = _load_conference_names(raw_import_dir)

    main_teams = list(
        session.scalars(
            select(Team).where(
                or_(Team.fhm_league_id.is_(None), Team.fhm_league_id.in_(combined_ids))
            )
        ).all()
    )
    conf_ids = sorted(
        {
            int(t.fhm_conference_id)
            for t in main_teams
            if t.fhm_conference_id is not None
        }
    )
    if len(conf_ids) < 2:
        conf_ids = [0, 1]

    upper_conf = conf_ids[0]
    lower_conf = conf_ids[1]
    upper_label = conf_names.get(upper_conf) or "Upper League"
    lower_label = conf_names.get(lower_conf) or "Lower League"
    if not upper_label.lower().endswith("league"):
        upper_label = f"{upper_label} League"
    if not lower_label.lower().endswith("league"):
        lower_label = f"{lower_label} League"

    return RelegationTierConfig(
        mode="conference_id",
        upper_league_ids=frozenset(),
        lower_league_ids=frozenset(),
        upper_conference_ids=frozenset({upper_conf}),
        lower_conference_ids=frozenset({lower_conf}),
        upper_label=upper_label,
        lower_label=lower_label,
        combined_league_ids=combined_ids,
    )


def team_tier(team: Team | None, config: RelegationTierConfig) -> RelegationTier | None:
    if team is None:
        return None
    if config.mode == "league_id":
        lid = team.fhm_league_id
        if lid is None:
            lid = 0
        ilid = int(lid)
        if ilid in config.upper_league_ids:
            return "upper"
        if ilid in config.lower_league_ids:
            return "lower"
        return None
    cid = team.fhm_conference_id
    if cid is None:
        return None
    icid = int(cid)
    if icid in config.upper_conference_ids:
        return "upper"
    if icid in config.lower_conference_ids:
        return "lower"
    return None


def team_matches_scope(
    team: Team | None,
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> bool:
    if scope == "combined":
        return True
    tier = team_tier(team, config)
    return tier == scope


def filter_teams_by_scope(
    teams: list[Team],
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> list[Team]:
    if scope == "combined":
        return teams
    return [t for t in teams if team_matches_scope(t, scope, config)]


def filter_standings_by_scope(
    rows: list[TeamStanding],
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> list[TeamStanding]:
    if scope == "combined":
        return rows
    return [st for st in rows if team_matches_scope(st.team, scope, config)]


def filter_games_by_scope(
    games: list[Game],
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> list[Game]:
    if scope == "combined":
        return games

    def _ok(g: Game) -> bool:
        ht = g.home_team if hasattr(g, "home_team") and g.home_team is not None else None
        at = g.away_team if hasattr(g, "away_team") and g.away_team is not None else None
        if ht is None and g.home_team_id:
            ht = None
        if at is None and g.away_team_id:
            at = None
        return team_matches_scope(ht, scope, config) and team_matches_scope(at, scope, config)

    return [g for g in games if _ok(g)]


def scope_league_ids_for_records(
    session: Session,
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> tuple[int, ...]:
    if scope == "combined":
        return config.combined_league_ids
    if config.mode == "league_id":
        ids = config.upper_league_ids if scope == "upper" else config.lower_league_ids
        return tuple(sorted(ids)) if ids else config.combined_league_ids
    return config.combined_league_ids


def scope_heading(scope: RelegationScope, config: RelegationTierConfig) -> str:
    if scope == "upper":
        return config.upper_label
    if scope == "lower":
        return config.lower_label
    return "Combined League"


def scope_explanation(scope: RelegationScope, config: RelegationTierConfig) -> str:
    if scope == "combined":
        return (
            "All Upper and Lower teams together — same player pool, shared draft, "
            "and combined all-time records."
        )
    if scope == "upper":
        return (
            f"{config.upper_label} only. Last place is relegated to the Lower League "
            "after the season playoffs."
        )
    return (
        f"{config.lower_label} only. The playoff champion is promoted to the Upper League "
        "after the season playoffs."
    )


def _count_remaining_rs_games(session: Session, season_id: int, team_id: int) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(Game)
            .where(
                Game.season_id == season_id,
                Game.status != "final",
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
        )
        or 0
    )


def _lower_playoff_leader(
    session: Session,
    season_id: int,
    lower_rows: list[TeamStanding],
    config: RelegationTierConfig,
) -> TeamStanding | None:
    from app.services.playoff_bracket import _current_postseason_games

    games = list(
        session.scalars(
            select(Game)
            .options(joinedload(Game.home_team), joinedload(Game.away_team))
            .where(Game.season_id == season_id)
        ).all()
    )
    playoff = _current_postseason_games(games)
    lower_playoff = [
        g
        for g in playoff
        if team_matches_scope(g.home_team, "lower", config)
        and team_matches_scope(g.away_team, "lower", config)
    ]
    if not lower_playoff:
        return lower_rows[0] if lower_rows else None

    wins: dict[int, int] = {}
    for g in lower_playoff:
        if (g.status or "").lower() != "final":
            continue
        if g.home_score is None or g.away_score is None:
            continue
        if g.home_score > g.away_score:
            wins[g.home_team_id] = wins.get(g.home_team_id, 0) + 1
        elif g.away_score > g.home_score:
            wins[g.away_team_id] = wins.get(g.away_team_id, 0) + 1
    if not wins:
        return lower_rows[0] if lower_rows else None
    leader_id = max(wins.items(), key=lambda kv: kv[1])[0]
    for st in lower_rows:
        if st.team_id == leader_id:
            return st
    return lower_rows[0] if lower_rows else None


def build_movement_watch(
    session: Session,
    season_id: int | None,
    config: RelegationTierConfig,
) -> dict[str, object]:
    """Danger zone (Upper last) and promotion watch (Lower leader/champion)."""
    empty: dict[str, object] = {
        "relegation_danger": None,
        "promotion_watch": None,
        "upper_standings_top": [],
        "lower_standings_top": [],
    }
    if season_id is None:
        return empty

    rows = list(
        session.scalars(
            select(TeamStanding)
            .options(joinedload(TeamStanding.team))
            .where(TeamStanding.season_id == season_id)
        ).all()
    )
    upper_rows = sorted(
        [st for st in rows if team_tier(st.team, config) == "upper"],
        key=standing_sort_key,
        reverse=True,
    )
    lower_rows = sorted(
        [st for st in rows if team_tier(st.team, config) == "lower"],
        key=standing_sort_key,
        reverse=True,
    )

    relegation_danger = None
    if upper_rows:
        last = upper_rows[-1]
        safety = upper_rows[-2] if len(upper_rows) >= 2 else None
        gap = None
        if safety is not None:
            gap = int(safety.pts or 0) - int(last.pts or 0)
        relegation_danger = {
            "team": last.team,
            "points": int(last.pts or 0),
            "rank": len(upper_rows),
            "gap_to_safety": gap,
            "remaining_games": _count_remaining_rs_games(session, season_id, int(last.team_id)),
        }

    promotion_st = _lower_playoff_leader(session, season_id, lower_rows, config)
    promotion_watch = None
    if promotion_st and promotion_st.team:
        promotion_watch = {
            "team": promotion_st.team,
            "points": int(promotion_st.pts or 0),
            "rank": lower_rows.index(promotion_st) + 1 if promotion_st in lower_rows else 1,
            "note": "Playoff bracket leader" if lower_rows else "Standings leader",
        }

    def _mini(st: TeamStanding) -> dict[str, object]:
        return {
            "team": st.team,
            "points": int(st.pts or 0),
            "gp": int(st.standing_gp_display() or 0),
            "rank": upper_rows.index(st) + 1 if st in upper_rows else lower_rows.index(st) + 1,
        }

    return {
        "relegation_danger": relegation_danger,
        "promotion_watch": promotion_watch,
        "upper_standings_top": [_mini(st) for st in upper_rows[:3]],
        "lower_standings_top": [_mini(st) for st in lower_rows[:3]],
        "upper_label": config.upper_label,
        "lower_label": config.lower_label,
    }


def _team_card(team: Team | None, logo_url_fn) -> dict[str, str] | None:
    if team is None:
        return None
    return {
        "name": team.full_display_name(),
        "slug": team.slug or "",
        "abbr": team.abbreviation or "",
        "logo_url": str(logo_url_fn(team) or ""),
    }


def serialize_movement_watch(
    movement: dict[str, object],
    *,
    logo_url_fn,
) -> dict[str, object]:
    danger = movement.get("relegation_danger")
    promo = movement.get("promotion_watch")
    out_danger = None
    if isinstance(danger, dict):
        team = danger.get("team")
        out_danger = {
            "team": _team_card(team if isinstance(team, Team) else None, logo_url_fn),
            "points": danger.get("points"),
            "rank": danger.get("rank"),
            "gap_to_safety": danger.get("gap_to_safety"),
            "remaining_games": danger.get("remaining_games"),
        }
    out_promo = None
    if isinstance(promo, dict):
        team = promo.get("team")
        out_promo = {
            "team": _team_card(team if isinstance(team, Team) else None, logo_url_fn),
            "points": promo.get("points"),
            "rank": promo.get("rank"),
            "note": promo.get("note"),
        }

    def _mini(rows: object) -> list[dict[str, object]]:
        if not isinstance(rows, list):
            return []
        out: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            team = row.get("team")
            out.append(
                {
                    "team": _team_card(team if isinstance(team, Team) else None, logo_url_fn),
                    "points": row.get("points"),
                    "gp": row.get("gp"),
                    "rank": row.get("rank"),
                }
            )
        return out

    return {
        "relegation_danger": out_danger,
        "promotion_watch": out_promo,
        "upper_standings_top": _mini(movement.get("upper_standings_top")),
        "lower_standings_top": _mini(movement.get("lower_standings_top")),
        "upper_label": movement.get("upper_label"),
        "lower_label": movement.get("lower_label"),
    }


def build_relegation_overview_payload(
    session: Session,
    season_id: int | None,
    config: RelegationTierConfig,
    *,
    logo_url_fn=None,
) -> dict[str, object]:
    movement = build_movement_watch(session, season_id, config)
    payload: dict[str, object] = {
        "upper_label": config.upper_label,
        "lower_label": config.lower_label,
        "mode": config.mode,
        "movement_watch": movement,
        "rules": {
            "relegation": "Last place in the Upper League moves down after playoffs.",
            "promotion": "Lower League playoff champion moves up after playoffs.",
        },
    }
    if logo_url_fn is not None:
        payload["movement_watch"] = serialize_movement_watch(movement, logo_url_fn=logo_url_fn)
    return payload


def team_fhm_ids_for_scope(
    session: Session,
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> frozenset[str] | None:
    if scope == "combined":
        return None
    teams = filter_teams_by_scope(
        list(session.scalars(select(Team)).all()),
        scope,
        config,
    )
    ids = frozenset(str(t.fhm_team_id) for t in teams if t.fhm_team_id)
    return ids if ids else frozenset({"__none__"})


def team_ids_for_scope(
    session: Session,
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> frozenset[int] | None:
    if scope == "combined":
        return None
    teams = filter_teams_by_scope(
        list(session.scalars(select(Team)).all()),
        scope,
        config,
    )
    return frozenset(int(t.id) for t in teams)


def filter_playoff_bracket_by_scope(
    payload: dict[str, object],
    scope: RelegationScope,
    config: RelegationTierConfig,
    *,
    teams_by_id: dict[int, Team] | None = None,
) -> dict[str, object]:
    if scope == "combined":
        return payload

    def _series_ok(series: object | None) -> bool:
        if not series or not isinstance(series, dict):
            return series is None
        for side in ("home", "away"):
            tid = series.get(f"{side}_team_id")
            if tid is None:
                team = series.get(f"{side}_team")
                if team is not None:
                    tid = getattr(team, "id", None)
            if tid is None:
                return False
            team = teams_by_id.get(int(tid)) if teams_by_id else None
            if team is None and teams_by_id is None:
                continue
            if not team_matches_scope(team, scope, config):
                return False
        return True

    out = dict(payload)
    for key in (
        "first_round",
        "second_round",
        "conference_finals",
        "quarterfinals",
        "semifinals",
        "rounds",
    ):
        val = out.get(key)
        if isinstance(val, list):
            out[key] = [s for s in val if _series_ok(s)]
    champ = out.get("championship")
    if champ and isinstance(champ, dict) and not _series_ok(champ):
        out["championship"] = None
    return out


def filter_history_awards_by_scope(
    awards: list[object],
    scope: RelegationScope,
    config: RelegationTierConfig,
) -> list[object]:
    if scope == "combined":
        return awards
    out: list[object] = []
    for a in awards:
        team = getattr(a, "team", None)
        if team is None and getattr(a, "team_id", None):
            continue
        if team_matches_scope(team, scope, config):
            out.append(a)
    return out

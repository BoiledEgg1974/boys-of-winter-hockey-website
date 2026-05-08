"""NHL/BOWL draft history: years, picks, and BOWL+NHL career totals from career lines."""
from __future__ import annotations

import csv
import math
from itertools import groupby
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Draft, DraftPick, Player, PlayerGoalieCareerLine, PlayerSkaterCareerLine
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.roster_team import main_league_roster_team

# All career_line career_source values used by FHM imports (active + retired, every segment).
CAREER_SOURCES: tuple[str, ...] = (
    "rs",
    "ps",
    "po",
    "retired_rs",
    "retired_ps",
    "retired_po",
)

# SQLite SQLITE_MAX_VARIABLE_NUMBER is often 999; keep IN clauses smaller.
_CAREER_QUERY_CHUNK = 400


def nhl_bowl_draft_clause():
    """SQL filter: draft event label is NHL or BOWL sim league."""
    return or_(Draft.label.ilike("%NHL%"), Draft.label.ilike("%BOWL%"))


def fetch_nhl_bowl_draft_years(session: Session) -> list[int]:
    rows = session.scalars(
        select(DraftPick.draft_year)
        .join(Draft)
        .where(DraftPick.draft_year.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
        .order_by(DraftPick.draft_year.desc())
    ).all()
    return sorted({int(y) for y in rows if y is not None}, reverse=True)


def fetch_nhl_bowl_picks_for_year(session: Session, year: int) -> list[DraftPick]:
    stmt = (
        select(DraftPick)
        .join(Draft)
        .options(
            joinedload(DraftPick.team),
            joinedload(DraftPick.player).joinedload(Player.current_team),
        )
        .where(DraftPick.draft_year == year)
        .where(nhl_bowl_draft_clause())
        .order_by(
            DraftPick.round.asc().nulls_last(),
            DraftPick.overall_pick.asc(),
        )
    )
    return list(session.scalars(stmt).unique().all())


def draft_pick_current_team_view(pick: DraftPick) -> dict[str, Any]:
    """State for the draft history \"Current team\" column (matches All-Time Records logic).

    Keys:
        ``kind``: ``\"no_player\"`` | ``\"retired\"`` | ``\"team\"`` | ``\"minors\"``
        ``team``: :class:`Team` when ``kind == \"team\"``, else ``None``
    """
    pl = pick.player
    if pl is None:
        return {"kind": "no_player", "team": None}
    if bool(pl.retired):
        return {"kind": "retired", "team": None}
    rt = main_league_roster_team(None, pl.current_team)
    if rt is not None:
        return {"kind": "team", "team": rt}
    return {"kind": "minors", "team": None}


def group_picks_by_round(picks: list[DraftPick]) -> list[tuple[int | None, list[DraftPick]]]:
    sorted_p = sorted(
        picks,
        key=lambda p: (p.round is None, (p.round or 0), p.overall_pick),
    )
    return [(k, list(g)) for k, g in groupby(sorted_p, key=lambda p: p.round)]


def is_goalie_player(player: Player | None) -> bool:
    if not player or not player.position:
        return False
    pos = player.position.strip().upper()
    return pos == "G" or pos.startswith("G ") or pos.startswith("G-")


def draft_row_stat_mode(
    player: Player | None,
    player_id: int | None,
    sk_map: dict[int, tuple[int, int, int, int]],
    gk_map: dict[int, tuple[int, int, int, int, float | None, int]],
) -> str:
    """Which stat columns to show: 'goalie', 'skater', or 'none'."""
    if not player or not player_id:
        return "none"
    g = gk_map.get(player_id)
    s = sk_map.get(player_id)
    # Prefer observed career data over a potentially stale/mis-tagged player.position.
    if g and not s:
        return "goalie"
    if s and not g:
        return "skater"
    if g and s:
        return "goalie" if g[0] >= s[0] else "skater"
    if is_goalie_player(player):
        return "goalie"
    return "skater"


def build_career_stat_maps(
    session: Session, player_ids: list[int]
) -> tuple[dict[int, tuple[int, int, int, int]], dict[int, tuple[int, int, int, int, float | None, int]]]:
    """Skater: (gp, g, a, pts). Goalie: (gp, w, l, otl, gaa, so).

    Only BOWL/NHL league rows (same ``league_fhm_id`` set as all-time records).
    """
    ids = sorted({i for i in player_ids if i})
    if not ids:
        return {}, {}

    league_ids = bowl_nhl_league_ids(session)
    if not league_ids:
        league_ids = (0,)

    sk_map: dict[int, tuple[int, int, int, int]] = {}
    g_map: dict[int, tuple[int, int, int, int, float | None, int]] = {}

    sk_line = PlayerSkaterCareerLine
    g_line = PlayerGoalieCareerLine
    for start in range(0, len(ids), _CAREER_QUERY_CHUNK):
        chunk = ids[start : start + _CAREER_QUERY_CHUNK]
        sk_rows = session.execute(
            select(
                sk_line.player_id,
                func.coalesce(func.sum(sk_line.gp), 0),
                func.coalesce(func.sum(sk_line.goals), 0),
                func.coalesce(func.sum(sk_line.assists), 0),
            )
            .where(sk_line.player_id.in_(chunk))
            .where(sk_line.career_source.in_(CAREER_SOURCES))
            .where(sk_line.league_fhm_id.in_(league_ids))
            .group_by(sk_line.player_id)
        ).all()
        for pid, gp, g, a in sk_rows:
            gi, gg, ga = int(gp), int(g), int(a)
            sk_map[int(pid)] = (gi, gg, ga, gg + ga)

        g_rows = session.execute(
            select(
                g_line.player_id,
                func.coalesce(func.sum(g_line.gp), 0),
                func.coalesce(func.sum(g_line.wins), 0),
                func.coalesce(func.sum(g_line.losses), 0),
                func.coalesce(func.sum(func.coalesce(g_line.ties_otl, 0)), 0),
                func.coalesce(func.sum(g_line.goals_against), 0),
                func.coalesce(func.sum(func.coalesce(g_line.minutes_played, 0)), 0),
                func.coalesce(func.sum(g_line.shutouts), 0),
            )
            .where(g_line.player_id.in_(chunk))
            .where(g_line.career_source.in_(CAREER_SOURCES))
            .where(g_line.league_fhm_id.in_(league_ids))
            .group_by(g_line.player_id)
        ).all()
        for pid, gp, w, l, otl, ga, mins, so in g_rows:
            tmin = int(mins)
            tga = int(ga)
            gaa = (tga * 60.0 / tmin) if tmin > 0 else None
            if gaa is not None and (math.isnan(gaa) or math.isinf(gaa)):
                gaa = None
            g_map[int(pid)] = (int(gp), int(w), int(l), int(otl), gaa, int(so))

    return sk_map, g_map


def draft_team_fhm_ids_for_player(raw_import_dir: Path, player_fhm_id: str | None) -> dict[tuple[int, int, int], int]:
    """Map (fhm_draft_id, year, overall) -> drafting team FHM id from draft_info CSV.

    Used to restore era team identity when ``draft_picks.team_id`` is null (common in old NHL drafts).
    """
    pid_txt = str(player_fhm_id or "").strip()
    try:
        pid = int(pid_txt)
    except (TypeError, ValueError):
        return {}
    path = raw_import_dir / "draft_info.csv"
    if not path.is_file():
        return {}
    out: dict[tuple[int, int, int], int] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(2048)
            fh.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            rdr = csv.DictReader(fh, delimiter=delim)
            for row in rdr:
                try:
                    row_pid = int(str(row.get("PlayerId") or row.get("playerid") or "").strip())
                except (TypeError, ValueError):
                    continue
                if row_pid != pid:
                    continue
                try:
                    did = int(str(row.get("DraftId") or row.get("draftid") or "").strip())
                    yr = int(str(row.get("Year") or row.get("year") or "").strip())
                    ov = int(str(row.get("Overall") or row.get("overall") or "").strip())
                    tm = int(str(row.get("Tam") or row.get("TeamId") or row.get("teamid") or "").strip())
                except (TypeError, ValueError):
                    continue
                out[(did, yr, ov)] = tm
    except OSError:
        return {}
    return out


def draft_team_fhm_ids_for_year(raw_import_dir: Path, draft_year: int) -> dict[tuple[int, int], int]:
    """Map (fhm_draft_id, overall) -> drafting team FHM id for one draft year."""
    path = raw_import_dir / "draft_info.csv"
    if not path.is_file():
        return {}
    out: dict[tuple[int, int], int] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(2048)
            fh.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            rdr = csv.DictReader(fh, delimiter=delim)
            for row in rdr:
                try:
                    yr = int(str(row.get("Year") or row.get("year") or "").strip())
                except (TypeError, ValueError):
                    continue
                if yr != int(draft_year):
                    continue
                try:
                    did = int(str(row.get("DraftId") or row.get("draftid") or "").strip())
                    ov = int(str(row.get("Overall") or row.get("overall") or "").strip())
                    tm = int(str(row.get("Tam") or row.get("TeamId") or row.get("teamid") or "").strip())
                except (TypeError, ValueError):
                    continue
                out[(did, ov)] = tm
    except OSError:
        return {}
    return out

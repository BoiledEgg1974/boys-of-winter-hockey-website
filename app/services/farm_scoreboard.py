"""Farm / overseas leagues: division standings, leaders, scores."""

from __future__ import annotations



from collections import defaultdict

from dataclasses import dataclass

from datetime import date, timedelta

from pathlib import Path

from typing import Any



from sqlalchemy import or_, select

from sqlalchemy.orm import Session, joinedload



from app.logo_urls import fhm_league_logo_url, team_logo_url_for_team

from app.models import Game, LeagueMeta, Player, PlayerGoalieStat, PlayerSkaterStat, Team, TeamStanding

from app.services.relegation import get_tier_config

from app.services.seasons import get_current_season, season_display_label

from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

from scripts.import_pipeline.fhm_loader import _slug, load_division_names, resolve_division_name





@dataclass(frozen=True)

class FarmLeagueDef:

    fhm_league_id: int

    name: str

    abbreviation: str





def _excluded_fhm_league_ids(session: Session) -> set[int]:

    cfg = get_tier_config(session)

    exclude = set(int(x) for x in cfg.upper_league_ids) | set(int(x) for x in cfg.lower_league_ids)

    exclude.add(6)

    return exclude





def farm_league_defs(session: Session, raw_dir: Path | None = None) -> list[FarmLeagueDef]:

    exclude = _excluded_fhm_league_ids(session)

    by_id: dict[int, FarmLeagueDef] = {}

    for row in session.scalars(select(LeagueMeta).order_by(LeagueMeta.name)).all():

        lid = int(row.fhm_league_id)

        if lid in exclude:

            continue

        by_id[lid] = FarmLeagueDef(lid, row.name, (row.abbreviation or "").strip())



    if raw_dir and (raw_dir / "league_data.csv").is_file():

        for _, csv_row in read_csv_normalized(raw_dir / "league_data.csv").iterrows():

            r = csv_row.to_dict()

            lid = to_int(cell_val(r, "leagueid", "league_id"))

            if lid is None or int(lid) in exclude:

                continue

            lid = int(lid)

            if lid not in by_id:

                by_id[lid] = FarmLeagueDef(

                    lid,

                    cell_val(r, "name") or "League",

                    (cell_val(r, "abbr") or "").strip(),

                )

    return [by_id[k] for k in sorted(by_id)]





def _conference_names(raw_dir: Path | None, fhm_league_id: int) -> dict[int, str]:
    if not raw_dir or not (raw_dir / "conferences.csv").is_file():
        return {}
    out: dict[int, str] = {}
    for _, row in read_csv_normalized(raw_dir / "conferences.csv").iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "league_id", "leagueid", "league id"))
        if lid != int(fhm_league_id):
            continue
        cid = to_int(cell_val(r, "conference_id", "conferenceid", "conference id"))
        name = cell_val(r, "name")
        if cid is not None and name:
            out[int(cid)] = name
    return out


def _division_display_name(
    division_name: str,
    conference_id: int,
    conf_names: dict[int, str],
) -> str:
    base = (division_name or "").strip() or "Division"
    if conference_id >= 0 and conference_id in conf_names:
        return f"{conf_names[conference_id]} — {base}"
    return base


def _division_catalog(raw_dir: Path | None, fhm_league_id: int) -> list[tuple[int, int, str]]:

    """Division order and labels from ``divisions.csv`` for one league."""

    if not raw_dir or not (raw_dir / "divisions.csv").is_file():

        return []

    out: list[tuple[int, int, str]] = []

    for _, row in read_csv_normalized(raw_dir / "divisions.csv").iterrows():

        r = row.to_dict()

        lid = to_int(cell_val(r, "league_id", "leagueid", "league id"))

        if lid != int(fhm_league_id):

            continue

        did = to_int(cell_val(r, "division_id", "divisionid", "division id"))

        name = cell_val(r, "name")

        cid_raw = to_int(cell_val(r, "conference_id", "conferenceid", "conference id"))

        cid = int(cid_raw) if cid_raw is not None else -1

        if did is not None and name:

            out.append((cid, int(did), name))

    return out





def _sort_standings_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:

    return sorted(rows, key=lambda r: (-int(r["pts"]), -int(r["w"]), -int(r["gf"])))





def _assemble_division_groups(

    rows: list[dict[str, Any]],

    *,

    catalog: list[tuple[int, int, str]],

    conf_names: dict[int, str] | None = None,

    fallback_label: str = "Other",

) -> list[dict[str, Any]]:

    conf_names = conf_names or {}

    buckets: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)

    labels: dict[tuple[int, int], str] = {}

    for row in rows:

        cid = int(row.get("conference_id") if row.get("conference_id") is not None else -1)

        did = int(row.get("division_id") if row.get("division_id") is not None else -1)

        key = (cid, did)

        buckets[key].append(row)

        label = (row.get("division_name") or "").strip()

        if label:

            labels[key] = label



    groups: list[dict[str, Any]] = []

    used: set[tuple[int, int]] = set()

    for cid, did, name in catalog:

        key = (cid, did)

        if key not in buckets:

            continue

        used.add(key)

        raw_label = labels.get(key) or name

        groups.append(

            {

                "division_name": _division_display_name(raw_label, cid, conf_names),

                "conference_id": cid,

                "division_id": did,

                "teams": _sort_standings_rows(buckets[key]),

            }

        )



    for key in sorted(buckets.keys()):

        if key in used:

            continue

        cid, did = key

        raw_label = labels.get(key) or (fallback_label if did >= 0 else "League")

        groups.append(

            {

                "division_name": _division_display_name(raw_label, cid, conf_names),

                "conference_id": cid,

                "division_id": did,

                "teams": _sort_standings_rows(buckets[key]),

            }

        )

    return groups


def _build_conference_groups(
    division_groups: list[dict[str, Any]],
    *,
    raw_dir: Path | None,
    fhm_league_id: int,
) -> list[dict[str, Any]]:
    """Stack conferences vertically; divisions within a conference stay side-by-side."""
    if not division_groups:
        return []
    conf_names = _conference_names(raw_dir, fhm_league_id)
    conf_order: list[int] = list(conf_names.keys())
    by_cid: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen_cids: list[int] = []
    for div in division_groups:
        cid = int(div.get("conference_id", -1))
        if cid not in seen_cids:
            seen_cids.append(cid)
        d = dict(div)
        full = (div.get("division_name") or "").strip()
        if " — " in full:
            d["division_name"] = full.split(" — ", 1)[1].strip()
        by_cid[cid].append(d)
    order = [c for c in conf_order if c in by_cid]
    for c in seen_cids:
        if c not in order:
            order.append(c)
    out: list[dict[str, Any]] = []
    for cid in order:
        if cid >= 0 and cid in conf_names:
            cname = conf_names[cid]
        elif cid >= 0:
            cname = f"Conference {cid + 1}"
        else:
            cname = "Standings"
        out.append(
            {
                "conference_id": cid,
                "conference_name": cname,
                "divisions": by_cid[cid],
            }
        )
    return out


def _team_ids_for_fhm_league(session: Session, fhm_league_id: int) -> frozenset[int]:

    teams = session.scalars(select(Team).where(Team.fhm_league_id == int(fhm_league_id))).all()

    return frozenset(int(t.id) for t in teams)





def _format_game(g: Game) -> dict[str, Any] | None:

    ht = g.home_team

    at = g.away_team

    if not ht or not at:

        return None

    return {

        "id": int(g.id),

        "date": g.game_date.isoformat() if g.game_date else None,

        "status": g.status or "",

        "home_abbr": ht.abbreviation or "",

        "away_abbr": at.abbreviation or "",

        "home_name": ht.full_display_name(),

        "away_name": at.full_display_name(),

        "home_logo_url": team_logo_url_for_team(ht),

        "away_logo_url": team_logo_url_for_team(at),

        "home_score": g.home_score,

        "away_score": g.away_score,

        "went_to_overtime": bool(g.went_to_overtime),

        "went_to_shootout": bool(g.went_to_shootout),

    }





def _standings_divisions_for_league(

    session: Session,

    season_id: int,

    fhm_league_id: int,

    raw_dir: Path | None,

    div_map: dict[tuple[int, int, int], str],

) -> list[dict[str, Any]]:

    rows_db = session.execute(

        select(TeamStanding, Team)

        .join(Team, Team.id == TeamStanding.team_id)

        .where(

            TeamStanding.season_id == season_id,

            Team.fhm_league_id == int(fhm_league_id),

        )

    ).all()

    flat: list[dict[str, Any]] = []

    for st, tm in rows_db:

        cid = tm.fhm_conference_id

        did = tm.fhm_division_id

        div_name = (st.division or "").strip() or resolve_division_name(

            div_map, int(fhm_league_id), cid, did

        )

        flat.append(

            {

                "team_abbr": tm.abbreviation or "",

                "team_name": tm.full_display_name(),

                "team_logo_url": team_logo_url_for_team(tm),

                "gp": st.standing_gp_display(),

                "w": int(st.w or 0),

                "l": int(st.l or 0),

                "otl": int(st.otl or 0),

                "pts": int(st.pts or 0),

                "gf": int(st.gf or 0),

                "ga": int(st.ga or 0),

                "conference_id": int(cid) if cid is not None else -1,

                "division_id": int(did) if did is not None else -1,

                "division_name": div_name or "",

            }

        )

    catalog = _division_catalog(raw_dir, fhm_league_id)

    conf_names = _conference_names(raw_dir, fhm_league_id)

    return _assemble_division_groups(flat, catalog=catalog, conf_names=conf_names)





def _standings_divisions_from_csv(

    raw_dir: Path,

    fhm_league_id: int,

    div_map: dict[tuple[int, int, int], str],

) -> list[dict[str, Any]]:

    team_path = raw_dir / "team_data.csv"

    rec_path = raw_dir / "team_records.csv"

    if not team_path.is_file() or not rec_path.is_file():

        return []



    meta_by_fhm: dict[int, dict[str, Any]] = {}

    for _, row in read_csv_normalized(team_path).iterrows():

        r = row.to_dict()

        if to_int(cell_val(r, "leagueid", "league_id")) != int(fhm_league_id):

            continue

        tid = to_int(cell_val(r, "teamid", "team_id"))

        if tid is None:

            continue

        city = (cell_val(r, "name") or "").strip()

        nick = (cell_val(r, "nickname") or "").strip()

        abbr = (cell_val(r, "abbr") or "").strip()

        cid = to_int(cell_val(r, "conference_id", "conferenceid", "conference id"))

        did = to_int(cell_val(r, "division_id", "divisionid", "division id"))

        slug = _slug(abbr or str(tid), int(tid))

        meta_by_fhm[int(tid)] = {

            "abbr": abbr or str(tid),

            "name": f"{city} {nick}".strip() or abbr,

            "conference_id": int(cid) if cid is not None else -1,

            "division_id": int(did) if did is not None else -1,

            "slug": slug,

        }



    flat: list[dict[str, Any]] = []

    for _, row in read_csv_normalized(rec_path).iterrows():

        r = row.to_dict()

        if to_int(cell_val(r, "leagueid", "league_id", "league id")) != int(fhm_league_id):

            continue

        tid = to_int(cell_val(r, "teamid", "team_id", "team id"))

        if tid is None:

            continue

        meta = meta_by_fhm.get(int(tid), {})

        cid = to_int(cell_val(r, "confid", "conf id", "conference_id", "conference id"))

        did = to_int(cell_val(r, "divid", "div id", "division_id", "division id"))

        if cid is None:

            cid = meta.get("conference_id", -1)

        if did is None:

            did = meta.get("division_id", -1)

        cid_i = int(cid) if cid is not None else -1

        did_i = int(did) if did is not None else -1

        div_name = resolve_division_name(div_map, int(fhm_league_id), cid_i if cid_i >= 0 else None, did_i)

        w = to_int(cell_val(r, "wins")) or 0

        l = to_int(cell_val(r, "losses")) or 0

        otl = to_int(cell_val(r, "otl")) or 0

        ties = to_int(cell_val(r, "ties")) or 0

        sow = to_int(cell_val(r, "shootoutwins", "shootout wins")) or 0

        sol = to_int(cell_val(r, "shootoutlosses", "shootout losses")) or 0

        gp = w + l + otl + ties + sow + sol

        slug = meta.get("slug") or _slug(meta.get("abbr", str(tid)), int(tid))

        logo_url = ""

        try:

            from flask import current_app, url_for



            rel = f"{current_app.config.get('TEAM_LOGOS_REL_DIR', 'logos/teams')}/{slug}.png".replace("\\", "/")

            static_root = Path(current_app.static_folder or "")

            if (static_root / rel).is_file():

                logo_url = url_for("static", filename=rel)

        except Exception:

            pass

        flat.append(

            {

                "team_abbr": meta.get("abbr", str(tid)),

                "team_name": meta.get("name", str(tid)),

                "team_logo_url": logo_url,

                "gp": gp,

                "w": w,

                "l": l,

                "otl": otl,

                "pts": to_int(cell_val(r, "points")) or 0,

                "gf": to_int(cell_val(r, "goalsfor", "goals for")) or 0,

                "ga": to_int(cell_val(r, "goalsagainst", "goals against")) or 0,

                "conference_id": cid_i,

                "division_id": did_i,

                "division_name": div_name or "",

            }

        )

    catalog = _division_catalog(raw_dir, fhm_league_id)

    conf_names = _conference_names(raw_dir, fhm_league_id)

    return _assemble_division_groups(flat, catalog=catalog, conf_names=conf_names)





def _parse_schedule_date(raw: str | None) -> date | None:

    if not raw:

        return None

    parts = str(raw).strip().split("-")

    if len(parts) != 3:

        return None

    y, m, d = to_int(parts[0]), to_int(parts[1]), to_int(parts[2])

    if not y or not m or not d:

        return None

    try:

        return date(int(y), int(m), int(d))

    except ValueError:

        return None





def _games_from_csv(

    raw_dir: Path,

    fhm_league_id: int,

    *,

    anchor: date,

    recent_days: int = 10,

    upcoming_days: int = 14,

) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    path = raw_dir / "schedules.csv"

    team_path = raw_dir / "team_data.csv"

    if not path.is_file() or not team_path.is_file():

        return [], []

    abbr_by_fhm: dict[int, str] = {}

    logo_by_fhm: dict[int, str] = {}

    for _, row in read_csv_normalized(team_path).iterrows():

        r = row.to_dict()

        if to_int(cell_val(r, "leagueid", "league_id")) != int(fhm_league_id):

            continue

        tid = to_int(cell_val(r, "teamid", "team_id"))

        if tid is None:

            continue

        abbr = (cell_val(r, "abbr") or str(tid)).strip()

        abbr_by_fhm[int(tid)] = abbr

        slug = _slug(abbr, int(tid))

        logo_by_fhm[int(tid)] = slug



    def _logo_url_for_fhm(fhm_tid: int) -> str:

        slug = logo_by_fhm.get(fhm_tid, "")

        if not slug:

            return ""

        try:

            from flask import current_app, url_for



            rel = f"{current_app.config.get('TEAM_LOGOS_REL_DIR', 'logos/teams')}/{slug}.png".replace("\\", "/")

            if (Path(current_app.static_folder or "") / rel).is_file():

                return url_for("static", filename=rel)

        except Exception:

            pass

        return ""



    recent_start = anchor - timedelta(days=recent_days)

    upcoming_end = anchor + timedelta(days=upcoming_days)

    recent: list[dict[str, Any]] = []

    upcoming: list[dict[str, Any]] = []



    for _, row in read_csv_normalized(path).iterrows():

        r = row.to_dict()

        if to_int(cell_val(r, "leagueid", "league_id", "league id")) != int(fhm_league_id):

            continue

        gd = _parse_schedule_date(cell_val(r, "date"))

        if gd is None:

            continue

        played = (cell_val(r, "played") or "").strip() == "1"

        home = to_int(cell_val(r, "home"))

        away = to_int(cell_val(r, "away"))

        if home is None or away is None:

            continue

        sh = to_int(cell_val(r, "scorehome", "score home"))

        sa = to_int(cell_val(r, "scoreaway", "score away"))

        ot = (cell_val(r, "overtime") or "").strip() == "1"

        so = (cell_val(r, "shootout") or "").strip() == "1"

        item = {

            "id": to_int(cell_val(r, "gameid", "game id")) or 0,

            "date": gd.isoformat(),

            "status": "final" if played else "scheduled",

            "home_abbr": abbr_by_fhm.get(int(home), str(home)),

            "away_abbr": abbr_by_fhm.get(int(away), str(away)),

            "home_logo_url": _logo_url_for_fhm(int(home)),

            "away_logo_url": _logo_url_for_fhm(int(away)),

            "home_score": sh,

            "away_score": sa,

            "went_to_overtime": ot and not so,

            "went_to_shootout": so,

        }

        if played and recent_start <= gd <= anchor:

            recent.append(item)

        elif not played and anchor < gd <= upcoming_end:

            upcoming.append(item)



    recent.sort(key=lambda g: g["date"], reverse=True)

    upcoming.sort(key=lambda g: g["date"])

    return recent[:40], upcoming[:40]





def _skater_leaders(session: Session, season_id: int, fhm_league_id: int, *, limit: int = 8) -> list[dict[str, Any]]:

    rows = session.execute(

        select(PlayerSkaterStat, Player, Team)

        .join(Player, Player.id == PlayerSkaterStat.player_id)

        .join(Team, Team.id == PlayerSkaterStat.team_id)

        .where(

            PlayerSkaterStat.season_id == season_id,

            PlayerSkaterStat.stat_segment == "rs",

            PlayerSkaterStat.gp > 0,

            Team.fhm_league_id == int(fhm_league_id),

        )

        .order_by(PlayerSkaterStat.points.desc(), PlayerSkaterStat.goals.desc())

        .limit(limit)

    ).all()

    return [

        {

            "name": (pl.full_name or f"{pl.first_name} {pl.last_name}").strip(),

            "team_abbr": tm.abbreviation or "",

            "team_logo_url": team_logo_url_for_team(tm),

            "gp": int(st.gp or 0),

            "g": int(st.goals or 0),

            "a": int(st.assists or 0),

            "pts": int(st.points or 0),

        }

        for st, pl, tm in rows

    ]





def _goalie_leaders(session: Session, season_id: int, fhm_league_id: int, *, limit: int = 5) -> list[dict[str, Any]]:

    rows = session.execute(

        select(PlayerGoalieStat, Player, Team)

        .join(Player, Player.id == PlayerGoalieStat.player_id)

        .join(Team, Team.id == PlayerGoalieStat.team_id)

        .where(

            PlayerGoalieStat.season_id == season_id,

            PlayerGoalieStat.stat_segment == "rs",

            PlayerGoalieStat.gp >= 3,

            Team.fhm_league_id == int(fhm_league_id),

        )

        .order_by(PlayerGoalieStat.wins.desc(), PlayerGoalieStat.sv_pct.desc())

        .limit(limit)

    ).all()

    out: list[dict[str, Any]] = []

    for st, pl, tm in rows:

        sv = st.sv_pct

        out.append(

            {

                "name": (pl.full_name or f"{pl.first_name} {pl.last_name}").strip(),

                "team_abbr": tm.abbreviation or "",

                "team_logo_url": team_logo_url_for_team(tm),

                "gp": int(st.gp or 0),

                "w": int(st.wins or 0),

                "gaa": round(float(st.gaa), 2) if st.gaa is not None else None,

                "sv_pct": round(float(sv), 3) if sv is not None else None,

            }

        )

    return out





def _games_for_league(

    session: Session,

    *,

    season_id: int,

    team_ids: frozenset[int],

    anchor: date,

    recent_days: int = 10,

    upcoming_days: int = 14,

) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    if not team_ids:

        return [], []

    recent_start = anchor - timedelta(days=recent_days)

    upcoming_end = anchor + timedelta(days=upcoming_days)



    recent_q = (

        select(Game)

        .options(joinedload(Game.home_team), joinedload(Game.away_team))

        .where(

            Game.season_id == season_id,

            or_(Game.home_team_id.in_(team_ids), Game.away_team_id.in_(team_ids)),

            Game.game_date.is_not(None),

            Game.game_date >= recent_start,

            Game.game_date <= anchor,

            Game.status == "final",

        )

        .order_by(Game.game_date.desc(), Game.id.desc())

        .limit(40)

    )

    upcoming_q = (

        select(Game)

        .options(joinedload(Game.home_team), joinedload(Game.away_team))

        .where(

            Game.season_id == season_id,

            or_(Game.home_team_id.in_(team_ids), Game.away_team_id.in_(team_ids)),

            Game.game_date.is_not(None),

            Game.game_date > anchor,

            Game.game_date <= upcoming_end,

            Game.status != "final",

        )

        .order_by(Game.game_date.asc(), Game.id.asc())

        .limit(40)

    )

    recent = [_format_game(g) for g in session.scalars(recent_q).all()]

    upcoming = [_format_game(g) for g in session.scalars(upcoming_q).all()]

    return [g for g in recent if g], [g for g in upcoming if g]





def _league_section(

    session: Session,

    *,

    league: FarmLeagueDef,

    season_id: int | None,

    anchor: date,

    raw_dir: Path | None,

    div_map: dict[tuple[int, int, int], str],

) -> dict[str, Any]:

    lid = int(league.fhm_league_id)

    team_ids = _team_ids_for_fhm_league(session, lid)

    division_groups: list[dict[str, Any]] = []

    skaters: list[dict[str, Any]] = []

    goalies: list[dict[str, Any]] = []

    recent: list[dict[str, Any]] = []

    upcoming: list[dict[str, Any]] = []



    if season_id is not None:

        division_groups = _standings_divisions_for_league(session, season_id, lid, raw_dir, div_map)

        skaters = _skater_leaders(session, season_id, lid)

        goalies = _goalie_leaders(session, season_id, lid)

        recent, upcoming = _games_for_league(session, season_id=season_id, team_ids=team_ids, anchor=anchor)



    if raw_dir and raw_dir.is_dir():

        if not division_groups:

            division_groups = _standings_divisions_from_csv(raw_dir, lid, div_map)

        if not recent and not upcoming:

            recent, upcoming = _games_from_csv(raw_dir, lid, anchor=anchor)

    conference_groups = _build_conference_groups(
        division_groups, raw_dir=raw_dir, fhm_league_id=lid
    )

    return {

        "fhm_league_id": lid,

        "name": league.name,

        "abbr": league.abbreviation,

        "logo_url": fhm_league_logo_url(lid),

        "conference_groups": conference_groups,

        "skater_leaders": skaters,

        "goalie_leaders": goalies,

        "recent_games": recent,

        "upcoming_games": upcoming,

    }





def build_farm_scoreboard_payload(

    session: Session,

    *,

    on_date: date | None = None,

    league_fhm_id: int | None = None,

    days_back: int = 1,

    raw_dir: Path | None = None,

) -> dict[str, Any]:

    _ = days_back

    anchor = on_date or date.today()

    season = get_current_season(session)

    season_id = int(season.id) if season else None

    div_map = load_division_names(raw_dir) if raw_dir and raw_dir.is_dir() else {}

    defs = farm_league_defs(session, raw_dir)

    if league_fhm_id is not None:

        lf = int(league_fhm_id)

        defs = [d for d in defs if d.fhm_league_id == lf]



    sections = [

        _league_section(session, league=d, season_id=season_id, anchor=anchor, raw_dir=raw_dir, div_map=div_map)

        for d in defs

    ]

    nav = [

        {"fhm_league_id": s["fhm_league_id"], "name": s["name"], "abbr": s["abbr"], "logo_url": s["logo_url"]}

        for s in sections

    ]

    return {

        "date": anchor.isoformat(),

        "season_label": season_display_label(season) if season else "",

        "leagues": nav,

        "sections": sections,

        "games": [],

    }



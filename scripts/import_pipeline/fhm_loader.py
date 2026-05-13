"""Import Franchise Hockey Manager semicolon CSV exports (team_data, schedules, boxscores, etc.)."""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select

from app.models import (
    Draft,
    DraftPick,
    Game,
    GameGoalieStat,
    GameSkaterStat,
    LeagueMeta,
    Player,
    PlayerContract,
    PlayerGoalieStat,
    PlayerGoalieCareerLine,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    ScoringEvent,
    Season,
    Team,
    TeamSeasonAggregate,
    TeamStanding,
    db,
)
from scripts.import_pipeline.encoding_utils import (
    cell_val,
    fhm_scoring_period_to_int,
    parse_fhm_date,
    repair_likely_cp1250_mojibake,
    read_csv_normalized,
    to_bool,
    to_float,
    to_int,
)

log = logging.getLogger("bowl.fhm")


def team_data_csv_path(raw_dir: Path) -> Path | None:
    """Resolve FHM team export (``team_data.csv`` or ``Team_Data.csv``)."""
    for name in ("team_data.csv", "Team_Data.csv"):
        p = raw_dir / name
        if p.is_file():
            return p
    return None


def _league_start_year_from_game_date(d: date) -> int:
    """League year begins July 1: games in Jan–Jun belong to the previous July–June label."""
    return int(d.year) if d.month >= 7 else int(d.year) - 1


def _slug(abbr: str, team_fhm_id: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (abbr or "tm").lower()).strip("-") or "tm"
    return f"{base}-t{team_fhm_id}"


def _clean_text(raw: str | None) -> str | None:
    return repair_likely_cp1250_mojibake(raw)


def _parse_date(y, m, d) -> date | None:
    yi, mi, di = to_int(y), to_int(m), to_int(d)
    if not yi or not mi or not di:
        return None
    try:
        return date(yi, mi, di)
    except ValueError:
        return None


def _fmt_clock_seconds(sec: int | None) -> str | None:
    if sec is None:
        return None
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _toi_seconds(raw) -> int | None:
    v = to_float(raw)
    if v is None:
        return None
    if v > 1_000_000:
        return int(v // 1000)
    if v > 10_000:
        return int(v // 1000)
    return int(v)


def load_division_names(raw_dir: Path) -> dict[tuple[int, int, int], str]:
    """Map (league_id, conference_id, division_id) -> name.

    FHM reuses division_id across conferences (e.g. 0 = Adams vs Norris); keys must include conference_id.
    """
    path = raw_dir / "divisions.csv"
    if not path.exists():
        return {}
    df = read_csv_normalized(path)
    out: dict[tuple[int, int, int], str] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "league_id", "leagueid"))
        did = to_int(cell_val(r, "division_id", "divisionid"))
        name = cell_val(r, "name")
        cid_raw = to_int(cell_val(r, "conference_id", "conferenceid"))
        cid = int(cid_raw) if cid_raw is not None else -1
        if lid is not None and did is not None and name:
            out[(lid, cid, did)] = name
    return out


def resolve_division_name(
    div_map: dict[tuple[int, int, int], str],
    league_id: int,
    conference_id: int | None,
    division_id: int | None,
) -> str | None:
    if division_id is None:
        return None
    cid = int(conference_id) if conference_id is not None else -9999
    key = (league_id, cid, division_id)
    if key in div_map:
        return div_map[key]
    wild = (league_id, -1, division_id)
    if wild in div_map:
        return div_map[wild]
    return None


def import_league_meta(raw_dir: Path, league_filter: int) -> int:
    path = raw_dir / "league_data.csv"
    if not path.exists():
        return 0
    n = 0
    df = read_csv_normalized(path)
    for _, row in df.iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "leagueid", "league_id"))
        if lid != league_filter:
            continue
        name = cell_val(r, "name") or "League"
        abbr = cell_val(r, "abbr")
        existing = db.session.scalars(
            select(LeagueMeta).where(LeagueMeta.fhm_league_id == lid).limit(1)
        ).first()
        if existing:
            lm = existing
        else:
            lm = LeagueMeta(fhm_league_id=lid, name=name)
            db.session.add(lm)
        lm.name = name
        lm.abbreviation = abbr
        n += 1
    db.session.commit()
    return n


def ensure_season(raw_dir: Path, league_filter: int) -> tuple[Season, bool]:
    """Upsert the single FHM mount season row and return whether ``start_year``/``end_year`` changed.

    Schedule dates are mapped to a **July–June** league start year so January games attach to
    the correct Boys-of-Winter season. The same ``Season`` row is reused (``fhm_season_id``);
    ``run_fhm_import`` always clears season-scoped skater/goalie aggregates before reloading
    CSV segments so rolled years cannot keep stale totals.
    """
    path = raw_dir / "schedules.csv"
    league_start_years: list[int] = []
    if path.exists():
        df = read_csv_normalized(path)
        for _, row in df.iterrows():
            r = row.to_dict()
            if to_int(cell_val(r, "league_id", "leagueid")) != league_filter:
                continue
            ds = cell_val(r, "date")
            parsed = parse_fhm_date(ds)
            if parsed:
                league_start_years.append(_league_start_year_from_game_date(parsed))
    y0 = min(league_start_years) if league_start_years else 1967
    if league_start_years:
        y1 = max(league_start_years) + 1
    else:
        y1 = y0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    lm = db.session.scalars(
        select(LeagueMeta).where(LeagueMeta.fhm_league_id == league_filter).limit(1)
    ).first()
    override_path = raw_dir / "season_label.txt"
    if override_path.is_file():
        label = override_path.read_text(encoding="utf-8").strip().split("\n")[0].strip()
    else:
        label = f"{y0}–{str(y1)[-2:]} Season"
        if lm:
            label = f"{label} ({lm.name})"
    sid = f"fhm-league-{league_filter}"
    for ex in db.session.scalars(select(Season)).all():
        ex.is_current = False
    s = db.session.scalars(select(Season).where(Season.fhm_season_id == sid).limit(1)).first()
    prev_y0: int | None = None
    prev_y1: int | None = None
    if s:
        prev_y0, prev_y1 = s.start_year, s.end_year
    if not s:
        s = Season(fhm_season_id=sid, label=label, start_year=y0, end_year=y1, is_current=True)
        db.session.add(s)
        db.session.flush()
        league_year_changed = True
    else:
        league_year_changed = (prev_y0 != y0) or (prev_y1 != y1)
        s.label = label
        s.start_year = y0
        s.end_year = y1
        s.is_current = True
    db.session.commit()
    return s, league_year_changed


def import_fhm_teams(raw_dir: Path, league_filter: int, div_map: dict) -> dict[int, int]:
    """Returns map fhm_team_id -> internal Team.id"""
    path = team_data_csv_path(raw_dir)
    if path is None:
        log.warning("Skipping FHM teams: no team_data.csv / Team_Data.csv in %s", raw_dir)
        return {}
    df = read_csv_normalized(path)
    fhm_to_id: dict[int, int] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        if to_int(cell_val(r, "leagueid", "league_id")) != league_filter:
            continue
        tid = to_int(cell_val(r, "teamid"))
        if tid is None:
            continue
        name = _clean_text(cell_val(r, "name")) or f"Team {tid}"
        nick = _clean_text(cell_val(r, "nickname"))
        abbr = _clean_text(cell_val(r, "abbr")) or str(tid)
        pc = cell_val(r, "primary_color")
        sc = cell_val(r, "secondary_color")
        tc = cell_val(r, "text_color")
        cid = to_int(cell_val(r, "conference_id", "conferenceid"))
        did = to_int(cell_val(r, "division_id", "divisionid"))
        slug = _slug(abbr, tid)
        fhm_key = str(tid)
        t = db.session.scalars(select(Team).where(Team.fhm_team_id == fhm_key).limit(1)).first()
        if not t:
            t = Team(fhm_team_id=fhm_key, slug=slug, abbreviation=abbr[:8], name=name)
            db.session.add(t)
            db.session.flush()
        t.name = name
        # Name column is the city/region (e.g. "New York", "St. Louis"); do not take only the first token
        # or multi-word cities become wrong ("New" + "Rangers" → "New Rangers").
        t.city = name if name else None
        t.nickname = nick
        t.abbreviation = abbr[:8]
        t.slug = slug
        t.primary_color = pc
        t.secondary_color = sc
        t.text_color = tc
        t.fhm_league_id = league_filter
        t.fhm_conference_id = cid
        t.fhm_division_id = did
        fhm_to_id[tid] = t.id
    db.session.commit()
    return fhm_to_id


def import_players(raw_dir: Path, teams_fhm: dict[int, int]) -> dict[int, int]:
    path = raw_dir / "player_master.csv"
    if not path.exists():
        return {}
    df = read_csv_normalized(path)
    fhm_to_id: dict[int, int] = {}
    batch = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        if pid is None:
            continue
        fn = _clean_text(cell_val(r, "first_name")) or "?"
        ln = _clean_text(cell_val(r, "last_name")) or "?"
        full = f"{fn} {ln}".strip()
        fhm_key = str(pid)
        p = db.session.scalars(select(Player).where(Player.fhm_player_id == fhm_key).limit(1)).first()
        if not p:
            p = Player(fhm_player_id=fhm_key, first_name=fn, last_name=ln, full_name=full)
            db.session.add(p)
            db.session.flush()
        p.first_name = fn
        p.last_name = ln
        p.full_name = full
        p.nick_name = _clean_text(cell_val(r, "nick_name", "nickname"))
        p.shoots_catches = cell_val(r, "hand")
        bd = cell_val(r, "date_of_birth")
        bd_parsed = parse_fhm_date(bd)
        if bd_parsed:
            p.birth_date = bd_parsed
        p.birth_city = _clean_text(cell_val(r, "birthcity"))
        p.birth_state = _clean_text(cell_val(r, "birthstate"))
        p.nationality = _clean_text(cell_val(r, "nationality_one", "nationality"))
        p.height_inches = to_int(cell_val(r, "height"))
        p.weight_lbs = to_int(cell_val(r, "weight"))
        p.franchise_fhm_id = to_int(cell_val(r, "franchiseid"))
        p.retired = to_bool(cell_val(r, "retired")) or (cell_val(r, "retired") == "1")
        tm_fhm = to_int(cell_val(r, "teamid"))
        p.current_team_id = teams_fhm.get(tm_fhm) if tm_fhm is not None else None
        raw_j = cell_val(r, "jersey_number", "jersey", "jersey_no", "sweater_number", "sweater")
        if raw_j is not None and str(raw_j).strip() != "":
            jn = to_int(raw_j)
            if jn is not None:
                p.jersey_number = jn
        fhm_to_id[pid] = p.id
        batch += 1
        if batch >= 400:
            db.session.commit()
            batch = 0
    db.session.commit()
    return fhm_to_id


def import_ratings(raw_dir: Path, players_fhm: dict[int, int]) -> int:
    path = raw_dir / "player_ratings.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        if pid is None or pid not in players_fhm:
            continue
        pl = db.session.get(Player, players_fhm[pid])
        if not pl:
            continue
        pl.overall_ability = to_float(cell_val(r, "ability"))
        pl.overall_potential = to_float(cell_val(r, "potential"))
        # Position from highest among G, LD, RD, ...
        pos_cols = [
            ("G", "g"),
            ("D", "ld"),
            ("D", "rd"),
            ("LW", "lw"),
            ("C", "c"),
            ("RW", "rw"),
        ]
        best = None
        best_v = -1
        for pos, col in pos_cols:
            v = to_int(cell_val(r, col))
            if v is not None and v > best_v:
                best_v = v
                best = pos
        if best and not pl.position:
            pl.position = best
        n += 1
    db.session.commit()
    return n


def import_standings(raw_dir: Path, season: Season, teams_fhm: dict[int, int], div_map, league_filter: int) -> int:
    path = raw_dir / "team_records.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        if to_int(cell_val(r, "league_id", "leagueid")) != league_filter:
            continue
        tid = to_int(cell_val(r, "team_id", "teamid"))
        if tid is None or tid not in teams_fhm:
            continue
        team = db.session.get(Team, teams_fhm[tid])
        if not team:
            continue
        st = db.session.scalars(
            select(TeamStanding).where(
                TeamStanding.season_id == season.id, TeamStanding.team_id == team.id
            ).limit(1)
        ).first()
        if not st:
            st = TeamStanding(season_id=season.id, team_id=team.id)
            db.session.add(st)
        w = to_int(cell_val(r, "wins"), 0) or 0
        l = to_int(cell_val(r, "losses"), 0) or 0
        t = to_int(cell_val(r, "ties"), 0) or 0
        otl = to_int(cell_val(r, "otl"), 0) or 0
        sow = to_int(cell_val(r, "shootout_wins"), 0) or 0
        sol = to_int(cell_val(r, "shootout_losses"), 0) or 0
        st.w = w
        st.l = l
        st.ties = t
        st.otl = otl
        st.shootout_wins = sow
        st.shootout_losses = sol
        # GP excludes OTL here; OTL is tracked separately (same game is not double-counted in GP).
        st.gp = w + l + t + sow + sol
        st.pts = to_int(cell_val(r, "points"), 0) or 0
        st.gf = to_int(cell_val(r, "goals_for"), 0) or 0
        st.ga = to_int(cell_val(r, "goals_against"), 0) or 0
        st.win_pct = to_float(cell_val(r, "pct"))
        did = team.fhm_division_id
        st.division = resolve_division_name(div_map, league_filter, team.fhm_conference_id, did)
        st.conference = None
        n += 1
    db.session.commit()
    return n


def import_team_season_stats(
    raw_dir: Path,
    season: Season,
    teams_fhm: dict[int, int],
    league_filter: int,
    *,
    filename: str = "team_stats.csv",
    stat_segment: str = "rs",
) -> int:
    path = raw_dir / filename
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        tid = to_int(cell_val(r, "teamid"))
        if tid is None or tid not in teams_fhm:
            continue
        team_id = teams_fhm[tid]
        agg = db.session.scalars(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == season.id,
                TeamSeasonAggregate.team_id == team_id,
                TeamSeasonAggregate.stat_segment == stat_segment,
            ).limit(1)
        ).first()
        if not agg:
            agg = TeamSeasonAggregate(
                season_id=season.id, team_id=team_id, stat_segment=stat_segment
            )
            db.session.add(agg)
        agg.shots_for = to_int(cell_val(r, "s"))
        agg.shots_against = to_int(cell_val(r, "sa"))
        agg.faceoff_pct = to_float(cell_val(r, "fo_pct", "fo"))
        agg.blocked_shots = to_int(cell_val(r, "sb"))
        agg.hits = to_int(cell_val(r, "h"))
        agg.takeaways = to_int(cell_val(r, "tka"))
        agg.giveaways = to_int(cell_val(r, "gva"))
        agg.pp_chances = to_int(cell_val(r, "pp_ch"))
        agg.pp_goals = to_int(cell_val(r, "ppg"))
        agg.pk_goals_against = to_int(cell_val(r, "pp_ga"))
        agg.sh_chances = to_int(cell_val(r, "sh_ch", "sh_chances", "shch"))
        agg.sh_goals = to_int(cell_val(r, "shg"))
        agg.pim_per_game = to_float(cell_val(r, "pim_g"))
        agg.attendance_home = to_int(cell_val(r, "att_total_home"))
        agg.attendance_away = to_int(cell_val(r, "att_total_away"))
        agg.sellouts_home = to_int(cell_val(r, "sellouts_home"))
        agg.sellouts_away = to_int(cell_val(r, "sellouts_away"))
        agg.capacity_use_pct = to_float(cell_val(r, "capacity_use"))
        n += 1
    db.session.commit()
    return n


def import_games(
    raw_dir: Path,
    season: Season,
    teams_fhm: dict[int, int],
    league_filter: int,
) -> dict[str, int]:
    """Returns fhm_game_id -> internal game id"""
    path = raw_dir / "schedules.csv"
    if not path.exists():
        return {}
    df = read_csv_normalized(path)

    def _sum_int_cells(row_dict: dict, *keys: str) -> int | None:
        vals: list[int] = []
        for k in keys:
            v = to_int(cell_val(row_dict, k))
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return sum(vals)

    fhm_to_gid: dict[str, int] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        if to_int(cell_val(r, "league_id", "leagueid")) != league_filter:
            continue
        gid = cell_val(r, "game_id", "gameid")
        if not gid:
            continue
        home_fhm = to_int(cell_val(r, "home"))
        away_fhm = to_int(cell_val(r, "away"))
        if home_fhm is None or away_fhm is None:
            continue
        hid = teams_fhm.get(home_fhm)
        aid = teams_fhm.get(away_fhm)
        if not hid or not aid:
            continue
        g = db.session.scalars(select(Game).where(Game.fhm_game_id == gid).limit(1)).first()
        if not g:
            g = Game(
                season_id=season.id,
                home_team_id=hid,
                away_team_id=aid,
                fhm_game_id=gid,
            )
            db.session.add(g)
            db.session.flush()
        else:
            # Re-bind to the active FHM season row; reused ``fhm_game_id`` rows must not keep
            # a stale ``season_id`` from an older league-year row after rollover.
            g.season_id = int(season.id)
        ds = cell_val(r, "date")
        gd = parse_fhm_date(ds)
        if gd:
            g.game_date = gd
        g.home_score = to_int(cell_val(r, "score_home"))
        g.away_score = to_int(cell_val(r, "score_away"))
        played = cell_val(r, "played")
        g.status = "final" if played == "1" else "scheduled"
        g.went_to_overtime = to_bool(cell_val(r, "overtime")) or cell_val(r, "overtime") == "1"
        g.went_to_shootout = to_bool(cell_val(r, "shootout")) or cell_val(r, "shootout") == "1"
        g.game_type = cell_val(r, "type")
        g.fhm_league_id = league_filter
        fhm_to_gid[gid] = g.id
    db.session.commit()

    # Enrich from boxscore_summary
    bpath = raw_dir / "boxscore_summary.csv"
    if bpath.exists():
        bdf = read_csv_normalized(bpath)
        for _, row in bdf.iterrows():
            r = row.to_dict()
            gid = cell_val(r, "game_id", "gameid")
            if not gid or gid not in fhm_to_gid:
                continue
            g = db.session.get(Game, fhm_to_gid[gid])
            if not g:
                continue
            g.arena = cell_val(r, "arena")
            g.attendance = to_int(cell_val(r, "attendance"))
            home_sog = _sum_int_cells(r, "sog_home_p1", "sog_home_p2", "sog_home_p3", "sog_home_ot")
            away_sog = _sum_int_cells(r, "sog_away_p1", "sog_away_p2", "sog_away_p3", "sog_away_ot")
            g.home_shots = home_sog if home_sog is not None else to_int(cell_val(r, "shots_home"))
            g.away_shots = away_sog if away_sog is not None else to_int(cell_val(r, "shots_away"))
            g.pim_home = to_int(cell_val(r, "pim_home"))
            g.pim_away = to_int(cell_val(r, "pim_away"))
            g.hits_home = to_int(cell_val(r, "hits_home"))
            g.hits_away = to_int(cell_val(r, "hits_away"))
            g.pp_goals_home = to_int(cell_val(r, "ppg_home"))
            g.pp_opp_home = to_int(cell_val(r, "ppo_home"))
            g.pp_goals_away = to_int(cell_val(r, "ppg_away"))
            g.pp_opp_away = to_int(cell_val(r, "ppo_away"))
            g.fhm_star1_player_id = to_int(cell_val(r, "star_1"))
            g.fhm_star2_player_id = to_int(cell_val(r, "star_2"))
            g.fhm_star3_player_id = to_int(cell_val(r, "star_3"))
            gt = cell_val(r, "type")
            if gt:
                g.game_type = gt
            y, m, d = cell_val(r, "date_year"), cell_val(r, "date_month"), cell_val(r, "date_day")
            gd = _parse_date(y, m, d)
            if gd:
                g.game_date = gd
    db.session.commit()
    return fhm_to_gid


def _clear_game_details() -> None:
    db.session.execute(delete(GameSkaterStat))
    db.session.execute(delete(GameGoalieStat))
    db.session.execute(delete(ScoringEvent))
    db.session.commit()


def ensure_players_from_boxscore_csvs(raw_dir: Path, players_fhm: dict[int, int]) -> int:
    """Create minimal Player rows for FHM ids that appear in box score CSVs but not in player_master.

    Some exports reference skaters/goalies in boxscore_*_summary.csv without listing them in
    player_master.csv; without this pass those game lines would be skipped.
    """
    needed: set[int] = set()
    for fname in ("boxscore_skater_summary.csv", "boxscore_goalie_summary.csv"):
        path = raw_dir / fname
        if not path.is_file():
            continue
        df = read_csv_normalized(path)
        for _, row in df.iterrows():
            r = row.to_dict()
            pid = to_int(cell_val(r, "playerid", "player_id"))
            if pid is not None and pid not in players_fhm:
                needed.add(pid)
    if not needed:
        return 0
    log.info(
        "Box scores reference %s player id(s) missing from player_master; adding placeholder rows.",
        len(needed),
    )
    n = 0
    for pid in sorted(needed):
        fhm_key = str(pid)
        full = f"Unknown player (FHM #{pid})"
        p = Player(
            fhm_player_id=fhm_key,
            first_name="Unknown",
            last_name=str(pid),
            full_name=full,
        )
        db.session.add(p)
        db.session.flush()
        players_fhm[pid] = p.id
        n += 1
    db.session.commit()
    return n


def ensure_players_from_draft_info(raw_dir: Path, players_fhm: dict[int, int]) -> int:
    """Create minimal Player rows for FHM ids in draft CSVs that are not in player_master / boxscores.

    Normally every draft_info playerid exists in player_master; this matches the boxscore safety net
    for edge-case exports.
    """
    needed: set[int] = set()
    for name in ("draft_info.csv", "draft_info_supplement.csv"):
        path = raw_dir / name
        if not path.is_file():
            continue
        df = read_csv_normalized(path)
        for _, row in df.iterrows():
            r = row.to_dict()
            pid = to_int(cell_val(r, "playerid", "player_id"))
            if pid is not None and pid not in players_fhm:
                needed.add(pid)
    if not needed:
        return 0
    log.info(
        "Draft CSVs reference %s player id(s) missing from player_master / boxscores; adding placeholder rows.",
        len(needed),
    )
    n = 0
    for pid in sorted(needed):
        fhm_key = str(pid)
        full = f"Unknown player (FHM #{pid})"
        p = Player(
            fhm_player_id=fhm_key,
            first_name="Unknown",
            last_name=str(pid),
            full_name=full,
        )
        db.session.add(p)
        db.session.flush()
        players_fhm[pid] = p.id
        n += 1
    db.session.commit()
    return n


def _iter_draft_info_dicts(raw_dir: Path):
    """Rows from draft_info.csv plus optional draft_info_supplement.csv (same columns)."""
    for name in ("draft_info.csv", "draft_info_supplement.csv"):
        path = raw_dir / name
        if not path.is_file():
            continue
        df = read_csv_normalized(path)
        for _, row in df.iterrows():
            yield row.to_dict()


def import_boxscore_skaters(raw_dir: Path, games_fhm: dict[str, int], players_fhm: dict[int, int], teams_fhm: dict[int, int]) -> int:
    path = raw_dir / "boxscore_skater_summary.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    batch = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        gid = cell_val(r, "game_id", "gameid")
        pid = to_int(cell_val(r, "playerid"))
        tid = to_int(cell_val(r, "teamid"))
        if not gid or gid not in games_fhm or pid not in players_fhm or tid not in teams_fhm:
            continue
        gs = db.session.scalars(
            select(GameSkaterStat).where(
                GameSkaterStat.game_id == games_fhm[gid],
                GameSkaterStat.player_id == players_fhm[pid],
            ).limit(1)
        ).first()
        if not gs:
            gs = GameSkaterStat(
                game_id=games_fhm[gid],
                player_id=players_fhm[pid],
                team_id=teams_fhm[tid],
            )
            db.session.add(gs)
        gs.goals = to_int(cell_val(r, "g"), 0) or 0
        gs.assists = to_int(cell_val(r, "a"), 0) or 0
        gs.shots = to_int(cell_val(r, "sog"), 0) or 0
        gs.pim = to_int(cell_val(r, "pim"), 0) or 0
        gs.plus_minus = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
        gs.game_rating = to_float(cell_val(r, "game_rating"))
        gs.hits = to_int(cell_val(r, "ht"))
        gs.blocked_shots = to_int(cell_val(r, "bs"))
        gs.missed_shots = to_int(cell_val(r, "ms"))
        gs.takeaways = to_int(cell_val(r, "tk"))
        gs.giveaways = to_int(cell_val(r, "gv"))
        gs.faceoffs_won = to_int(cell_val(r, "fow"))
        gs.faceoffs_lost = to_int(cell_val(r, "fol"))
        toi = to_int(cell_val(r, "tot"))
        gs.toi_seconds = toi
        n += 1
        batch += 1
        if batch >= 500:
            db.session.commit()
            batch = 0
    db.session.commit()
    return n


def import_boxscore_goalies(raw_dir: Path, games_fhm: dict[str, int], players_fhm: dict[int, int], teams_fhm: dict[int, int]) -> int:
    path = raw_dir / "boxscore_goalie_summary.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        gid = cell_val(r, "game_id", "gameid")
        pid = to_int(cell_val(r, "playerid"))
        tid = to_int(cell_val(r, "teamid"))
        if not gid or gid not in games_fhm or pid not in players_fhm or tid not in teams_fhm:
            continue
        gg = db.session.scalars(
            select(GameGoalieStat).where(
                GameGoalieStat.game_id == games_fhm[gid],
                GameGoalieStat.player_id == players_fhm[pid],
            ).limit(1)
        ).first()
        if not gg:
            gg = GameGoalieStat(
                game_id=games_fhm[gid],
                player_id=players_fhm[pid],
                team_id=teams_fhm[tid],
            )
            db.session.add(gg)
        sa = to_int(cell_val(r, "sa"), 0) or 0
        ga = to_int(cell_val(r, "ga"), 0) or 0
        sv = to_int(cell_val(r, "sv"), 0) or 0
        gg.shots_against = sa
        gg.goals_allowed = ga
        gg.saves = sv if sv else max(0, sa - ga)
        gg.game_rating = to_float(cell_val(r, "game_rating"))
        raw_sv = cell_val(r, "sv_pct", "sv_")
        if raw_sv and "nan" not in str(raw_sv).lower():
            pct = to_float(str(raw_sv).replace("%", ""))
            if pct is not None and pct > 1.5:
                pct = pct / 100.0
        gg.toi_seconds = _toi_seconds(cell_val(r, "toi"))
        n += 1
    db.session.commit()
    return n


def import_period_scoring(raw_dir: Path, games_fhm: dict[str, int], players_fhm: dict[int, int], teams_fhm: dict[int, int]) -> int:
    path = raw_dir / "boxscore_period_scoring_summary.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        gid = cell_val(r, "game_id", "gameid")
        if not gid or gid not in games_fhm:
            continue
        per = fhm_scoring_period_to_int(cell_val(r, "period"), 1)
        tsec = to_int(cell_val(r, "time"))
        scorer = to_int(cell_val(r, "scorer"))
        a1 = to_int(cell_val(r, "assist_1"))
        a2 = to_int(cell_val(r, "assist_2"))
        tm = to_int(cell_val(r, "teamid"))
        note = cell_val(r, "note")
        ev = ScoringEvent(
            game_id=games_fhm[gid],
            period=per,
            time_elapsed=_fmt_clock_seconds(tsec),
            scorer_player_id=players_fhm.get(scorer) if scorer else None,
            assist1_player_id=players_fhm.get(a1) if a1 else None,
            assist2_player_id=players_fhm.get(a2) if a2 else None,
            scoring_team_id=teams_fhm.get(tm) if tm is not None else None,
            strength=note,
        )
        db.session.add(ev)
        n += 1
        if n % 500 == 0:
            db.session.commit()
    db.session.commit()
    return n


def import_skater_segment(
    raw_dir: Path,
    fname: str,
    segment: str,
    season: Season,
    players_fhm: dict[int, int],
    teams_fhm: dict[int, int],
) -> int:
    path = raw_dir / fname
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        tid = to_int(cell_val(r, "teamid"))
        if pid is None or pid not in players_fhm:
            continue
        row_db = db.session.scalars(
            select(PlayerSkaterStat).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.player_id == players_fhm[pid],
                PlayerSkaterStat.stat_segment == segment,
            ).limit(1)
        ).first()
        if not row_db:
            row_db = PlayerSkaterStat(
                season_id=season.id,
                player_id=players_fhm[pid],
                stat_segment=segment,
            )
            db.session.add(row_db)
        row_db.team_id = teams_fhm.get(tid) if tid is not None else None
        g = to_int(cell_val(r, "g"), 0) or 0
        a = to_int(cell_val(r, "a"), 0) or 0
        row_db.gp = to_int(cell_val(r, "gp"), 0) or 0
        row_db.goals = g
        row_db.assists = a
        row_db.points = g + a
        row_db.pim = to_int(cell_val(r, "pim"), 0) or 0
        row_db.plus_minus = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
        row_db.shots = to_int(cell_val(r, "sog"))
        row_db.ppg = to_int(cell_val(r, "pp_g"))
        row_db.pp_assists = to_int(cell_val(r, "pp_a"))
        row_db.shg = to_int(cell_val(r, "sh_g"))
        row_db.sh_assists = to_int(cell_val(r, "sh_a"))
        row_db.gwg = to_int(cell_val(r, "gwg"))
        row_db.hits = to_int(cell_val(r, "hit"))
        row_db.blocked_shots = to_int(cell_val(r, "sb"))
        row_db.takeaways = to_int(cell_val(r, "tka"))
        row_db.giveaways = to_int(cell_val(r, "gva"))
        row_db.faceoffs = to_int(cell_val(r, "fo"))
        row_db.faceoff_wins = to_int(cell_val(r, "fow"))
        row_db.fights = to_int(cell_val(r, "fights"))
        row_db.fights_won = to_int(cell_val(r, "fights_won"))
        row_db.toi_seconds = to_int(cell_val(r, "toi"))
        row_db.ppto_seconds = to_int(cell_val(r, "pptoi"))
        row_db.shto_seconds = to_int(cell_val(r, "shtoi"))
        row_db.game_rating = to_float(cell_val(r, "gr"))
        row_db.game_rating_off = to_float(cell_val(r, "game_rating_off"))
        row_db.game_rating_def = to_float(cell_val(r, "game_rating_def"))
        row_db.pdo = to_float(cell_val(r, "pdo"))
        n += 1
        if n % 400 == 0:
            db.session.commit()
    db.session.commit()
    return n


def import_goalie_segment(
    raw_dir: Path,
    fname: str,
    segment: str,
    season: Season,
    players_fhm: dict[int, int],
    teams_fhm: dict[int, int],
) -> int:
    path = raw_dir / fname
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        tid = to_int(cell_val(r, "teamid"))
        if pid is None or pid not in players_fhm:
            continue
        row_db = db.session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.player_id == players_fhm[pid],
                PlayerGoalieStat.stat_segment == segment,
            ).limit(1)
        ).first()
        if not row_db:
            row_db = PlayerGoalieStat(
                season_id=season.id,
                player_id=players_fhm[pid],
                stat_segment=segment,
            )
            db.session.add(row_db)
        row_db.team_id = teams_fhm.get(tid) if tid is not None else None
        row_db.gp = to_int(cell_val(r, "games_played", "gp"), 0) or 0
        row_db.games_started = to_int(cell_val(r, "games_started"))
        row_db.minutes_played = to_int(cell_val(r, "minutes_played"))
        row_db.wins = to_int(cell_val(r, "wins"), 0) or 0
        row_db.losses = to_int(cell_val(r, "losses"), 0) or 0
        row_db.otl = to_int(cell_val(r, "ot"), 0) or 0
        row_db.ga = to_int(cell_val(r, "goals_against"), 0) or 0
        row_db.sa = to_int(cell_val(r, "shots_against"), 0) or 0
        row_db.so = to_int(cell_val(r, "shutouts"), 0) or 0
        row_db.gaa = to_float(cell_val(r, "goals_against_average"))
        raw_sp = cell_val(r, "save_percentage")
        if raw_sp and "nan" not in raw_sp.lower():
            sp = to_float(raw_sp.replace("%", ""))
            if sp is not None and sp > 1.5:
                sp = sp / 100.0
            row_db.sv_pct = sp
        row_db.game_rating = to_float(cell_val(r, "game_rating"))
        row_db.gsaa = to_float(
            cell_val(r, "gsaa", "goals_saved_above_average", "goals_saved_above_avg")
        )
        n += 1
    db.session.commit()
    return n


def _goalie_career_minutes(raw) -> int | None:
    """FHM goalie career 'Min' is minutes × 100 (fixed-point); always divide by 100.

    Values under 10_000 are still scaled (e.g. 3600 → 36 minutes for one appearance);
    the old `> 10000` branch left those rows inflated by ~100× and broke GAA.
    """
    v = to_int(raw)
    if v is None:
        return None
    if v <= 0:
        return 0
    return v // 100


def import_career_skater_file(
    raw_dir: Path,
    filename: str,
    career_source: str,
    players_fhm: dict[int, int],
    teams_fhm: dict[int, int],
) -> int:
    path = raw_dir / filename
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        year = to_int(cell_val(r, "year"))
        tm_fhm = to_int(cell_val(r, "team_id", "teamid"))
        lid = to_int(cell_val(r, "league_id", "leagueid"))
        if pid is None or year is None or tm_fhm is None or lid is None:
            continue
        if pid not in players_fhm:
            continue
        cl = db.session.scalars(
            select(PlayerSkaterCareerLine).where(
                PlayerSkaterCareerLine.player_id == players_fhm[pid],
                PlayerSkaterCareerLine.season_year == year,
                PlayerSkaterCareerLine.team_fhm_id == tm_fhm,
                PlayerSkaterCareerLine.league_fhm_id == lid,
                PlayerSkaterCareerLine.career_source == career_source,
            ).limit(1)
        ).first()
        if not cl:
            cl = PlayerSkaterCareerLine(
                player_id=players_fhm[pid],
                season_year=year,
                team_fhm_id=tm_fhm,
                league_fhm_id=lid,
                career_source=career_source,
            )
            db.session.add(cl)
        cl.team_id = teams_fhm.get(tm_fhm)
        cl.gp = to_int(cell_val(r, "gp"), 0) or 0
        cl.goals = to_int(cell_val(r, "g"), 0) or 0
        cl.assists = to_int(cell_val(r, "a"), 0) or 0
        cl.pim = to_int(cell_val(r, "pim"), 0) or 0
        cl.plus_minus = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
        cl.pp_goals = to_int(cell_val(r, "pp_g"))
        cl.pp_assists = to_int(cell_val(r, "pp_a"))
        cl.sh_goals = to_int(cell_val(r, "sh_g"))
        cl.sh_assists = to_int(cell_val(r, "sh_a"))
        cl.gwg = to_int(cell_val(r, "gwg"))
        cl.shots = to_int(cell_val(r, "sog"))
        cl.hits = to_int(cell_val(r, "hit"))
        cl.gva = to_int(cell_val(r, "gva"))
        cl.tka = to_int(cell_val(r, "tka"))
        cl.sb = to_int(cell_val(r, "sb"))
        cl.fights = to_int(cell_val(r, "fights"))
        cl.fights_won = to_int(cell_val(r, "fights_won"))
        cl.game_rating = to_float(cell_val(r, "gr", "game_rating"))
        n += 1
        if n % 500 == 0:
            db.session.commit()
    db.session.commit()
    return n


def import_career_goalie_file(
    raw_dir: Path,
    filename: str,
    career_source: str,
    players_fhm: dict[int, int],
    teams_fhm: dict[int, int],
) -> int:
    path = raw_dir / filename
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        year = to_int(cell_val(r, "year"))
        tm_fhm = to_int(cell_val(r, "team_id", "teamid"))
        lid = to_int(cell_val(r, "league_id", "leagueid"))
        if pid is None or year is None or tm_fhm is None or lid is None:
            continue
        if pid not in players_fhm:
            continue
        gl = db.session.scalars(
            select(PlayerGoalieCareerLine).where(
                PlayerGoalieCareerLine.player_id == players_fhm[pid],
                PlayerGoalieCareerLine.season_year == year,
                PlayerGoalieCareerLine.team_fhm_id == tm_fhm,
                PlayerGoalieCareerLine.league_fhm_id == lid,
                PlayerGoalieCareerLine.career_source == career_source,
            ).limit(1)
        ).first()
        if not gl:
            gl = PlayerGoalieCareerLine(
                player_id=players_fhm[pid],
                season_year=year,
                team_fhm_id=tm_fhm,
                league_fhm_id=lid,
                career_source=career_source,
            )
            db.session.add(gl)
        gl.team_id = teams_fhm.get(tm_fhm)
        gl.gp = to_int(cell_val(r, "gp"), 0) or 0
        gl.games_started = to_int(cell_val(r, "gs"))
        gl.minutes_played = _goalie_career_minutes(cell_val(r, "min"))
        gl.wins = to_int(cell_val(r, "w"), 0) or 0
        gl.losses = to_int(cell_val(r, "l"), 0) or 0
        gl.ties_otl = to_int(cell_val(r, "t_ol", "tol", "otl"))
        gl.empty_net_goals = to_int(cell_val(r, "eng"))
        gl.shutouts = to_int(cell_val(r, "so"), 0) or 0
        gl.goals_against = to_int(cell_val(r, "ga"), 0) or 0
        gl.shots_against = to_int(cell_val(r, "sa"), 0) or 0
        gl.game_rating = to_float(cell_val(r, "gr"))
        n += 1
        if n % 500 == 0:
            db.session.commit()
    db.session.commit()
    return n


def import_contracts(raw_dir: Path, players_fhm: dict[int, int]) -> int:
    path = raw_dir / "player_contract.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid"))
        if pid is None or pid not in players_fhm:
            continue
        pc = db.session.scalars(
            select(PlayerContract).where(PlayerContract.player_id == players_fhm[pid]).limit(1)
        ).first()
        if not pc:
            pc = PlayerContract(player_id=players_fhm[pid])
            db.session.add(pc)
        pc.fhm_team_id = to_int(cell_val(r, "team"))
        pc.average_salary = to_int(cell_val(r, "average_salary"))
        pc.has_ntc = (cell_val(r, "ntc") or "").lower() == "yes"
        pc.has_nmc = (cell_val(r, "nmc") or "").lower() == "yes"
        pc.is_elc = (cell_val(r, "elc") or "").lower() == "yes"
        pc.is_ufa = to_bool(cell_val(r, "ufa"))
        n += 1
    db.session.commit()
    return n


def import_drafts_fhm(raw_dir: Path, players_fhm: dict[int, int], teams_fhm: dict[int, int]) -> int:
    idx = raw_dir / "draft_index.csv"
    info = raw_dir / "draft_info.csv"
    if not idx.exists() or not info.exists():
        return 0
    for d in db.session.scalars(select(Draft).where(Draft.fhm_draft_id.isnot(None))).all():
        db.session.execute(delete(DraftPick).where(DraftPick.draft_id == d.id))
    db.session.commit()

    idf = read_csv_normalized(idx)
    draft_by_fhm: dict[int, int] = {}
    for _, row in idf.iterrows():
        r = row.to_dict()
        did = to_int(cell_val(r, "draftid", "draft_id"))
        label = cell_val(r, "draft_name", "name") or "Draft"
        if did is None:
            continue
        d = db.session.scalars(select(Draft).where(Draft.fhm_draft_id == did).limit(1)).first()
        if not d:
            d = Draft(fhm_draft_id=did, label=label)
            db.session.add(d)
            db.session.flush()
        else:
            d.label = label
        draft_by_fhm[did] = d.id
    db.session.commit()

    n = 0
    for r in _iter_draft_info_dicts(raw_dir):
        pid = to_int(cell_val(r, "playerid", "player_id"))
        did = to_int(cell_val(r, "draftid", "draft_id"))
        if did is None or did not in draft_by_fhm or pid is None or pid not in players_fhm:
            continue
        overall = to_int(cell_val(r, "overall"), 0) or 0
        pk = DraftPick(
            draft_id=draft_by_fhm[did],
            overall_pick=overall,
            round=to_int(cell_val(r, "round")),
            team_id=teams_fhm.get(to_int(cell_val(r, "tam"))),
            player_id=players_fhm[pid],
            draft_year=to_int(cell_val(r, "year")),
            fhm_picked_from_team_id=to_int(cell_val(r, "picked_from")),
        )
        db.session.add(pk)
        n += 1
    db.session.commit()
    return n


def import_player_jersey_numbers(raw_dir: Path, players_fhm: dict[int, int]) -> int:
    """Optional ``player_jersey_numbers.csv``: FHM PlayerId + jersey (overrides ``player_master``)."""
    path = raw_dir / "player_jersey_numbers.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = to_int(cell_val(r, "playerid", "player_id"))
        if pid is None or pid not in players_fhm:
            continue
        jn = to_int(cell_val(r, "jersey", "jersey_number", "number"))
        if jn is None:
            continue
        p = db.session.get(Player, players_fhm[pid])
        if not p:
            continue
        p.jersey_number = jn
        n += 1
    db.session.commit()
    return n


def run_fhm_import(raw_dir: Path, app, league_filter: int = 0) -> dict[str, int]:
    """Import FHM-style CSV set. Requires fresh schema (stat_segment, etc.)."""
    counts: dict[str, int] = {}
    div_map = load_division_names(raw_dir)
    counts["league_meta"] = import_league_meta(raw_dir, league_filter)
    season, league_year_changed = ensure_season(raw_dir, league_filter)
    if league_year_changed:
        log.info(
            "FHM schedule span changed (season_id=%s start_year=%s end_year=%s).",
            int(season.id),
            season.start_year,
            season.end_year,
        )
    teams_fhm = import_fhm_teams(raw_dir, league_filter, div_map)
    counts["teams"] = len(teams_fhm)
    players_fhm = import_players(raw_dir, teams_fhm)
    counts["players"] = len(players_fhm)
    box_extra = ensure_players_from_boxscore_csvs(raw_dir, players_fhm)
    if box_extra:
        counts["players"] = len(players_fhm)
        counts["players_from_boxscores_only"] = box_extra
    counts["player_jersey_numbers"] = import_player_jersey_numbers(raw_dir, players_fhm)
    counts["ratings"] = import_ratings(raw_dir, players_fhm)
    counts["standings"] = import_standings(raw_dir, season, teams_fhm, div_map, league_filter)
    counts["team_aggregates_rs"] = import_team_season_stats(
        raw_dir, season, teams_fhm, league_filter, filename="team_stats.csv", stat_segment="rs"
    )
    counts["team_aggregates_po"] = import_team_season_stats(
        raw_dir, season, teams_fhm, league_filter, filename="team_stats_playoffs.csv", stat_segment="po"
    )
    games_fhm = import_games(raw_dir, season, teams_fhm, league_filter)
    counts["games"] = len(games_fhm)

    _clear_game_details()
    counts["box_skaters"] = import_boxscore_skaters(raw_dir, games_fhm, players_fhm, teams_fhm)
    counts["box_goalies"] = import_boxscore_goalies(raw_dir, games_fhm, players_fhm, teams_fhm)
    counts["scoring_events"] = import_period_scoring(raw_dir, games_fhm, players_fhm, teams_fhm)

    # One reused ``Season`` row per FHM mount: ``import_skater_segment`` only overwrites rows
    # present in each CSV. After a rolled year, ``ensure_season``'s start/end years may not
    # change if ``schedules.csv`` still spans overlapping calendar years, so always wipe
    # season-scoped aggregates before reloading the bundle (semicolon exports are full-file).
    sid = int(season.id)
    db.session.execute(delete(PlayerSkaterStat).where(PlayerSkaterStat.season_id == sid))
    db.session.execute(delete(PlayerGoalieStat).where(PlayerGoalieStat.season_id == sid))
    db.session.commit()
    log.info("Cleared player_skater_stats / player_goalie_stats for season_id=%s before FHM segment import.", sid)

    for fname, seg in [
        ("player_skater_stats_rs.csv", "rs"),
        ("player_skater_stats_ps.csv", "ps"),
        ("player_skater_stats_po.csv", "po"),
    ]:
        counts[f"skater_{seg}"] = import_skater_segment(raw_dir, fname, seg, season, players_fhm, teams_fhm)
    for fname, seg in [
        ("player_goalie_stats_rs.csv", "rs"),
        ("player_goalie_stats_ps.csv", "ps"),
        ("player_goalie_stats_po.csv", "po"),
    ]:
        counts[f"goalie_{seg}"] = import_goalie_segment(raw_dir, fname, seg, season, players_fhm, teams_fhm)

    sk_career_files = [
        ("player_skater_career_stats_rs.csv", "rs"),
        ("player_skater_career_stats_po.csv", "po"),
        ("player_skater_retired_career_stats_rs.csv", "retired_rs"),
        ("player_skater_retired_career_stats_ps.csv", "retired_ps"),
        ("player_skater_retired_career_stats_po.csv", "retired_po"),
    ]
    for fname, src in sk_career_files:
        counts[f"career_skater_{src}"] = import_career_skater_file(
            raw_dir, fname, src, players_fhm, teams_fhm
        )
    gk_career_files = [
        ("player_goalie_career_stats_rs.csv", "rs"),
        ("player_goalie_career_stats_ps.csv", "ps"),
        ("player_goalie_career_stats_po.csv", "po"),
        ("player_goalie_retired_career_stats_rs.csv", "retired_rs"),
        ("player_goalie_retired_career_stats_ps.csv", "retired_ps"),
        ("player_goalie_retired_career_stats_po.csv", "retired_po"),
    ]
    for fname, src in gk_career_files:
        counts[f"career_goalie_{src}"] = import_career_goalie_file(
            raw_dir, fname, src, players_fhm, teams_fhm
        )
    counts["contracts"] = import_contracts(raw_dir, players_fhm)
    d_extra = ensure_players_from_draft_info(raw_dir, players_fhm)
    if d_extra:
        counts["players_from_draft_csvs_only"] = d_extra
        counts["players"] = len(players_fhm)
    counts["draft"] = import_drafts_fhm(raw_dir, players_fhm, teams_fhm)
    return counts


def is_fhm_export_dir(raw_dir: Path) -> bool:
    return team_data_csv_path(raw_dir) is not None

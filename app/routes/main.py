"""Server-rendered pages for the Boys of Winter League site."""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, url_for
from sqlalchemy import case, cast, Float, func, not_, nulls_last, or_, select
from sqlalchemy.orm import joinedload

from app.config import BASE_DIR, Config
from app.models import (
    Draft,
    DraftPick,
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAward,
    Player,
    PlayerContract,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Prospect,
    Team,
    TeamSeasonAggregate,
    TeamStanding,
    db,
)
from app.services.all_time_records import (
    bowl_nhl_league_ids,
    default_goalie_sort_order,
    default_skater_sort_order,
    fetch_goalie_all_time,
    fetch_skater_all_time,
)
from app.services.roster_team import main_league_roster_team
from app.services.draft_history import (
    build_career_stat_maps,
    draft_row_stat_mode,
    fetch_nhl_bowl_draft_years,
    fetch_nhl_bowl_picks_for_year,
    group_picks_by_round,
    nhl_bowl_draft_clause,
)
from app.services.import_career_seasons import import_folder_season_labels
from app.services.player_contract_csv import contract_years_remaining_major
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_ratings_csv import get_player_ratings_row
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.standings import (
    conferences_for_season,
    divisions_for_season,
    standings_for_season,
    team_aggregate_rows,
)

main_bp = Blueprint("main", __name__)


# Banner / Banner1.png / banner 1.png / .PNG — case-insensitive; optional space before digits
_BANNER_FILE_RE = re.compile(r"^banner\s*(\d+)\.png$", re.IGNORECASE)


def champion_banner_urls() -> list[str]:
    """All ``Banner<N>.png`` / ``banner<N>.png`` files under the active league champions folder.

    Sorted by *N*. Gaps are allowed. Extension ``.png`` is case-insensitive. Filenames are NFC-normalized
    so lookalike Unicode does not prevent matches. Uses each file's real on-disk name in URLs.
    """
    rel = str(current_app.config.get("HISTORY_CHAMPIONS_REL_DIR", "img/history/champions")).strip("/\\")
    primary_dir = (BASE_DIR / "app" / "static" / Path(rel)).resolve()
    legacy_rel = "img/history/champions"
    legacy_dir = (BASE_DIR / "app" / "static" / legacy_rel).resolve()

    def _scan(folder: Path) -> list[tuple[int, str]]:
        if not folder.is_dir():
            return []
        found_local: list[tuple[int, str]] = []
        for p in folder.iterdir():
            if not p.is_file():
                continue
            safe_name = unicodedata.normalize("NFC", p.name)
            m = _BANNER_FILE_RE.match(safe_name)
            if m:
                found_local.append((int(m.group(1)), p.name))
        found_local.sort(key=lambda t: t[0])
        return found_local

    found = _scan(primary_dir)
    out_rel = rel
    # Backward compatibility while league folders are being populated.
    if not found and primary_dir != legacy_dir:
        found = _scan(legacy_dir)
        out_rel = legacy_rel

    return [url_for("static", filename=f"{out_rel}/{name}") for _, name in found]


@main_bp.get("/")
def home():
    return render_template("home.html")


@main_bp.get("/standings")
def standings():
    season = get_current_season()
    view = request.args.get("view", "overall")
    conf = request.args.get("conference")
    div = request.args.get("division")
    conferences = conferences_for_season(season)
    conf_names = [c.name for c in conferences if getattr(c, "name", None)] if conferences and hasattr(conferences[0], "name") else list(conferences or [])
    conf_name_by_id: dict[int, str] = {}
    conf_csv = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "conferences.csv"
    if conf_csv.is_file():
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
                    conf_name_by_id[cid] = label or nm
        except Exception:
            conf_name_by_id = {}
    if conf_name_by_id:
        conf_names = [conf_name_by_id[k] for k in sorted(conf_name_by_id.keys())]
    div_name_by_pair: dict[tuple[int, int], str] = {}
    div_name_by_id: dict[int, str] = {}
    div_csv = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "divisions.csv"
    if div_csv.is_file():
        try:
            with div_csv.open("r", encoding="utf-8-sig", newline="") as f:
                sample = f.read(2048)
                f.seek(0)
                delim = ";" if sample.count(";") >= sample.count(",") else ","
                reader = csv.DictReader(f, delimiter=delim)
                for row in reader:
                    lid = (row.get("League Id") or row.get("league_id") or "").strip()
                    if lid and lid != "0":
                        continue
                    did = (row.get("Division Id") or row.get("division_id") or "").strip()
                    cid = (row.get("Conference Id") or row.get("conference_id") or "").strip()
                    nm = (row.get("Name") or row.get("name") or "").strip()
                    if not did or not nm:
                        continue
                    try:
                        div_id = int(did)
                    except ValueError:
                        continue
                    try:
                        conf_id = int(cid) if cid else -9999
                    except ValueError:
                        conf_id = -9999
                    if conf_id != -9999:
                        div_name_by_pair[(conf_id, div_id)] = nm
                    # Keep first-seen fallback by division id when conference id is unavailable.
                    if div_id not in div_name_by_id:
                        div_name_by_id[div_id] = nm
        except Exception:
            div_name_by_pair = {}
            div_name_by_id = {}
    divisions = divisions_for_season(season)
    division_names: list[str] = list(divisions or [])
    if view == "conference":
        # Enable conference view for Fantasy/Cap (and any league with conference data).
        # If no conference is selected, show all rows in conference-grouped context.
        selected_conf = conf if (not conf_names or conf in conf_names) else None
        rows = standings_for_season(season, conference=selected_conf)
    elif view == "division":
        # Division view uses actual division names from league data.
        rows = standings_for_season(season)
    else:
        rows = standings_for_season(
            season,
            conference=None,
            division=None,
        )
    team_stat_rows_rs = team_aggregate_rows(season, rows, "rs")
    team_stat_rows_po = team_aggregate_rows(season, rows, "po")

    # Some league exports leave TeamStanding.conference empty but populate team.fhm_conference_id.
    # Add a display fallback so CONF / DIV shows conference names for Fantasy/Cap.
    conf_ids = sorted(
        {
            int(st.team.fhm_conference_id)
            for st in rows
            if st.team is not None and st.team.fhm_conference_id is not None
        }
    )
    conf_id_to_label: dict[int, str] = {}
    if conf_ids:
        if conf_name_by_id:
            for cid in conf_ids:
                if cid in conf_name_by_id:
                    conf_id_to_label[cid] = conf_name_by_id[cid]
        elif conf_names and len(conf_names) >= len(conf_ids):
            # Prefer names from imported conference data when present.
            for idx, cid in enumerate(conf_ids):
                conf_id_to_label[cid] = conf_names[idx]
        elif len(conf_ids) >= 2:
            conf_id_to_label[conf_ids[0]] = "East"
            conf_id_to_label[conf_ids[-1]] = "West"
    for st in rows:
        label = (st.conference or "").strip()
        if not label and st.team is not None and st.team.fhm_conference_id is not None:
            label = conf_id_to_label.get(int(st.team.fhm_conference_id), "")
        setattr(st, "conference_label", label)
        div_label = (st.division or "").strip()
        if st.team is not None and st.team.fhm_division_id is not None:
            did = int(st.team.fhm_division_id)
            cid = int(st.team.fhm_conference_id) if st.team.fhm_conference_id is not None else None
            if cid is not None and (cid, did) in div_name_by_pair:
                div_label = div_name_by_pair[(cid, did)]
            elif did in div_name_by_id:
                div_label = div_name_by_id[did]
        setattr(st, "division_label", div_label)

    # Prefer division names from CSV mapping for tabs; fallback to labels present in rows.
    if div_name_by_pair or div_name_by_id:
        seen_divs: set[str] = set()
        ordered: list[str] = []
        for _, nm in sorted(div_name_by_pair.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if nm and nm not in seen_divs:
                seen_divs.add(nm)
                ordered.append(nm)
        for _, nm in sorted(div_name_by_id.items(), key=lambda kv: kv[0]):
            if nm and nm not in seen_divs:
                seen_divs.add(nm)
                ordered.append(nm)
        if ordered:
            division_names = ordered
    if not division_names:
        division_names = sorted({(getattr(st, "division_label", "") or "").strip() for st in rows if (getattr(st, "division_label", "") or "").strip()})

    if view == "division":
        selected_div = div if div in division_names else None
        if selected_div:
            rows = [st for st in rows if (getattr(st, "division_label", "") or "").strip() == selected_div]

    return render_template(
        "standings.html",
        season=season,
        standings=rows,
        team_stat_rows_rs=team_stat_rows_rs,
        team_stat_rows_po=team_stat_rows_po,
        view=view,
        conferences=conferences,
        conference_names=conf_names,
        divisions=divisions,
        division_names=division_names,
        sel_conference=conf,
        sel_division=div,
    )


@main_bp.get("/statistics")
def statistics():
    season = get_current_season()
    team_id = request.args.get("team_id", type=int)
    sort = request.args.get("sort", "points")
    goalies = request.args.get("goalies") == "1"
    segment = request.args.get("segment", "rs") or "rs"
    if segment not in ("rs", "ps", "po"):
        segment = "rs"
    pos_filter = request.args.get("pos", "all") or "all"
    if pos_filter not in ("all", "fwd", "def", "c", "lw", "rw"):
        pos_filter = "all"
    stats_expanded = request.args.get("expanded") == "1"
    stats_page_limit = 100
    stats_full_limit = 8000

    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    teams_by_id = {t.id: t for t in teams}
    if not season:
        return render_template(
            "statistics.html",
            season=None,
            teams=teams,
            teams_by_id=teams_by_id,
            skaters=[],
            goalies_list=[],
            sort=sort,
            g_sort="wins",
            team_id=team_id,
            show_goalies=goalies,
            segment=segment,
            pos_filter=pos_filter,
            stats_expanded=False,
            total_skaters=0,
            total_goalies=0,
            statistics_expand_url="",
            statistics_collapsed_url="",
            stats_page_limit=stats_page_limit,
        )

    sk_gp_nf = func.nullif(PlayerSkaterStat.gp, 0)
    sk_es_sec = PlayerSkaterStat.toi_seconds - func.coalesce(
        PlayerSkaterStat.ppto_seconds, 0
    ) - func.coalesce(PlayerSkaterStat.shto_seconds, 0)
    sk_toi_avg = case(
        (PlayerSkaterStat.gp > 0, cast(PlayerSkaterStat.toi_seconds, Float) / sk_gp_nf),
        else_=None,
    )
    sk_es_toi_avg = case(
        (PlayerSkaterStat.gp > 0, cast(sk_es_sec, Float) / sk_gp_nf),
        else_=None,
    )
    sk_ppto_avg = case(
        (
            PlayerSkaterStat.gp > 0,
            cast(func.coalesce(PlayerSkaterStat.ppto_seconds, 0), Float) / sk_gp_nf,
        ),
        else_=None,
    )
    sk_shto_avg = case(
        (
            PlayerSkaterStat.gp > 0,
            cast(func.coalesce(PlayerSkaterStat.shto_seconds, 0), Float) / sk_gp_nf,
        ),
        else_=None,
    )
    sk_order_map: dict[str, object] = {
        "rank": (
            PlayerSkaterStat.points.desc(),
            PlayerSkaterStat.goals.desc(),
            PlayerSkaterStat.assists.desc(),
            Player.full_name.asc(),
        ),
        "points": PlayerSkaterStat.points.desc(),
        "goals": PlayerSkaterStat.goals.desc(),
        "assists": PlayerSkaterStat.assists.desc(),
        "gp": PlayerSkaterStat.gp.desc(),
        "pim": PlayerSkaterStat.pim.desc(),
        "hits": PlayerSkaterStat.hits.desc().nulls_last(),
        "shots": PlayerSkaterStat.shots.desc().nulls_last(),
        "plus_minus": PlayerSkaterStat.plus_minus.desc().nulls_last(),
        "gr": PlayerSkaterStat.game_rating.desc().nulls_last(),
        "player": Player.full_name.asc(),
        "abi": Player.overall_ability.desc().nulls_last(),
        "pot": Player.overall_potential.desc().nulls_last(),
        "blocked_shots": PlayerSkaterStat.blocked_shots.desc().nulls_last(),
        "toi": sk_toi_avg.desc().nulls_last(),
        "ppto": sk_ppto_avg.desc().nulls_last(),
        "shto": sk_shto_avg.desc().nulls_last(),
        "es_toi": sk_es_toi_avg.desc().nulls_last(),
        "ppg": PlayerSkaterStat.ppg.desc().nulls_last(),
        "ppa": PlayerSkaterStat.pp_assists.desc().nulls_last(),
        "shg": PlayerSkaterStat.shg.desc().nulls_last(),
        "sha": PlayerSkaterStat.sh_assists.desc().nulls_last(),
        "gwg": PlayerSkaterStat.gwg.desc().nulls_last(),
    }
    if sort not in sk_order_map:
        sort = "points"

    sk_q = (
        select(PlayerSkaterStat, Player)
        .join(Player, PlayerSkaterStat.player_id == Player.id)
        .where(
            PlayerSkaterStat.season_id == season.id,
            PlayerSkaterStat.stat_segment == segment,
        )
    )
    if team_id:
        sk_q = sk_q.where(PlayerSkaterStat.team_id == team_id)

    def_pos = or_(
        Player.position.in_(("D", "LD", "RD")),
        Player.position.like("D %"),
        Player.position.like("% D"),
    )
    pos_c = or_(
        Player.position == "C",
        Player.position.like("C %"),
        Player.position.like("C-%"),
        Player.position.like("% - C"),
    )
    pos_lw = or_(
        Player.position == "LW",
        Player.position.like("LW %"),
        Player.position.like("LW-%"),
        Player.position.like("%LW%"),
    )
    pos_rw = or_(
        Player.position == "RW",
        Player.position.like("RW %"),
        Player.position.like("RW-%"),
        Player.position.like("%RW%"),
    )
    if pos_filter == "def":
        sk_q = sk_q.where(def_pos)
    elif pos_filter == "fwd":
        sk_q = sk_q.where(not_(def_pos))
    elif pos_filter == "c":
        sk_q = sk_q.where(pos_c)
    elif pos_filter == "lw":
        sk_q = sk_q.where(pos_lw)
    elif pos_filter == "rw":
        sk_q = sk_q.where(pos_rw)

    total_skaters = db.session.scalar(select(func.count()).select_from(sk_q.subquery())) or 0
    _sk_ord = sk_order_map[sort]
    if isinstance(_sk_ord, tuple):
        sk_q = sk_q.order_by(*_sk_ord, Player.id.asc())
    else:
        sk_q = sk_q.order_by(_sk_ord, Player.id.asc())
    if not stats_expanded:
        sk_q = sk_q.limit(stats_page_limit)
    else:
        sk_q = sk_q.limit(stats_full_limit)
    skaters = db.session.execute(sk_q).all()

    gq = (
        select(PlayerGoalieStat, Player)
        .join(Player, PlayerGoalieStat.player_id == Player.id)
        .where(
            PlayerGoalieStat.season_id == season.id,
            PlayerGoalieStat.stat_segment == segment,
        )
    )
    if team_id:
        gq = gq.where(PlayerGoalieStat.team_id == team_id)
    g_sort = request.args.get("g_sort", "wins")
    g_gp_nf = func.nullif(PlayerGoalieStat.gp, 0)
    g_atoi_avg = case(
        (
            PlayerGoalieStat.gp > 0,
            cast(func.coalesce(PlayerGoalieStat.minutes_played, 0) * 60, Float) / g_gp_nf,
        ),
        else_=None,
    )
    g_order_map: dict[str, object] = {
        "rank": (
            PlayerGoalieStat.wins.desc(),
            PlayerGoalieStat.gp.desc(),
            Player.full_name.asc(),
        ),
        "wins": PlayerGoalieStat.wins.desc(),
        "gp": PlayerGoalieStat.gp.desc(),
        "games_started": PlayerGoalieStat.games_started.desc().nulls_last(),
        "losses": PlayerGoalieStat.losses.desc(),
        "otl": PlayerGoalieStat.otl.desc(),
        "ga": PlayerGoalieStat.ga.asc(),
        "sa": PlayerGoalieStat.sa.desc(),
        "so": PlayerGoalieStat.so.desc(),
        "sv_pct": PlayerGoalieStat.sv_pct.desc().nulls_last(),
        "gaa": PlayerGoalieStat.gaa.asc().nulls_last(),
        "gr": PlayerGoalieStat.game_rating.desc().nulls_last(),
        "player": Player.full_name.asc(),
        "abi": Player.overall_ability.desc().nulls_last(),
        "pot": Player.overall_potential.desc().nulls_last(),
        "atoi": g_atoi_avg.desc().nulls_last(),
    }
    if g_sort not in g_order_map:
        g_sort = "wins"
    total_goalies = db.session.scalar(select(func.count()).select_from(gq.subquery())) or 0
    _g_ord = g_order_map[g_sort]
    if isinstance(_g_ord, tuple):
        gq = gq.order_by(*_g_ord, Player.id.asc())
    else:
        gq = gq.order_by(_g_ord, Player.id.asc())
    if not stats_expanded:
        gq = gq.limit(stats_page_limit)
    else:
        gq = gq.limit(stats_full_limit)
    goalies_list = db.session.execute(gq).all()

    _stat_params = {
        "segment": segment,
        "sort": sort,
        "g_sort": g_sort,
        "team_id": team_id,
        "pos": pos_filter if pos_filter != "all" else None,
        "goalies": 1 if goalies else None,
    }
    _stat_params = {k: v for k, v in _stat_params.items() if v is not None}
    statistics_expand_url = url_for("main.statistics", **{**_stat_params, "expanded": 1})
    statistics_collapsed_url = url_for("main.statistics", **_stat_params)

    return render_template(
        "statistics.html",
        season=season,
        teams=teams,
        teams_by_id=teams_by_id,
        skaters=skaters,
        goalies_list=goalies_list,
        sort=sort,
        g_sort=g_sort,
        team_id=team_id,
        show_goalies=goalies,
        segment=segment,
        pos_filter=pos_filter,
        stats_expanded=stats_expanded,
        total_skaters=total_skaters,
        total_goalies=total_goalies,
        statistics_expand_url=statistics_expand_url,
        statistics_collapsed_url=statistics_collapsed_url,
        stats_page_limit=stats_page_limit,
    )


@main_bp.get("/schedule")
def schedule():
    season = get_current_season()
    team_filter = request.args.get("team")
    tab = request.args.get("tab", "recent")
    game_type = request.args.get("game_type", "").strip()
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    team_obj = None
    if team_filter:
        team_obj = db.session.scalars(
            select(Team).where(Team.slug == team_filter).limit(1)
        ).first()
    if not season:
        return render_template(
            "schedule.html",
            season=None,
            games=[],
            teams=teams,
            tab=tab,
            team_obj=team_obj,
            game_type=game_type,
            game_types=[],
        )
    q = select(Game).options(
        joinedload(Game.home_team),
        joinedload(Game.away_team),
    ).where(Game.season_id == season.id)
    if team_obj:
        q = q.where((Game.home_team_id == team_obj.id) | (Game.away_team_id == team_obj.id))
    if game_type:
        q = q.where(Game.game_type == game_type)
    if tab == "upcoming":
        q = q.where(Game.status != "final").order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
    else:
        q = q.where(Game.status == "final").order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
    games = db.session.scalars(q.limit(120)).all()
    gt_rows = db.session.scalars(
        select(Game.game_type)
        .where(Game.season_id == season.id, Game.game_type.isnot(None))
        .distinct()
        .order_by(Game.game_type)
    ).all()
    game_types = [g for g in gt_rows if g]
    return render_template(
        "schedule.html",
        season=season,
        games=games,
        teams=teams,
        tab=tab,
        team_obj=team_obj,
        game_type=game_type,
        game_types=game_types,
    )


@main_bp.get("/history")
def history():
    awards = db.session.scalars(
        select(HistoryAward)
        .options(
            joinedload(HistoryAward.season),
            joinedload(HistoryAward.player),
            joinedload(HistoryAward.team),
        )
        .order_by(HistoryAward.season_id.desc())
        .limit(200)
    ).all()
    seasons_on_file = import_folder_season_labels()
    champion_banners = champion_banner_urls()
    return render_template(
        "history.html",
        awards=awards,
        seasons_on_file=seasons_on_file,
        champion_banners=champion_banners,
    )


@main_bp.get("/records")
def all_time_records():
    split = request.args.get("split", "rs") or "rs"
    if split not in ("rs", "po"):
        split = "rs"
    show_goalies = request.args.get("goalies") == "1"
    expanded = request.args.get("expanded") == "1"
    records_page_limit = 100
    sort = request.args.get("sort", "points")
    order = request.args.get("order")
    g_sort = request.args.get("g_sort", "wins")
    g_order = request.args.get("g_order")
    if show_goalies:
        goalie_rows_all, g_sort_used, g_order_used = fetch_goalie_all_time(
            db.session, split, g_sort, g_order or ""
        )
        total_goalies = len(goalie_rows_all)
        goalie_rows = goalie_rows_all if expanded else goalie_rows_all[:records_page_limit]
        skater_rows = []
        total_skaters = 0
        sort_used = sort
        sk_order_used = (
            order
            if order in ("asc", "desc")
            else default_skater_sort_order(sort_used)
        )
    else:
        skater_rows_all, sort_used, sk_order_used = fetch_skater_all_time(
            db.session, split, sort, order or ""
        )
        total_skaters = len(skater_rows_all)
        skater_rows = skater_rows_all if expanded else skater_rows_all[:records_page_limit]
        goalie_rows = []
        total_goalies = 0
        g_sort_used = g_sort
        g_order_used = (
            g_order
            if g_order in ("asc", "desc")
            else default_goalie_sort_order(g_sort_used)
        )
    return render_template(
        "records.html",
        show_goalies=show_goalies,
        split=split,
        expanded=expanded,
        records_page_limit=records_page_limit,
        total_skaters=total_skaters,
        total_goalies=total_goalies,
        sort=sort_used,
        sk_order=sk_order_used,
        g_sort=g_sort_used,
        g_order=g_order_used,
        skater_rows=skater_rows,
        goalie_rows=goalie_rows,
    )


def _prospect_pos_matches(player_position: str | None, wanted: str | None) -> bool:
    if not wanted:
        return True
    p = (player_position or "").strip().upper()
    w = wanted.strip().upper()
    if w == "D":
        return p in ("D", "LD", "RD")
    return p == w


def _prospect_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and val != val:  # NaN
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


@main_bp.get("/prospects")
def prospects():
    team_slug = request.args.get("team")
    pos = request.args.get("position")
    prospect_expanded = request.args.get("expanded") == "1"
    prospect_page_limit = 50
    session = db.session
    league_ids = bowl_nhl_league_ids(session)
    age_ref = season_age_reference_date(get_current_season())

    # (full name for header tooltip, column abbreviation, ratings CSV key)
    overview_headers = (
        ("Skating", "SKT", "skating"),
        ("Shooting", "SHT", "shooting"),
        ("Playmaking", "PLM", "playmaking"),
        ("Defending", "DEF", "defending"),
        ("Physicality", "PHY", "physicality"),
        ("Conditioning", "CON", "conditioning"),
        ("Character", "CHR", "character"),
        ("Hockey sense", "HSN", "hockey_sense"),
    )
    attr_sort_keys = frozenset(h[2] for h in overview_headers)
    valid_sorts = frozenset({"player", "abi", "pot", *attr_sort_keys})
    sort_default_desc = frozenset({"abi", "pot", *attr_sort_keys})

    sort_col = request.args.get("sort") or "pot"
    order = request.args.get("order") or "desc"
    if sort_col not in valid_sorts:
        sort_col = "pot"
    if order not in ("asc", "desc"):
        order = "desc"

    season = get_current_season()
    q = select(Player).options(joinedload(Player.current_team)).where(
        Player.retired.is_(False),
        Player.birth_date.isnot(None),
    )
    players = session.scalars(q).unique().all()

    # Fallback team resolution for players whose current_team_id is NULL:
    # infer team from current-season skater/goalie rows (highest GP).
    resolved_team_by_player_id: dict[int, Team | None] = {}
    missing_ids = [p.id for p in players if p.current_team is None]
    if missing_ids and season:
        inferred_team_id: dict[int, tuple[int, int]] = {}  # player_id -> (gp, team_id)
        sk_rows = session.execute(
            select(
                PlayerSkaterStat.player_id,
                PlayerSkaterStat.team_id,
                PlayerSkaterStat.gp,
            ).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.player_id.in_(missing_ids),
                PlayerSkaterStat.team_id.isnot(None),
            )
        ).all()
        for pid, tid, gp in sk_rows:
            if tid is None:
                continue
            gpv = int(gp or 0)
            prev = inferred_team_id.get(int(pid))
            if prev is None or gpv > prev[0]:
                inferred_team_id[int(pid)] = (gpv, int(tid))
        goalie_rows = session.execute(
            select(
                PlayerGoalieStat.player_id,
                PlayerGoalieStat.team_id,
                PlayerGoalieStat.gp,
            ).where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.player_id.in_(missing_ids),
                PlayerGoalieStat.team_id.isnot(None),
            )
        ).all()
        for pid, tid, gp in goalie_rows:
            if tid is None:
                continue
            gpv = int(gp or 0)
            prev = inferred_team_id.get(int(pid))
            if prev is None or gpv > prev[0]:
                inferred_team_id[int(pid)] = (gpv, int(tid))
        team_ids = sorted({v[1] for v in inferred_team_id.values()})
        teams_map = {
            t.id: t
            for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        } if team_ids else {}
        for pid, (_gp, tid) in inferred_team_id.items():
            resolved_team_by_player_id[pid] = teams_map.get(tid)

    # Final fallback: player_rights.csv (PlayerId -> Team FHM id) for prospects not on active roster.
    unresolved_ids = [p.id for p in players if p.current_team is None and p.id not in resolved_team_by_player_id]
    if unresolved_ids:
        try:
            rights_path = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "player_rights.csv"
            if rights_path.is_file():
                pid_to_fhm_team: dict[int, str] = {}
                with rights_path.open("r", encoding="utf-8-sig", newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    delim = ";" if sample.count(";") >= sample.count(",") else ","
                    reader = csv.DictReader(f, delimiter=delim)
                    for row in reader:
                        pid_s = (row.get("PlayerId") or row.get("playerid") or "").strip()
                        tid_s = (row.get("Team") or row.get("team") or "").strip()
                        if not pid_s or not tid_s:
                            continue
                        try:
                            pid = int(pid_s)
                        except ValueError:
                            continue
                        if pid not in unresolved_ids:
                            continue
                        pid_to_fhm_team[pid] = tid_s
                if pid_to_fhm_team:
                    want_fhm_ids = {v for v in pid_to_fhm_team.values() if v}
                    fhm_team_map: dict[str, Team] = {}
                    if want_fhm_ids:
                        t_rows = session.scalars(
                            select(Team).where(Team.fhm_team_id.in_(want_fhm_ids))
                        ).all()
                        fhm_team_map = {str(t.fhm_team_id): t for t in t_rows if t.fhm_team_id is not None}
                    for pid, fhm_tid in pid_to_fhm_team.items():
                        tm = fhm_team_map.get(str(fhm_tid))
                        if tm is not None:
                            resolved_team_by_player_id[pid] = tm
        except Exception:
            pass

    selected_team = None
    if team_slug:
        selected_team = session.scalars(select(Team).where(Team.slug == team_slug).limit(1)).first()

    def _effective_team(pl: Player) -> Team | None:
        return pl.current_team or resolved_team_by_player_id.get(pl.id)
    young: list[Player] = []
    for p in players:
        eff_team = _effective_team(p)
        if not eff_team or eff_team.fhm_league_id not in league_ids:
            continue
        if selected_team and eff_team.id != selected_team.id:
            continue
        age = _player_age_years(p.birth_date, age_ref)
        if age is None or age > 22:
            continue
        if not _prospect_pos_matches(p.position, pos):
            continue
        young.append(p)

    items: list[dict] = []
    for pl in young:
        rr = get_player_ratings_row(pl.fhm_player_id)
        attrs: dict[str, float | None] = {}
        attrs_display: dict[str, object | None] = {}
        if rr:
            for _full, _abbr, key in overview_headers:
                raw_cell = rr.get(key)
                attrs_display[key] = raw_cell
                attrs[key] = _prospect_float(raw_cell)
        items.append(
            {
                "pl": pl,
                "attrs": attrs,
                "attrs_display": attrs_display,
                "age": _player_age_years(pl.birth_date, age_ref),
            }
        )

    rev = order == "desc"
    if sort_col == "player":

        def str_key(it: dict) -> tuple:
            pl = it["pl"]
            return ((pl.full_name or "").lower(), pl.id)

        items.sort(key=str_key, reverse=rev)
    else:

        def num_key(it: dict) -> tuple:
            pl = it["pl"]
            if sort_col == "abi":
                raw = pl.overall_ability
            elif sort_col == "pot":
                raw = pl.overall_potential
            else:
                raw = it["attrs"].get(sort_col)
            v = _prospect_float(raw) if raw is not None else None
            if v is None:
                sentinel = float("-inf") if rev else float("inf")
                return (sentinel, pl.full_name or "", pl.id)
            return (v, pl.full_name or "", pl.id)

        items.sort(key=num_key, reverse=rev)

    rows_out: list[dict] = []
    for i, it in enumerate(items, start=1):
        pl = it["pl"]
        rows_out.append(
            {
                "rank": i,
                "player": pl,
                "team": _effective_team(pl),
                "age": it["age"],
                "attrs": it["attrs_display"],
            }
        )

    total_prospects = len(rows_out)
    if prospect_expanded or total_prospects <= prospect_page_limit:
        display_rows = rows_out
    else:
        display_rows = rows_out[:prospect_page_limit]

    teams = session.scalars(select(Team).where(Team.fhm_league_id.in_(league_ids)).order_by(Team.name)).all()
    return render_template(
        "prospects.html",
        prospect_rows=display_rows,
        total_prospects=total_prospects,
        prospect_page_limit=prospect_page_limit,
        prospect_expanded=prospect_expanded,
        prospect_overview_headers=overview_headers,
        teams=teams,
        team_slug=team_slug,
        position=pos,
        prospect_sort=sort_col,
        prospect_order=order,
        prospect_sort_desc_defaults=sort_default_desc,
    )


_UNDRAFTED_AGE_OPTIONS: tuple[int, ...] = (20, 19, 18, 17, 16, 15)


@main_bp.get("/undrafted-prospects")
def undrafted_prospects():
    """Players with no NHL/BOWL draft pick, age ≤20, optional exact age and position filters."""
    pos = request.args.get("position")
    age_param = (request.args.get("age") or "").strip()
    ud_expanded = request.args.get("expanded") == "1"
    page_limit = 50
    session = db.session
    age_ref = season_age_reference_date(get_current_season())

    overview_headers = (
        ("Skating", "SKT", "skating"),
        ("Shooting", "SHT", "shooting"),
        ("Playmaking", "PLM", "playmaking"),
        ("Defending", "DEF", "defending"),
        ("Physicality", "PHY", "physicality"),
        ("Conditioning", "CON", "conditioning"),
        ("Character", "CHR", "character"),
        ("Hockey sense", "HSN", "hockey_sense"),
    )
    attr_sort_keys = frozenset(h[2] for h in overview_headers)
    valid_sorts = frozenset({"rank", "player", "abi", "pot", *attr_sort_keys})
    sort_default_desc = frozenset({"rank", "abi", "pot", *attr_sort_keys})

    sort_col = request.args.get("sort") or "pot"
    order = request.args.get("order") or "desc"
    if sort_col not in valid_sorts:
        sort_col = "pot"
    if order not in ("asc", "desc"):
        order = "desc"

    age_exact: int | None = None
    if age_param.isdigit():
        ai = int(age_param)
        if ai in _UNDRAFTED_AGE_OPTIONS:
            age_exact = ai

    drafted_subq = (
        select(DraftPick.player_id)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .where(DraftPick.player_id.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
    )

    q = select(Player).where(
        Player.retired.is_(False),
        Player.birth_date.isnot(None),
        Player.id.not_in(drafted_subq),
    )
    players = session.scalars(q).unique().all()

    pool: list[Player] = []
    for p in players:
        age = _player_age_years(p.birth_date, age_ref)
        if age is None or age > 20:
            continue
        if age_exact is not None and age != age_exact:
            continue
        if not _prospect_pos_matches(p.position, pos):
            continue
        pool.append(p)

    items: list[dict] = []
    for pl in pool:
        rr = get_player_ratings_row(pl.fhm_player_id)
        attrs: dict[str, float | None] = {}
        attrs_display: dict[str, object | None] = {}
        if rr:
            for _full, _abbr, key in overview_headers:
                raw_cell = rr.get(key)
                attrs_display[key] = raw_cell
                attrs[key] = _prospect_float(raw_cell)
        items.append(
            {
                "pl": pl,
                "attrs": attrs,
                "attrs_display": attrs_display,
                "age": _player_age_years(pl.birth_date, age_ref),
            }
        )

    rev = order == "desc"
    if sort_col == "rank":

        def rank_key(it: dict) -> tuple:
            pl = it["pl"]
            pot = _prospect_float(pl.overall_potential)
            abi = _prospect_float(pl.overall_ability)
            pot_v = pot if pot is not None else float("-inf")
            abi_v = abi if abi is not None else float("-inf")
            return (pot_v, abi_v, pl.full_name or "", pl.id)

        items.sort(key=rank_key, reverse=rev)
    elif sort_col == "player":

        def str_key(it: dict) -> tuple:
            pl = it["pl"]
            return ((pl.full_name or "").lower(), pl.id)

        items.sort(key=str_key, reverse=rev)
    else:

        def num_key(it: dict) -> tuple:
            pl = it["pl"]
            if sort_col == "abi":
                raw = pl.overall_ability
            elif sort_col == "pot":
                raw = pl.overall_potential
            else:
                raw = it["attrs"].get(sort_col)
            v = _prospect_float(raw) if raw is not None else None
            if v is None:
                sentinel = float("-inf") if rev else float("inf")
                return (sentinel, pl.full_name or "", pl.id)
            return (v, pl.full_name or "", pl.id)

        items.sort(key=num_key, reverse=rev)

    rows_out: list[dict] = []
    for i, it in enumerate(items, start=1):
        pl = it["pl"]
        rows_out.append(
            {
                "rank": i,
                "player": pl,
                "age": it["age"],
                "attrs": it["attrs_display"],
            }
        )

    total_n = len(rows_out)
    if ud_expanded or total_n <= page_limit:
        display_rows = rows_out
    else:
        display_rows = rows_out[:page_limit]

    age_query_value = str(age_exact) if age_exact is not None else ""

    return render_template(
        "undrafted_prospects.html",
        prospect_rows=display_rows,
        total_prospects=total_n,
        prospect_page_limit=page_limit,
        prospect_expanded=ud_expanded,
        prospect_overview_headers=overview_headers,
        position=pos,
        age_filter=age_query_value,
        prospect_sort=sort_col,
        prospect_order=order,
        prospect_sort_desc_defaults=sort_default_desc,
        undrafted_age_options=_UNDRAFTED_AGE_OPTIONS,
    )


@main_bp.get("/draft")
def draft():
    years = fetch_nhl_bowl_draft_years(db.session)
    year = request.args.get("year", type=int)
    if year is None and years:
        year = years[0]
    elif year is not None and year not in years:
        year = years[0] if years else None

    picks: list[DraftPick] = []
    picks_by_round: list[tuple[int | None, list[DraftPick]]] = []
    skater_career: dict[int, tuple[int, int, int, int]] = {}
    goalie_career: dict[int, tuple[int, int, int, int, float | None, int]] = {}

    if year is not None:
        picks = fetch_nhl_bowl_picks_for_year(db.session, year)
        picks_by_round = group_picks_by_round(picks)
        pids = [pk.player_id for pk in picks if pk.player_id]
        skater_career, goalie_career = build_career_stat_maps(db.session, pids)

    return render_template(
        "draft.html",
        draft_years=years,
        draft_year=year,
        picks=picks,
        picks_by_round=picks_by_round,
        skater_career=skater_career,
        goalie_career=goalie_career,
        draft_row_stat_mode=draft_row_stat_mode,
    )


def _read_semicolon_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f, delimiter=";"))
        except UnicodeDecodeError:
            continue
    return []


def _line_name(lines_row: dict[str, str], key: str, players_by_fhm: dict[str, Player]) -> str | None:
    raw = (lines_row.get(key) or "").strip()
    if not raw:
        return None
    pl = players_by_fhm.get(raw)
    return pl.full_name if pl else None


def _line_player(lines_row: dict[str, str], key: str, players_by_fhm: dict[str, Player]) -> Player | None:
    raw = (lines_row.get(key) or "").strip()
    if not raw:
        return None
    return players_by_fhm.get(raw)


def _unique_nonempty(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not v or v in seen:
            continue
        out.append(v)
        seen.add(v)
    return out


def _norm_contract_key(key: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in key).strip("_")


def _contract_rows_by_playerid(raw_import_dir: Path) -> dict[str, dict[str, str]]:
    path = raw_import_dir / "player_contract.csv"
    out: dict[str, dict[str, str]] = {}
    for row in _read_semicolon_rows(path):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
        pid = (nr.get("playerid") or "").strip()
        if pid:
            out[pid] = nr
    return out


def _contract_year_val(row: dict[str, str] | None, prefix: str, year: int) -> int | None:
    if not row:
        return None
    raw = (row.get(f"{prefix}_{year}") or "").strip()
    if raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _build_team_lines_views(
    team: Team,
    roster: list[Player],
    season: Season | None,
    raw_import_dir: Path,
) -> tuple[
    dict[str, list[dict[str, object]]],
    list[tuple[str, list[tuple[str, str | list[str] | None]]]],
    dict[str, int],
    list[dict[str, object]],
    int,
]:
    players_by_fhm: dict[str, Player] = {
        str(p.fhm_player_id): p
        for p in roster
        if p.fhm_player_id is not None and str(p.fhm_player_id).strip() != ""
    }
    lines_path = raw_import_dir / "team_lines.csv"
    lines_row: dict[str, str] = {}
    team_fhm = str(team.fhm_team_id) if team.fhm_team_id is not None else None
    if team_fhm:
        for row in _read_semicolon_rows(lines_path):
            if (row.get("TeamId") or row.get("teamid") or "").strip() == team_fhm:
                lines_row = row
                break
    main_line_player_ids: set[str] = {
        str(v).strip()
        for v in lines_row.values()
        if v is not None and str(v).strip().isdigit()
    }
    line_pids = sorted(
        {
            str(v).strip()
            for v in lines_row.values()
            if v is not None and str(v).strip().isdigit()
        }
    )
    if line_pids:
        extra_players = db.session.scalars(select(Player).where(Player.fhm_player_id.in_(line_pids))).all()
        for p in extra_players:
            if p.fhm_player_id is not None:
                players_by_fhm[str(p.fhm_player_id)] = p
    org_players_by_id: dict[int, Player] = {p.id: p for p in roster}
    # Include organization-owned players not on the active roster (e.g., minors/reserves)
    if team.fhm_team_id is not None:
        contracted_org_players = db.session.scalars(
            select(Player)
            .join(PlayerContract, PlayerContract.player_id == Player.id)
            .where(PlayerContract.fhm_team_id == team.fhm_team_id)
        ).all()
        for p in contracted_org_players:
            org_players_by_id[p.id] = p
    # Include prospects assigned to the team, if linked to player records
    prospect_org_players = db.session.scalars(
        select(Player).join(Prospect, Prospect.player_id == Player.id).where(Prospect.team_id == team.id)
    ).all()
    for p in prospect_org_players:
        org_players_by_id[p.id] = p

    allowed_org_ids = set(org_players_by_id.keys())

    def lp(key: str) -> Player | None:
        pl = _line_player(lines_row, key, players_by_fhm)
        if not pl or pl.id not in allowed_org_ids:
            return None
        return pl

    def ln(key: str) -> str | None:
        pl = lp(key)
        return pl.full_name if pl else None

    lines_name_to_id = {p.full_name: p.id for p in players_by_fhm.values() if p.id in allowed_org_ids}
    lw_lines = _unique_nonempty([ln("ES L1 LW"), ln("ES L2 LW"), ln("ES L3 LW"), ln("ES L4 LW")])
    c_lines = _unique_nonempty([ln("ES L1 C"), ln("ES L2 C"), ln("ES L3 C"), ln("ES L4 C")])
    rw_lines = _unique_nonempty([ln("ES L1 RW"), ln("ES L2 RW"), ln("ES L3 RW"), ln("ES L4 RW")])
    ld_lines = _unique_nonempty([ln("ES L1 LD"), ln("ES L2 LD"), ln("ES L3 LD"), ln("ES L4 LD")])
    rd_lines = _unique_nonempty([ln("ES L1 RD"), ln("ES L2 RD"), ln("ES L3 RD"), ln("ES L4 RD")])
    g_lines = _unique_nonempty([ln("Goalie 1"), ln("Goalie 2")])

    def _rating_num(pl: Player, key: str) -> float:
        rr = get_player_ratings_row(pl.fhm_player_id)
        if not rr:
            return -1.0
        raw = rr.get(key)
        if raw is None:
            return -1.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return -1.0

    def _bucket_for_player(pl: Player) -> str | None:
        pos = (pl.position or "").upper()
        if pos in ("LW", "C", "RW", "D", "G"):
            return pos
        scores = {
            "G": _rating_num(pl, "g"),
            "D": max(_rating_num(pl, "ld"), _rating_num(pl, "rd")),
            "LW": _rating_num(pl, "lw"),
            "C": _rating_num(pl, "c"),
            "RW": _rating_num(pl, "rw"),
        }
        best = max(scores.items(), key=lambda kv: kv[1])
        return best[0] if best[1] >= 0 else None

    by_pos: dict[str, list[Player]] = {"LW": [], "C": [], "RW": [], "D": [], "G": []}
    for p in org_players_by_id.values():
        b = _bucket_for_player(p)
        if b in by_pos:
            by_pos[b].append(p)

    def _depth_entry(pl: Player) -> dict[str, object]:
        fhm_pid = str(pl.fhm_player_id or "").strip()
        # Prefer explicit lineup/depth slots from team_lines.csv for "main club".
        # Fallback to current_team_id when line data is unavailable.
        is_main = (fhm_pid in main_line_player_ids) if main_line_player_ids else (pl.current_team_id == team.id)
        return {
            "name": pl.full_name,
            "is_main_roster": is_main,
            "abi": round(float(pl.overall_ability), 1) if pl.overall_ability is not None else None,
            "pot": round(float(pl.overall_potential), 1) if pl.overall_potential is not None else None,
            "pid": pl.id,
        }

    def _depth_score(pl: Player, bucket: str) -> tuple[float, float, float, str]:
        if bucket == "G":
            primary = _rating_num(pl, "g")
        elif bucket == "D":
            primary = max(_rating_num(pl, "ld"), _rating_num(pl, "rd"))
        elif bucket == "LW":
            primary = _rating_num(pl, "lw")
        elif bucket == "C":
            primary = _rating_num(pl, "c")
        else:
            primary = _rating_num(pl, "rw")
        abi = float(pl.overall_ability) if pl.overall_ability is not None else -1.0
        pot = float(pl.overall_potential) if pl.overall_potential is not None else -1.0
        return (primary, abi, pot, pl.full_name.lower())

    def _merge_depth(players: list[Player], bucket: str) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        seen: set[int] = set()
        ordered = sorted(players, key=lambda p: _depth_score(p, bucket), reverse=True)
        for pl in ordered:
            if not pl or pl.id in seen:
                continue
            seen.add(pl.id)
            out.append(_depth_entry(pl))
        return out

    depth_chart = {
        "goalies": _merge_depth(by_pos["G"], "G"),
        "defensemen": _merge_depth(by_pos["D"], "D"),
        "left_wings": _merge_depth(by_pos["LW"], "LW"),
        "centers": _merge_depth(by_pos["C"], "C"),
        "right_wings": _merge_depth(by_pos["RW"], "RW"),
    }

    lines_sections: list[tuple[str, list[tuple[str, str | list[str] | None]]]] = [
        ("Forwards", [("LW", lw_lines), ("C", c_lines), ("RW", rw_lines)]),
        ("Defense", [("LD", ld_lines), ("RD", rd_lines)]),
        ("1st Powerplay Unit (5v4)", [("LW", ln("PP5on4 L1 LW")), ("C", ln("PP5on4 L1 C")), ("RW", ln("PP5on4 L1 RW")), ("LD", ln("PP5on4 L1 LD")), ("RD", ln("PP5on4 L1 RD"))]),
        ("2nd Powerplay Unit (5v4)", [("LW", ln("PP5on4 L2 LW")), ("C", ln("PP5on4 L2 C")), ("RW", ln("PP5on4 L2 RW")), ("LD", ln("PP5on4 L2 LD")), ("RD", ln("PP5on4 L2 RD"))]),
        ("Penalty Kill Unit 1 (4v5)", [("F1", ln("PK4on5 L1 F1")), ("F2", ln("PK4on5 L1 F2")), ("LD", ln("PK4on5 L1 LD")), ("RD", ln("PK4on5 L1 RD"))]),
        ("Penalty Kill Unit 2 (4v5)", [("F1", ln("PK4on5 L2 F1")), ("F2", ln("PK4on5 L2 F2")), ("LD", ln("PK4on5 L2 LD")), ("RD", ln("PK4on5 L2 RD"))]),
        ("Penalty Kill Unit 3 (4v5)", [("F1", ln("PK4on5 L3 F1")), ("F2", ln("PK4on5 L3 F2")), ("LD", ln("PK4on5 L3 LD")), ("RD", ln("PK4on5 L3 RD"))]),
        ("Goalies", [("Starter", ln("Goalie 1")), ("Backup", ln("Goalie 2"))]),
    ]

    age_ref = season_age_reference_date(season)
    salary_rows: list[dict[str, object]] = []
    salary_total = 0
    salary_years = [int(season.start_year) + i for i in range(6)] if season and season.start_year else []
    contract_rows = _contract_rows_by_playerid(raw_import_dir)
    contracts_q = select(PlayerContract).join(Player, Player.id == PlayerContract.player_id)
    if team.fhm_team_id is not None:
        contracts_q = contracts_q.where(PlayerContract.fhm_team_id == team.fhm_team_id)
    else:
        contracts_q = contracts_q.where(Player.current_team_id == team.id)
    contracts = db.session.scalars(contracts_q).all()
    season_start_year = season.start_year if season else None
    total_year0 = 0
    for c in contracts:
        p = c.player
        if not p:
            continue
        crow = contract_rows.get(str(p.fhm_player_id or "").strip())
        y0 = salary_years[0] if salary_years else None
        if y0 is not None:
            mv = _contract_year_val(crow, "major", y0)
            nv = _contract_year_val(crow, "minor", y0)
            base = mv if mv is not None and mv >= 0 else (nv if nv is not None and nv >= 0 else 0)
            total_year0 += int(base)
    salary_total = total_year0

    for c in contracts:
        p = c.player
        if not p:
            continue
        crow = contract_rows.get(str(p.fhm_player_id or "").strip())
        is_minor = p.current_team_id != team.id
        year_cells: list[dict[str, object]] = []
        last_active_idx: int | None = None
        for idx, yr in enumerate(salary_years):
            major_v = _contract_year_val(crow, "major", yr)
            minor_v = _contract_year_val(crow, "minor", yr)
            val = None
            if is_minor:
                val = minor_v if minor_v is not None and minor_v >= 0 else major_v
            else:
                val = major_v if major_v is not None and major_v >= 0 else minor_v
            if val is not None and val >= 0:
                pct = (100.0 * float(val) / float(total_year0)) if total_year0 > 0 and idx == 0 else None
                year_cells.append({"kind": "salary", "value": int(val), "pct": pct})
                last_active_idx = idx
            else:
                year_cells.append({"kind": "empty"})
        if last_active_idx is not None and last_active_idx + 1 < len(year_cells):
            year_cells[last_active_idx + 1] = {"kind": "tag", "value": "UFA" if c.is_ufa else "RFA"}

        pos = (p.position or "").upper()
        if is_minor:
            salary_group = "Minors"
        elif pos in ("LW", "C", "RW"):
            salary_group = "Forwards"
        elif pos in ("D", "LD", "RD"):
            salary_group = "Defensemen"
        elif pos.startswith("G"):
            salary_group = "Goalies"
        else:
            salary_group = "Forwards"
        name_badges: list[str] = []
        if c.has_nmc:
            name_badges.append("NMC")
        if c.has_ntc:
            name_badges.append("NTC")
        salary_rows.append(
            {
                "player": p,
                "pos": p.position or "—",
                "age": _player_age_years(p.birth_date, age_ref),
                "salary": int(c.average_salary or 0),
                "group": salary_group,
                "name_badges": name_badges,
                "year_cells": year_cells,
                "years_left": contract_years_remaining_major(
                    p.fhm_player_id, season_start_year, raw_import_dir
                ),
            }
        )
    group_order = {"Forwards": 0, "Defensemen": 1, "Goalies": 2, "Minors": 3}
    salary_rows.sort(key=lambda r: (group_order.get(str(r["group"]), 9), -int(r["salary"]), str(r["player"].full_name)))
    return depth_chart, lines_sections, lines_name_to_id, salary_rows, salary_total


@main_bp.get("/team/<slug>")
def team_page(slug: str):
    team = db.session.scalars(select(Team).where(Team.slug == slug).limit(1)).first()
    if not team:
        abort(404)
    season = get_current_season()
    division_name = None
    division_rank = None
    arena_name = None
    arena_capacity = None
    arena_row = db.session.execute(
        select(
            Game.arena.label("arena"),
            func.count(Game.id).label("games"),
            func.max(Game.attendance).label("max_attendance"),
        )
        .where(
            Game.home_team_id == team.id,
            Game.arena.isnot(None),
            Game.arena != "",
        )
        .group_by(Game.arena)
        .order_by(func.count(Game.id).desc(), Game.arena.asc())
        .limit(1)
    ).first()
    if arena_row:
        arena_name = arena_row.arena
        arena_capacity = int(arena_row.max_attendance) if arena_row.max_attendance is not None else None
    standing = None
    if season:
        standing = db.session.scalars(
            select(TeamStanding).where(
                TeamStanding.team_id == team.id, TeamStanding.season_id == season.id
            ).limit(1)
        ).first()
        if standing:
            # Keep team-page division labels aligned with standings page mapping.
            conf_name_by_id: dict[int, str] = {}
            div_name_by_pair: dict[tuple[int, int], str] = {}
            div_name_by_id: dict[int, str] = {}
            raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))

            conf_csv = raw_dir / "conferences.csv"
            if conf_csv.is_file():
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
                            conf_name_by_id[cid] = nm.removesuffix(" Conference").strip() or nm
                except Exception:
                    conf_name_by_id = {}

            div_csv = raw_dir / "divisions.csv"
            if div_csv.is_file():
                try:
                    with div_csv.open("r", encoding="utf-8-sig", newline="") as f:
                        sample = f.read(2048)
                        f.seek(0)
                        delim = ";" if sample.count(";") >= sample.count(",") else ","
                        reader = csv.DictReader(f, delimiter=delim)
                        for row in reader:
                            lid = (row.get("League Id") or row.get("league_id") or "").strip()
                            if lid and lid != "0":
                                continue
                            did = (row.get("Division Id") or row.get("division_id") or "").strip()
                            cid = (row.get("Conference Id") or row.get("conference_id") or "").strip()
                            nm = (row.get("Name") or row.get("name") or "").strip()
                            if not did or not nm:
                                continue
                            try:
                                div_id = int(did)
                            except ValueError:
                                continue
                            try:
                                conf_id = int(cid) if cid else -9999
                            except ValueError:
                                conf_id = -9999
                            if conf_id != -9999:
                                div_name_by_pair[(conf_id, div_id)] = nm
                            if div_id not in div_name_by_id:
                                div_name_by_id[div_id] = nm
                except Exception:
                    div_name_by_pair = {}
                    div_name_by_id = {}

            def _display_division(st_row: TeamStanding) -> str | None:
                if st_row.team is not None and st_row.team.fhm_division_id is not None:
                    did = int(st_row.team.fhm_division_id)
                    cid = (
                        int(st_row.team.fhm_conference_id)
                        if st_row.team.fhm_conference_id is not None
                        else None
                    )
                    if cid is not None and (cid, did) in div_name_by_pair:
                        return div_name_by_pair[(cid, did)]
                    if did in div_name_by_id:
                        return div_name_by_id[did]
                return st_row.division or st_row.conference

            division_name = _display_division(standing)
            if division_name:
                div_rows = [
                    r
                    for r in standings_for_season(season)
                    if _display_division(r) == division_name
                ]
                for idx, r in enumerate(div_rows, start=1):
                    if r.team_id == team.id:
                        division_rank = idx
                        break
    panel = (request.args.get("panel", "roster") or "roster").strip().lower()
    if panel not in {"roster", "depth", "lines", "salary"}:
        panel = "roster"
    salary_years = [int(season.start_year) + i for i in range(6)] if season and season.start_year else []
    roster = db.session.scalars(
        select(Player).where(Player.current_team_id == team.id).order_by(Player.last_name, Player.first_name)
    ).all()
    age_ref = season_age_reference_date(season)
    roster_ages = {p.id: _player_age_years(p.birth_date, age_ref) for p in roster}
    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    depth_chart, lines_sections, lines_name_to_id, salary_rows, salary_total = _build_team_lines_views(
        team, roster, season, raw_dir
    )
    sk_leaders = []
    team_agg = None
    team_agg_po = None
    team_rank_rs: dict[str, int] = {}
    team_rank_po: dict[str, int] = {}

    def _rank_maps_for_segment(segment: str) -> dict[int, dict[str, int]]:
        if not season:
            return {}
        aggs = db.session.scalars(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == season.id,
                TeamSeasonAggregate.stat_segment == segment,
            )
        ).all()
        if not aggs:
            return {}

        specs = {
            "shots_for": ("shots_for", True),
            "shots_against": ("shots_against", False),
            "faceoff_pct": ("faceoff_pct", True),
            "blocked_shots": ("blocked_shots", True),
            "hits": ("hits", True),
            "takeaways": ("takeaways", True),
            "giveaways": ("giveaways", False),
            "pp_chances": ("pp_chances", True),
            "pp_goals": ("pp_goals", True),
            "pp_pct": ("pp_pct", True),
            "pk_goals_against": ("pk_goals_against", False),
            "sh_chances": ("sh_chances", False),
            "pk_pct": ("pk_pct", False),
            "sh_goals": ("sh_goals", True),
            "pim_per_game": ("pim_per_game", False),
            "attendance_home": ("attendance_home", True),
            "attendance_away": ("attendance_away", True),
            "sellouts_home": ("sellouts_home", True),
            "sellouts_away": ("sellouts_away", True),
        }

        by_team: dict[int, dict[str, int]] = {}
        for key, (attr, high_good) in specs.items():
            vals: list[tuple[int, float]] = []
            for a in aggs:
                if attr == "pp_pct":
                    if a.pp_chances and a.pp_chances > 0 and a.pp_goals is not None:
                        v = float(a.pp_goals) / float(a.pp_chances)
                    else:
                        v = None
                elif attr == "pk_pct":
                    if a.sh_chances and a.sh_chances > 0 and a.pk_goals_against is not None:
                        v = float(a.pk_goals_against) / float(a.sh_chances)
                    else:
                        v = None
                else:
                    raw = getattr(a, attr)
                    v = float(raw) if raw is not None else None
                if v is None or a.team_id is None:
                    continue
                vals.append((a.team_id, v))
            if not vals:
                continue
            vals.sort(key=lambda tv: tv[1], reverse=high_good)
            prev_val = None
            rank = 0
            for idx, (tid, v) in enumerate(vals, start=1):
                if prev_val is None or abs(v - prev_val) > 1e-12:
                    rank = idx
                    prev_val = v
                by_team.setdefault(tid, {})[key] = rank
        return by_team
    if season:
        ranks_rs = _rank_maps_for_segment("rs")
        ranks_po = _rank_maps_for_segment("po")
        sk_leaders = db.session.execute(
            select(PlayerSkaterStat, Player)
            .join(Player, PlayerSkaterStat.player_id == Player.id)
            .where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.team_id == team.id,
                PlayerSkaterStat.stat_segment == "rs",
            )
            .order_by(PlayerSkaterStat.points.desc())
            .limit(11)
        ).all()
        team_agg = db.session.scalars(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == season.id,
                TeamSeasonAggregate.team_id == team.id,
                TeamSeasonAggregate.stat_segment == "rs",
            ).limit(1)
        ).first()
        if team_agg and team.id in ranks_rs:
            team_rank_rs = ranks_rs[team.id]
        team_agg_po = db.session.scalars(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == season.id,
                TeamSeasonAggregate.team_id == team.id,
                TeamSeasonAggregate.stat_segment == "po",
            ).limit(1)
        ).first()
        if team_agg_po and team.id in ranks_po:
            team_rank_po = ranks_po[team.id]
    recent_games = []
    upcoming_games = []
    if season:
        recent_games = db.session.scalars(
            select(Game)
            .options(joinedload(Game.home_team), joinedload(Game.away_team))
            .where(
                Game.season_id == season.id,
                Game.status == "final",
                (Game.home_team_id == team.id) | (Game.away_team_id == team.id),
            )
            .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
            .limit(5)
        ).all()
        upcoming_games = db.session.scalars(
            select(Game)
            .options(joinedload(Game.home_team), joinedload(Game.away_team))
            .where(
                Game.season_id == season.id,
                Game.status != "final",
                (Game.home_team_id == team.id) | (Game.away_team_id == team.id),
            )
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            .limit(5)
        ).all()
    team_prospects = db.session.scalars(
        select(Prospect).options(joinedload(Prospect.player)).where(Prospect.team_id == team.id)
    ).all()
    return render_template(
        "team.html",
        team=team,
        arena_name=arena_name,
        arena_capacity=arena_capacity,
        division_name=division_name,
        division_rank=division_rank,
        season=season,
        standing=standing,
        roster=roster,
        roster_ages=roster_ages,
        sk_leaders=sk_leaders,
        recent_games=recent_games,
        upcoming_games=upcoming_games,
        prospects_list=team_prospects,
        team_agg=team_agg,
        team_agg_po=team_agg_po,
        team_rank_rs=team_rank_rs,
        team_rank_po=team_rank_po,
        active_panel=panel,
        depth_chart=depth_chart,
        lines_sections=lines_sections,
        lines_name_to_id=lines_name_to_id,
        salary_rows=salary_rows,
        salary_total=salary_total,
        salary_years=salary_years,
    )


def _player_age_years(birth: date | None, as_of: date | None = None) -> int | None:
    if birth is None:
        return None
    ref = as_of if as_of is not None else date.today()
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


@main_bp.get("/player/<int:player_id>")
def player_page(player_id: int):
    player = db.session.get(Player, player_id)
    if not player:
        abort(404)
    season = get_current_season()
    sk_career_lines = db.session.scalars(
        select(PlayerSkaterCareerLine)
        .options(joinedload(PlayerSkaterCareerLine.team))
        .where(PlayerSkaterCareerLine.player_id == player.id)
        .order_by(PlayerSkaterCareerLine.season_year.desc())
    ).all()
    # rs + retired_rs: active vs retired regular-season career CSVs (see import_career_skater_file)
    career_rs_sk = [ln for ln in sk_career_lines if ln.career_source in ("rs", "retired_rs")]
    career_po_sk = [ln for ln in sk_career_lines if ln.career_source in ("po", "retired_po")]

    gk_career_lines = db.session.scalars(
        select(PlayerGoalieCareerLine)
        .options(joinedload(PlayerGoalieCareerLine.team))
        .where(PlayerGoalieCareerLine.player_id == player.id)
        .order_by(PlayerGoalieCareerLine.season_year.desc())
    ).all()
    career_rs_gk = [ln for ln in gk_career_lines if ln.career_source in ("rs", "retired_rs")]
    career_po_gk = [ln for ln in gk_career_lines if ln.career_source in ("ps", "po", "retired_ps", "retired_po")]
    pos = (player.position or "").strip().upper()
    is_goalie = pos.startswith("G")
    if is_goalie:
        game_log = db.session.scalars(
            select(GameGoalieStat)
            .options(
                joinedload(GameGoalieStat.game).joinedload(Game.home_team),
                joinedload(GameGoalieStat.game).joinedload(Game.away_team),
            )
            .join(Game, GameGoalieStat.game_id == Game.id)
            .where(GameGoalieStat.player_id == player.id)
            .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
            .limit(40)
        ).all()
    else:
        game_log = db.session.scalars(
            select(GameSkaterStat)
            .options(
                joinedload(GameSkaterStat.game).joinedload(Game.home_team),
                joinedload(GameSkaterStat.game).joinedload(Game.away_team),
            )
            .join(Game, GameSkaterStat.game_id == Game.id)
            .where(GameSkaterStat.player_id == player.id)
            .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
            .limit(40)
        ).all()
    current_team = db.session.get(Team, player.current_team_id) if player.current_team_id else None
    contract = db.session.scalars(
        select(PlayerContract).where(PlayerContract.player_id == player.id).limit(1)
    ).first()
    draft_picks = db.session.scalars(
        select(DraftPick)
        .options(joinedload(DraftPick.draft), joinedload(DraftPick.team))
        .where(DraftPick.player_id == player.id)
        .order_by(DraftPick.draft_year.desc().nulls_last(), DraftPick.overall_pick)
    ).all()
    contract_team = None
    if contract and contract.fhm_team_id is not None:
        contract_team = db.session.scalars(
            select(Team).where(Team.fhm_team_id == str(contract.fhm_team_id)).limit(1)
        ).first()
    accent_team = contract_team or current_team
    ratings_row = get_player_ratings_row(player.fhm_player_id)
    player_age = _player_age_years(player.birth_date, season_age_reference_date(season))
    rating_avgs_skater = skater_category_averages(ratings_row)
    rating_avgs_goalie = goalie_category_averages(ratings_row)
    season_start_year = season.start_year if season else None
    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    contract_years_left = contract_years_remaining_major(
        player.fhm_player_id, season_start_year, raw_dir
    )
    roster_header_team = main_league_roster_team(contract_team, current_team)
    return render_template(
        "player.html",
        player=player,
        season=season,
        career_rs_sk=career_rs_sk,
        career_po_sk=career_po_sk,
        career_rs_gk=career_rs_gk,
        career_po_gk=career_po_gk,
        game_log=game_log,
        current_team=current_team,
        contract=contract,
        contract_team=contract_team,
        roster_header_team=roster_header_team,
        accent_team=accent_team,
        draft_picks=draft_picks,
        ratings_row=ratings_row,
        player_age=player_age,
        player_is_goalie=is_goalie,
        rating_avgs_skater=rating_avgs_skater,
        rating_avgs_goalie=rating_avgs_goalie,
        contract_years_left=contract_years_left,
    )


@main_bp.get("/game/<int:game_id>")
def game_page(game_id: int):
    game = db.session.scalars(
        select(Game)
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.id == game_id)
        .limit(1)
    ).first()
    if not game:
        abort(404)
    return render_template("game.html", game=game)


@main_bp.get("/search")
def search_page():
    q = request.args.get("q", "")
    return render_template("search.html", q=q)

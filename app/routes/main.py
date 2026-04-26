"""Server-rendered pages for the Boys of Winter League site."""
from __future__ import annotations

import csv
import re
import smtplib
import unicodedata
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, url_for
from sqlalchemy import case, cast, extract, Float, func, not_, nulls_last, or_, select
from sqlalchemy.orm import joinedload

from app.config import (
    BASE_DIR,
    Config,
    undrafted_prospects_age_filter_options,
    undrafted_prospects_max_age,
)
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
    Season,
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
from app.services.player_career_totals import goalie_career_lines_totals, skater_career_lines_totals
from app.services.player_contract_csv import (
    contract_final_season_label_from_remaining,
    contract_years_remaining_major,
)
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.franchise_leaders import build_franchise_history_sections
from app.services.free_agents import (
    FA_GOALIE_MAIN,
    FA_GOALIE_MENTAL,
    FA_ROLES,
    FA_SKATER_DEFENSE,
    FA_SKATER_MENTAL,
    FA_SKATER_OFFENSE,
    FA_SKATER_OVERVIEW,
    FA_SKATER_PHYSICAL,
    GOALIE_VIEWS,
    SKATER_VIEWS,
    bowl_org_rights_player_ids,
    fetch_free_agent_players,
)
from app.services.history_coach_awards import (
    attach_coach_award_displays,
    is_jim_gregory_award,
    is_staff_history_award,
)


# Trophies whose ``history_awards`` row stores the winner on ``team_id`` (not ``player_id``).
_TEAM_HISTORY_AWARD_TITLES: frozenset[str] = frozenset(
    (
        "BOILEDEGG'S TROPHY",
        "PRINCE OF WALES TROPHY",
        "CLARENCE CAMPBELL TROPHY",
        "BOWL CUP TROPHY",
    )
)


def is_team_history_award(award_name: str | None) -> bool:
    return _norm_award_title(award_name or "") in _TEAM_HISTORY_AWARD_TITLES
from app.services.player_history_award_badges import player_history_award_badges
from app.services.team_staff_csv import (
    STAFF_COACH_COLUMNS,
    STAFF_SCOUT_COLUMNS,
    STAFF_TRAINER_COLUMNS,
    get_staff_sections_for_team,
)
from app.services.division_labels import load_division_display_maps
from app.services.standings import (
    conferences_for_season,
    divisions_for_season,
    standings_for_season,
    team_aggregate_rows,
)
from app.services.postseason_odds import build_team_page_mc_bundle
main_bp = Blueprint("main", __name__)


def _require_join_field(form: dict[str, str], key: str, label: str, errors: list[str]) -> str:
    v = (form.get(key) or "").strip()
    if not v:
        errors.append(f"{label} is required.")
    return v


def _join_league_team_options() -> list[str]:
    """Admin-editable team options for the join form; always starts with ``Waitlist``.

    Source file (optional): ``<instance>/join_league_available_teams.txt`` one team per line.
    Lines starting with ``#`` are ignored. If the file is missing or has no team lines, only
    ``Waitlist`` is offered until you add teams to the file.
    """
    options: list[str] = []
    teams_file = Path(current_app.instance_path) / "join_league_available_teams.txt"
    if teams_file.is_file():
        try:
            for raw in teams_file.read_text(encoding="utf-8").splitlines():
                v = raw.strip()
                if not v or v.startswith("#"):
                    continue
                if v.lower() == "waitlist":
                    continue
                options.append(v)
        except OSError:
            options = []

    # De-duplicate while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for t in options:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return ["Waitlist", *uniq]


def _send_join_league_email(payload: dict[str, str], heard_from: list[str]) -> None:
    recipient = str(current_app.config.get("JOIN_LEAGUE_RECIPIENT", "keenovdecimanus@gmail.com")).strip()
    smtp_host = str(current_app.config.get("MAIL_SMTP_HOST", "")).strip()
    smtp_port = int(current_app.config.get("MAIL_SMTP_PORT", 587))
    smtp_user = str(current_app.config.get("MAIL_SMTP_USERNAME", "")).strip()
    smtp_pass = str(current_app.config.get("MAIL_SMTP_PASSWORD", "")).strip()
    smtp_from = str(current_app.config.get("MAIL_FROM", smtp_user or recipient)).strip()
    use_tls = bool(current_app.config.get("MAIL_SMTP_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_SMTP_USE_SSL", False))

    if not smtp_host:
        raise RuntimeError("MAIL_SMTP_HOST is not configured.")

    msg = EmailMessage()
    msg["Subject"] = f"[{current_app.config.get('LEAGUE_DISPLAY_NAME', 'League')}] Join League Application - {payload['first_name']} {payload['last_name']}"
    msg["From"] = smtp_from
    msg["To"] = recipient
    body_lines = [
        f"League: {current_app.config.get('LEAGUE_DISPLAY_NAME', '')}",
        f"First Name: {payload['first_name']}",
        f"Last Name: {payload['last_name']}",
        f"Email: {payload['email']}",
        f"Age: {payload['age']}",
        f"Location: {payload['location']}",
        f"Discord: {payload['discord_status']}",
        f"Available Team: {payload['available_team']}",
        f"Heard About League From: {', '.join(heard_from)}",
        f"Other Leagues Active In: {payload['other_leagues_count']}",
        f"Favorite NHL Team: {payload['favorite_nhl_team']}",
        f"Favorite Player: {payload['favorite_player']}",
        "",
        "Experience Description:",
        payload["experience"],
        "",
        "Hockey Knowledge Description:",
        payload["knowledge"],
        "",
        "Team Building Style:",
        payload["team_building_style"],
    ]
    msg.set_content("\n".join(body_lines))

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)


# Banner / Banner1.png / banner 1.png — case-insensitive; optional space before digits; png/webp/jpeg
_BANNER_FILE_RE = re.compile(r"^banner\s*(\d+)\.(png|webp|jpe?g)$", re.IGNORECASE)
_BANNER_EXT_PRIORITY = {".png": 0, ".webp": 1, ".jpeg": 2, ".jpg": 2}


def champion_banner_urls() -> list[str]:
    """Championship banner images under the league champions folder (and legacy root when merged).

    Sorted by banner index *N*. Gaps are allowed. Extensions ``.png`` / ``.webp`` / ``.jpg`` / ``.jpeg``
    are case-insensitive. If the same *N* exists in both the league folder and ``img/history/champions``,
    the league copy wins. Filenames are NFC-normalized; URLs use each file's real on-disk name.
    """
    rel = str(current_app.config.get("HISTORY_CHAMPIONS_REL_DIR", "img/history/champions")).strip("/\\")
    primary_dir = (BASE_DIR / "app" / "static" / Path(rel)).resolve()
    legacy_rel = "img/history/champions"
    legacy_dir = (BASE_DIR / "app" / "static" / legacy_rel).resolve()

    def _scan(folder: Path) -> dict[int, str]:
        """Map banner index -> filename (one file per index; prefers png over webp over jpeg)."""
        if not folder.is_dir():
            return {}
        candidates: list[tuple[int, Path]] = []
        for p in folder.iterdir():
            if not p.is_file():
                continue
            safe_name = unicodedata.normalize("NFC", p.name)
            m = _BANNER_FILE_RE.match(safe_name)
            if m:
                candidates.append((int(m.group(1)), p))
        by_n: dict[int, tuple[int, str]] = {}
        for n, p in candidates:
            ext = p.suffix.lower()
            prio = _BANNER_EXT_PRIORITY.get(ext, 9)
            prev = by_n.get(n)
            if prev is None or prio < prev[0]:
                by_n[n] = (prio, p.name)
        return {n: name for n, (_, name) in by_n.items()}

    merged: dict[int, tuple[str, str]] = {}
    if primary_dir != legacy_dir:
        for n, name in _scan(legacy_dir).items():
            merged[n] = (legacy_rel, name)
    for n, name in _scan(primary_dir).items():
        merged[n] = (rel, name)

    ordered = sorted(merged.items(), key=lambda kv: kv[0])
    return [url_for("static", filename=f"{out_rel}/{name}") for _, (out_rel, name) in ordered]


@main_bp.get("/")
def home():
    from app.services.milestones import build_milestone_sections

    skater_sections, goalie_sections = build_milestone_sections(db.session, split="rs")
    raw_teasers: list[dict[str, object]] = []
    for section in skater_sections:
        for row in section.rows:
            raw_teasers.append(
                {
                    "player": row.player,
                    "group": "Skater",
                    "stat_title": section.title,
                    "current_value": row.current_value,
                    "next_milestone": row.next_milestone,
                    "remaining": row.remaining,
                }
            )
    for section in goalie_sections:
        for row in section.rows:
            raw_teasers.append(
                {
                    "player": row.player,
                    "group": "Goalie",
                    "stat_title": section.title,
                    "current_value": row.current_value,
                    "next_milestone": row.next_milestone,
                    "remaining": row.remaining,
                }
            )

    # Keep the closest item per player to avoid repeated names in the teaser card.
    best_by_player: dict[int, dict[str, object]] = {}
    for item in raw_teasers:
        player = item["player"]
        if not isinstance(player, Player):
            continue
        prior = best_by_player.get(player.id)
        if prior is None or int(item["remaining"]) < int(prior["remaining"]):
            best_by_player[player.id] = item
    milestone_teasers = sorted(
        best_by_player.values(),
        key=lambda x: (int(x["remaining"]), str(getattr(x["player"], "full_name", "")).lower()),
    )[:5]
    return render_template("home.html", milestone_teasers=milestone_teasers)


@main_bp.route("/join-league", methods=["GET", "POST"])
def join_league():
    available_teams = _join_league_team_options()
    if request.method == "GET":
        return render_template(
            "join_league.html",
            errors=[],
            submitted=False,
            form_data={"available_team": "Waitlist"},
            available_teams=available_teams,
        )

    form_data = {k: (request.form.get(k) or "").strip() for k in request.form.keys()}
    errors: list[str] = []
    first_name = _require_join_field(form_data, "first_name", "First name", errors)
    last_name = _require_join_field(form_data, "last_name", "Last name", errors)
    email = _require_join_field(form_data, "email", "E-mail", errors)
    age = _require_join_field(form_data, "age", "Age", errors)
    location = _require_join_field(form_data, "location", "Location", errors)
    discord_status = _require_join_field(form_data, "discord_status", "Discord response", errors)
    available_team = _require_join_field(form_data, "available_team", "Available team", errors)
    other_leagues_count = _require_join_field(form_data, "other_leagues_count", "Other leagues count", errors)
    favorite_nhl_team = _require_join_field(form_data, "favorite_nhl_team", "Favorite NHL team", errors)
    favorite_player = _require_join_field(form_data, "favorite_player", "Favorite player", errors)
    experience = _require_join_field(form_data, "experience", "Experience description", errors)
    knowledge = _require_join_field(form_data, "knowledge", "Hockey knowledge description", errors)
    team_building_style = _require_join_field(form_data, "team_building_style", "Team building style", errors)

    heard_from = [x.strip() for x in request.form.getlist("heard_from") if x.strip()]
    if not heard_from:
        errors.append("Select at least one option for how you heard about the league.")

    if email and "@" not in email:
        errors.append("E-mail must be valid.")
    if available_team and available_team not in available_teams:
        errors.append("Available team selection is invalid.")
    if (request.form.get("acknowledge") or "") != "yes":
        errors.append("You must acknowledge the participation requirements.")
    if (request.form.get("security_answer") or "").strip() != "48":
        errors.append("Security answer is incorrect.")

    if errors:
        return render_template(
            "join_league.html",
            errors=errors,
            submitted=False,
            form_data=form_data,
            available_teams=available_teams,
        )

    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "age": age,
        "location": location,
        "discord_status": discord_status,
        "available_team": available_team,
        "other_leagues_count": other_leagues_count,
        "favorite_nhl_team": favorite_nhl_team,
        "favorite_player": favorite_player,
        "experience": experience,
        "knowledge": knowledge,
        "team_building_style": team_building_style,
    }
    try:
        _send_join_league_email(payload, heard_from)
    except Exception as exc:
        return render_template(
            "join_league.html",
            errors=[f"Could not send application email: {exc}"],
            submitted=False,
            form_data=form_data,
            available_teams=available_teams,
        )
    return render_template(
        "join_league.html",
        errors=[],
        submitted=True,
        form_data={"available_team": "Waitlist"},
        available_teams=available_teams,
    )


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
    div_csv = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "divisions.csv"
    div_name_by_pair, div_name_by_id = load_division_display_maps(div_csv)
    divisions = divisions_for_season(season)
    division_names: list[str] = list(divisions or [])
    selected_conf: str | None = None
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
        sel_conference=selected_conf,
        sel_division=div,
    )


def _build_statistics_view_vars(
    locked_team_id: int | None = None,
    locked_team_slug: str | None = None,
) -> dict[str, object]:
    """Context dict for statistics.html or team page statistics panel.

    When locked_team_id is set, stats are restricted to that team and query
    `team_id` is ignored. URLs for sort/expand use team_page when slug is set.
    """
    season = get_current_season()
    team_id = locked_team_id if locked_team_id is not None else request.args.get("team_id", type=int)
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
    league_slug_cfg = str(current_app.config.get("LEAGUE_SLUG") or "")
    bowl_fhm_for_fantasy: tuple[int, ...] | None = None
    if league_slug_cfg == "bowl-fantasy":
        bowl_fhm_for_fantasy = bowl_nhl_league_ids(db.session)
        if not bowl_fhm_for_fantasy:
            bowl_fhm_for_fantasy = (0,)
        teams = [t for t in teams if t.fhm_league_id in bowl_fhm_for_fantasy]
    teams_by_id = {t.id: t for t in teams}
    if not season:
        return {
            "season": None,
            "teams": teams,
            "teams_by_id": teams_by_id,
            "skaters": [],
            "goalies_list": [],
            "sort": sort,
            "g_sort": "wins",
            "team_id": team_id,
            "show_goalies": goalies,
            "segment": segment,
            "pos_filter": pos_filter,
            "stats_expanded": False,
            "total_skaters": 0,
            "total_goalies": 0,
            "statistics_expand_url": "",
            "statistics_collapsed_url": "",
            "stats_page_limit": stats_page_limit,
        }

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
    sk_fo_pct = case(
        (
            PlayerSkaterStat.faceoffs > 0,
            cast(func.coalesce(PlayerSkaterStat.faceoff_wins, 0), Float) / PlayerSkaterStat.faceoffs,
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
        "ogr": PlayerSkaterStat.game_rating_off.desc().nulls_last(),
        "dgr": PlayerSkaterStat.game_rating_def.desc().nulls_last(),
        "takeaways": PlayerSkaterStat.takeaways.desc().nulls_last(),
        "giveaways": PlayerSkaterStat.giveaways.desc().nulls_last(),
        "fo_pct": sk_fo_pct.desc().nulls_last(),
        "fights": PlayerSkaterStat.fights.desc().nulls_last(),
        "fights_won": PlayerSkaterStat.fights_won.desc().nulls_last(),
        "pdo": PlayerSkaterStat.pdo.desc().nulls_last(),
    }
    if sort not in sk_order_map:
        sort = "points"

    sk_q = select(PlayerSkaterStat, Player).join(
        Player, PlayerSkaterStat.player_id == Player.id
    ).where(
        PlayerSkaterStat.season_id == season.id,
        PlayerSkaterStat.stat_segment == segment,
    )
    if bowl_fhm_for_fantasy is not None:
        sk_q = sk_q.join(Team, PlayerSkaterStat.team_id == Team.id).where(
            Team.fhm_league_id.in_(bowl_fhm_for_fantasy)
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

    gq = select(PlayerGoalieStat, Player).join(
        Player, PlayerGoalieStat.player_id == Player.id
    ).where(
        PlayerGoalieStat.season_id == season.id,
        PlayerGoalieStat.stat_segment == segment,
    )
    if bowl_fhm_for_fantasy is not None:
        gq = gq.join(Team, PlayerGoalieStat.team_id == Team.id).where(
            Team.fhm_league_id.in_(bowl_fhm_for_fantasy)
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

    _stat_params: dict[str, object] = {
        "segment": segment,
        "sort": sort,
        "g_sort": g_sort,
        "pos": pos_filter if pos_filter != "all" else None,
        "goalies": 1 if goalies else None,
    }
    if locked_team_id is None:
        _stat_params["team_id"] = team_id
    _stat_params = {k: v for k, v in _stat_params.items() if v is not None}
    if locked_team_slug:
        statistics_expand_url = url_for(
            "main.team_page",
            slug=locked_team_slug,
            panel="statistics",
            **{**_stat_params, "expanded": 1},
        )
        statistics_collapsed_url = url_for(
            "main.team_page",
            slug=locked_team_slug,
            panel="statistics",
            **_stat_params,
        )
    else:
        statistics_expand_url = url_for("main.statistics", **{**_stat_params, "expanded": 1})
        statistics_collapsed_url = url_for("main.statistics", **_stat_params)

    return {
        "season": season,
        "teams": teams,
        "teams_by_id": teams_by_id,
        "skaters": skaters,
        "goalies_list": goalies_list,
        "sort": sort,
        "g_sort": g_sort,
        "team_id": team_id,
        "show_goalies": goalies,
        "segment": segment,
        "pos_filter": pos_filter,
        "stats_expanded": stats_expanded,
        "total_skaters": total_skaters,
        "total_goalies": total_goalies,
        "statistics_expand_url": statistics_expand_url,
        "statistics_collapsed_url": statistics_collapsed_url,
        "stats_page_limit": stats_page_limit,
    }


@main_bp.get("/statistics")
def statistics():
    return render_template("statistics.html", **_build_statistics_view_vars())


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


def _slugify_award_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _history_award_trophy_scan_dirs(static_root: Path, league_slug: str) -> tuple[Path, ...]:
    """League trophy art: prefer ``img/trophies/<slug>/``, then ``img/history/trophies/<slug>/`` fallback."""
    return (
        static_root / "img" / "trophies" / league_slug,
        static_root / "img" / "history" / "trophies" / league_slug,
    )


# Award slug -> try these file stems (in ``img/trophies/``) when the canonical stem has no file.
_TROPHY_STEM_ALIASES: dict[str, tuple[str, ...]] = {
    "boiledegg_s_trophy": ("boiledeggs_trophy",),
    "the_masters_green_jacket": ("masters_green_jacket",),
}

# File stem (slugified ``Path.stem``) -> also register these award slug keys (same image path).
_TROPHY_FILE_STEM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "boiledeggs_trophy": ("boiledegg_s_trophy",),
    "masters_green_jacket": ("the_masters_green_jacket",),
}


def _history_award_trophy_lookup_stems(award_name: str) -> tuple[str, ...]:
    key = _slugify_award_key(award_name)
    if not key:
        return ()
    alts = _TROPHY_STEM_ALIASES.get(key, ())
    return (key,) + alts


def _history_award_trophy_stem_map() -> dict[str, str]:
    """Scan trophy image dirs once: slugified file stem -> static relpath (first dir wins per stem)."""
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "bowl-fantasy")
    static_root = Path(str(current_app.static_folder or ""))
    out: dict[str, str] = {}
    for base in _history_award_trophy_scan_dirs(static_root, league_slug):
        if not base.is_dir():
            continue
        try:
            paths = [p for p in base.iterdir() if p.is_file()]
        except OSError:
            continue
        paths.sort(key=lambda x: x.name.lower())
        for p in paths:
            if p.suffix.lower() not in (".png", ".webp", ".jpg", ".jpeg", ".svg"):
                continue
            stem_key = _slugify_award_key(p.stem)
            if not stem_key or stem_key in out:
                continue
            try:
                rel = p.relative_to(static_root)
            except ValueError:
                continue
            rel_s = str(rel).replace("\\", "/")
            out[stem_key] = rel_s
            for syn in _TROPHY_FILE_STEM_SYNONYMS.get(stem_key, ()):
                if syn not in out:
                    out[syn] = rel_s
    return out


def _history_award_trophy_rel_from_map(stem_map: dict[str, str], award_name: str) -> str | None:
    """Resolve trophy static path using a pre-built :func:`_history_award_trophy_stem_map`."""
    for cand in _history_award_trophy_lookup_stems(award_name):
        hit = stem_map.get(cand)
        if hit:
            return hit
    static_root = Path(str(current_app.static_folder or ""))
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "bowl-fantasy")
    for base in _history_award_trophy_scan_dirs(static_root, league_slug):
        if not base.is_dir():
            continue
        for cand in _history_award_trophy_lookup_stems(award_name):
            for ext in ("png", "webp", "jpg", "jpeg", "svg"):
                p = base / f"{cand}.{ext}"
                if p.is_file():
                    rel = p.relative_to(static_root)
                    return str(rel).replace("\\", "/")
    return None


# Order of award cards on the History page (matches common NHL-style trophy sheet layout).
_AWARD_PANEL_ORDER: tuple[str, ...] = (
    "ART ROSS TROPHY",
    "RICHARD TROPHY",
    "NORRIS TROPHY",
    "BOURQUE TROPHY",
    "LANGWAY TROPHY",
    "CALDER TROPHY",
    "SELKE TROPHY",
    "VEZINA TROPHY",
    "LADY BYNG TROPHY",
    "CONN SMYTHE TROPHY",
    "HART TROPHY",
    "JACK ADAMS TROPHY",
    "WILLIAM JENNINGS TROPHY",
    "TED LINDSAY TROPHY",
    "MASTERTON TROPHY",
    "BOILEDEGG'S TROPHY",
    "PRINCE OF WALES TROPHY",
    "CLARENCE CAMPBELL TROPHY",
    "BOWL CUP TROPHY",
    "JIM GREGORY TROPHY",
    "MARK MESSIER LEADERSHIP AWARD",
    "ROGER CROZIER SAVING GRACE TROPHY",
    "PLUS/MINUS TROPHY",
    "THE MASTERS' GREEN JACKET",
    "BOWL RISING STAR",
)

# Sheet / DB typos → canonical key in ``_AWARD_PANEL_ORDER`` (after ``_norm_award_title``).
_AWARD_NAME_ALIASES: dict[str, str] = {
    "LANGWY TROPHY": "LANGWAY TROPHY",
}


def _norm_award_title(s: str) -> str:
    """Uppercase, collapse internal whitespace (handles ``WILLIAM JENNINGS  TROPHY`` style)."""
    return " ".join((s or "").upper().split())


def _award_panel_sort_index(award_name: str) -> int:
    key = _norm_award_title(award_name)
    key = _AWARD_NAME_ALIASES.get(key, key)
    for i, canonical in enumerate(_AWARD_PANEL_ORDER):
        if _norm_award_title(canonical) == key:
            return i
    return len(_AWARD_PANEL_ORDER) + 1


_SHEET_SEASON_LABEL_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _history_award_sheet_season_from_notes(notes: str | None) -> str | None:
    """Parse ``sheet_season=YYYY-YY`` from full ``notes`` (may be ``a; sheet_season=…; b``)."""
    for part in (notes or "").split(";"):
        p = part.strip()
        if p.startswith("sheet_season="):
            tok = p.split("=", 1)[1].strip().split(";")[0].strip()
            if _SHEET_SEASON_LABEL_RE.match(tok):
                return tok
    return None


def _sheet_season_start_year(label: str | None) -> int | None:
    tok = (label or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", tok)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _history_award_start_year(a: HistoryAward) -> int | None:
    tok = _history_award_sheet_season_from_notes(a.notes)
    if tok:
        sy = _sheet_season_start_year(tok)
        if sy is not None:
            return sy
    if a.season and (a.season.label or "").strip():
        sy = _sheet_season_start_year(a.season.label)
        if sy is not None:
            return sy
    return None


def _attach_history_award_season_teams(awards: list[HistoryAward]) -> None:
    """Annotate awards with ``season_team`` resolved for the winner's season.

    For player awards that do not store ``team_id``, infer team by counting game-stat rows
    for that player in the award season window (start year and start+1), choosing the team
    with the most appearances.
    """
    key_rows: list[tuple[int, int, HistoryAward]] = []
    for a in awards:
        sy = _history_award_start_year(a)
        if a.player_id is None or sy is None:
            continue
        key_rows.append((int(a.player_id), sy, a))
    if not key_rows:
        return

    player_ids = sorted({pid for pid, _, _ in key_rows})
    season_years = sorted({sy for _, sy, _ in key_rows})
    # player_id -> year -> team_id -> appearances
    by_player_year_team: dict[int, dict[int, dict[int, int]]] = {}
    # (player_id, season_year) -> (gp, team_id, team_fhm_id)
    career_best: dict[tuple[int, int], tuple[int, int | None, int | None]] = {}

    def _add_career_rows(rows: list[tuple[object, object, object, object, object]]) -> None:
        for pid_raw, year_raw, gp_raw, team_id_raw, team_fhm_raw in rows:
            try:
                pid = int(pid_raw)
                year = int(year_raw)
                gp = int(gp_raw or 0)
            except (TypeError, ValueError):
                continue
            team_id: int | None
            team_fhm_id: int | None
            try:
                team_id = int(team_id_raw) if team_id_raw is not None else None
            except (TypeError, ValueError):
                team_id = None
            try:
                team_fhm_id = int(team_fhm_raw) if team_fhm_raw is not None else None
            except (TypeError, ValueError):
                team_fhm_id = None
            k = (pid, year)
            prev = career_best.get(k)
            if prev is None or gp > prev[0]:
                career_best[k] = (gp, team_id, team_fhm_id)

    def _add_counts(rows: list[tuple[object, object, object, object]]) -> None:
        for pid_raw, year_raw, team_id_raw, n_raw in rows:
            try:
                pid = int(pid_raw)
                year = int(year_raw)
                team_id = int(team_id_raw)
                n = int(n_raw)
            except (TypeError, ValueError):
                continue
            by_player_year_team.setdefault(pid, {}).setdefault(year, {})
            by_player_year_team[pid][year][team_id] = by_player_year_team[pid][year].get(team_id, 0) + n

    sk_career = db.session.execute(
        select(
            PlayerSkaterCareerLine.player_id,
            PlayerSkaterCareerLine.season_year,
            PlayerSkaterCareerLine.gp,
            PlayerSkaterCareerLine.team_id,
            PlayerSkaterCareerLine.team_fhm_id,
        ).where(
            PlayerSkaterCareerLine.player_id.in_(player_ids),
            PlayerSkaterCareerLine.season_year.in_(season_years),
        )
    ).all()
    _add_career_rows(sk_career)

    gk_career = db.session.execute(
        select(
            PlayerGoalieCareerLine.player_id,
            PlayerGoalieCareerLine.season_year,
            PlayerGoalieCareerLine.gp,
            PlayerGoalieCareerLine.team_id,
            PlayerGoalieCareerLine.team_fhm_id,
        ).where(
            PlayerGoalieCareerLine.player_id.in_(player_ids),
            PlayerGoalieCareerLine.season_year.in_(season_years),
        )
    ).all()
    _add_career_rows(gk_career)

    sk_rows = db.session.execute(
        select(
            GameSkaterStat.player_id,
            extract("year", Game.game_date),
            GameSkaterStat.team_id,
            func.count(GameSkaterStat.id),
        )
        .join(Game, GameSkaterStat.game_id == Game.id)
        .where(
            GameSkaterStat.player_id.in_(player_ids),
            GameSkaterStat.team_id.isnot(None),
            Game.game_date.isnot(None),
        )
        .group_by(GameSkaterStat.player_id, extract("year", Game.game_date), GameSkaterStat.team_id)
    ).all()
    _add_counts(sk_rows)

    gk_rows = db.session.execute(
        select(
            GameGoalieStat.player_id,
            extract("year", Game.game_date),
            GameGoalieStat.team_id,
            func.count(GameGoalieStat.id),
        )
        .join(Game, GameGoalieStat.game_id == Game.id)
        .where(
            GameGoalieStat.player_id.in_(player_ids),
            GameGoalieStat.team_id.isnot(None),
            Game.game_date.isnot(None),
        )
        .group_by(GameGoalieStat.player_id, extract("year", Game.game_date), GameGoalieStat.team_id)
    ).all()
    _add_counts(gk_rows)

    if not by_player_year_team:
        by_player_year_team = {}

    season_team_id_by_award_id: dict[int, int] = {}
    team_fhm_ids = sorted(
        {
            int(v[2])
            for v in career_best.values()
            if v[2] is not None and str(v[2]).strip() != ""
        }
    )
    team_by_fhm: dict[int, Team] = {}
    if team_fhm_ids:
        team_by_fhm = {
            int(str(t.fhm_team_id).strip()): t
            for t in db.session.scalars(select(Team).where(Team.fhm_team_id.in_(team_fhm_ids))).all()
            if t.fhm_team_id is not None and str(t.fhm_team_id).strip() != ""
        }

    for pid, sy, a in key_rows:
        car = career_best.get((pid, sy))
        if car is not None:
            _, car_team_id, car_team_fhm = car
            if car_team_id is not None:
                season_team_id_by_award_id[a.id] = car_team_id
                continue
            if car_team_fhm is not None and car_team_fhm in team_by_fhm:
                season_team_id_by_award_id[a.id] = team_by_fhm[car_team_fhm].id
                continue

        team_counts: dict[int, int] = {}
        for yr in (sy, sy + 1):
            for team_id, n in by_player_year_team.get(pid, {}).get(yr, {}).items():
                team_counts[team_id] = team_counts.get(team_id, 0) + n
        if not team_counts:
            continue
        best_team_id = max(team_counts.items(), key=lambda x: (x[1], -x[0]))[0]
        season_team_id_by_award_id[a.id] = best_team_id

    if not season_team_id_by_award_id:
        return
    teams = {
        t.id: t
        for t in db.session.scalars(
            select(Team).where(Team.id.in_(sorted(set(season_team_id_by_award_id.values()))))
        ).all()
    }
    for a in awards:
        team_id = season_team_id_by_award_id.get(a.id)
        setattr(a, "season_team", teams.get(team_id) if team_id is not None else None)


def _history_award_year_token(a: HistoryAward) -> object:
    """Canonical trophy year for dedupe/sort (sheet label, else ``Season.label``, else ``season_id``)."""
    tok = _history_award_sheet_season_from_notes(a.notes)
    if tok:
        return tok
    if getattr(a, "season", None) is not None and (a.season.label or "").strip():
        return (a.season.label or "").strip()
    return int(a.season_id)


def _history_award_year_sort_key(a: HistoryAward) -> tuple[int, int, int]:
    """Prefer ``sheet_season=YYYY-YY`` in ``notes`` so ordering works when every row shares one DB season."""

    def _end_year(start_year: int, yy_two: str) -> int:
        yy_i = int(yy_two)
        century = start_year - (start_year % 100)
        cand = century + yy_i
        if cand < start_year:
            cand += 100
        return cand

    for token in (
        _history_award_sheet_season_from_notes(a.notes),
        (a.season.label or "").strip() if getattr(a, "season", None) is not None else "",
    ):
        if not token:
            continue
        m = _SHEET_SEASON_LABEL_RE.match(token)
        if m:
            y1 = int(m.group(1))
            try:
                y2 = _end_year(y1, m.group(2))
                return (1, y2, y1)
            except ValueError:
                pass
    return (0, a.season_id, 0)


def _history_award_dedupe_key(a: HistoryAward) -> tuple[object, object, str]:
    """One row per trophy year + player (or distinct ``notes`` when ``player_id`` is null).

    Tie rows often share the same sheet year with no resolved player; keep them separate.
    """
    return (_history_award_year_token(a), a.player_id, (a.notes or "").strip())


def _history_award_dedupe_rank(a: HistoryAward) -> tuple[int, int, int, int, int]:
    """Prefer ``staff_fhm_id`` / ``team_id`` / ``player_id`` over longer ``notes`` (fixes ``unresolved_*`` dupes)."""
    return (
        1 if (getattr(a, "staff_fhm_id", None) or "").strip() else 0,
        1 if a.team_id is not None else 0,
        1 if a.player_id is not None else 0,
        len((a.notes or "").strip()),
        -a.id,
    )


def _dedupe_history_awards(rows: list[HistoryAward]) -> list[HistoryAward]:
    """Drop duplicate DB rows (re-import). Do not merge different trophy years for the same player."""
    best: dict[tuple[object, object], HistoryAward] = {}
    for a in rows:
        k = _history_award_dedupe_key(a)
        prev = best.get(k)
        if prev is None:
            best[k] = a
            continue
        ra = _history_award_dedupe_rank(a)
        rb = _history_award_dedupe_rank(prev)
        if ra > rb or (ra == rb and a.id < prev.id):
            best[k] = a
    return list(best.values())


def _collapse_same_trophy_year_history_awards(rows: list[HistoryAward]) -> list[HistoryAward]:
    """Merge rows that share the same trophy year after CSV re-import drift.

    Typical case: one row has ``player_id`` + ``sheet_season=…`` and another has the same season with
    ``unresolved_player=…`` and no player — keep the resolved row. When multiple rows share the year
    and have distinct ``player_id`` values (e.g. Jennings tandem), keep all resolved rows.
    """
    if len(rows) <= 1:
        return rows
    from collections import defaultdict

    by_year: dict[object, list[HistoryAward]] = defaultdict(list)
    for a in rows:
        by_year[_history_award_year_token(a)].append(a)
    out: list[HistoryAward] = []
    for group in by_year.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        resolved = [a for a in group if a.player_id is not None]
        if len(resolved) >= 2:
            out.extend(sorted(resolved, key=lambda a: a.id))
            continue
        if len(resolved) == 1:
            out.append(resolved[0])
            continue
        out.append(max(group, key=_history_award_dedupe_rank))
    return out


def _build_award_panels(awards: list[HistoryAward]) -> list[dict]:
    """One panel per ``award_name``: latest season is featured; older rows listed below."""
    from collections import defaultdict

    trophy_stem_map = _history_award_trophy_stem_map()
    by_name: dict[str, list[HistoryAward]] = defaultdict(list)
    for a in awards:
        key = (a.award_name or "").strip() or "Award"
        by_name[key].append(a)
    panels: list[dict] = []
    for name, rows in by_name.items():
        rows = _dedupe_history_awards(rows)
        rows = _collapse_same_trophy_year_history_awards(rows)
        rows_sorted = sorted(rows, key=_history_award_year_sort_key, reverse=True)
        featured = rows_sorted[0]
        past = rows_sorted[1:]
        panels.append(
            {
                "award_name": name,
                "featured": featured,
                "past": past,
                "trophy_rel": _history_award_trophy_rel_from_map(trophy_stem_map, name),
                "coach_award": is_staff_history_award(name),
                "jim_gregory_award": is_jim_gregory_award(name),
                "team_award": is_team_history_award(name),
            }
        )
    panels.sort(
        key=lambda p: (_award_panel_sort_index(p["award_name"]), _norm_award_title(p["award_name"])),
    )
    return panels


@main_bp.get("/history")
def history():
    awards = db.session.scalars(
        select(HistoryAward)
        .options(
            joinedload(HistoryAward.season),
            joinedload(HistoryAward.player).joinedload(Player.current_team),
            joinedload(HistoryAward.team),
        )
        .order_by(HistoryAward.season_id.desc())
        .limit(2000)
    ).all()
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    attach_coach_award_displays(awards, db.session, raw_dir)
    _attach_history_award_season_teams(awards)
    award_panels = _build_award_panels(awards)
    seasons_on_file = import_folder_season_labels(
        Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    )
    champion_banners = champion_banner_urls()
    return render_template(
        "history.html",
        award_panels=award_panels,
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


@main_bp.get("/season-records")
def league_season_records():
    from app.services.league_season_records import build_league_season_records_bundle

    season_records_rs_sections, season_records_po_sections = build_league_season_records_bundle(db.session)
    return render_template(
        "season_records.html",
        season_records_rs_sections=season_records_rs_sections,
        season_records_po_sections=season_records_po_sections,
    )


@main_bp.get("/milestones")
def milestones():
    from app.services.milestones import build_milestone_sections

    split = request.args.get("split", "rs") or "rs"
    if split not in ("rs", "po"):
        split = "rs"
    skater_sections, goalie_sections = build_milestone_sections(db.session, split=split)
    return render_template(
        "milestones.html",
        split=split,
        skater_sections=skater_sections,
        goalie_sections=goalie_sections,
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
                unresolved_fhm_to_player_id: dict[str, int] = {}
                unresolved_id_set = set(unresolved_ids)
                unresolved_db_only_ids: set[int] = set()
                for p in players:
                    if p.id not in unresolved_id_set:
                        continue
                    if p.fhm_player_id is None:
                        unresolved_db_only_ids.add(p.id)
                    else:
                        fhm_pid = str(p.fhm_player_id).strip()
                        if fhm_pid:
                            unresolved_fhm_to_player_id[fhm_pid] = p.id
                        else:
                            unresolved_db_only_ids.add(p.id)

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
                        pid = unresolved_fhm_to_player_id.get(pid_s)
                        # Backward compatibility: DB-id matching only for players missing FHM id.
                        if pid is None:
                            try:
                                pid_db = int(pid_s)
                            except ValueError:
                                continue
                            if pid_db not in unresolved_db_only_ids:
                                continue
                            pid = pid_db
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


@main_bp.get("/undrafted-prospects")
def undrafted_prospects():
    """Players with no NHL/BOWL draft pick, no NHL/BOWL org rights, age within league cap, optional filters."""
    pos = request.args.get("position")
    age_param = (request.args.get("age") or "").strip()
    ud_expanded = request.args.get("expanded") == "1"
    page_limit = 50
    session = db.session
    age_ref = season_age_reference_date(get_current_season())
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    undrafted_max_age = undrafted_prospects_max_age(league_slug)
    ud_age_options = undrafted_prospects_age_filter_options(league_slug)

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
        if ai in ud_age_options:
            age_exact = ai

    drafted_subq = (
        select(DraftPick.player_id)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .where(DraftPick.player_id.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
    )
    rights_ids = bowl_org_rights_player_ids(session)
    q_where = [
        Player.retired.is_(False),
        Player.birth_date.isnot(None),
        Player.id.not_in(drafted_subq),
    ]
    if rights_ids:
        q_where.append(Player.id.not_in(rights_ids))
    q = select(Player).where(*q_where)
    players = session.scalars(q).unique().all()

    pool: list[Player] = []
    for p in players:
        age = _player_age_years(p.birth_date, age_ref)
        if age is None or age > undrafted_max_age:
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
        undrafted_age_options=ud_age_options,
        undrafted_max_age=undrafted_max_age,
    )


@main_bp.get("/free-agents")
def free_agents():
    """Free pool: not on NHL/BOWL roster and no NHL/BOWL org contract/prospect link; excludes undrafted pool."""
    session = db.session
    age_ref = season_age_reference_date(get_current_season())

    role = (request.args.get("role") or "fwd").strip().lower()
    if role not in FA_ROLES:
        role = "fwd"

    view = (request.args.get("view") or "overview").strip().lower()
    if role == "g":
        if view not in GOALIE_VIEWS:
            view = "overview"
        active_headers = FA_GOALIE_MAIN if view == "overview" else FA_GOALIE_MENTAL
    else:
        if view not in SKATER_VIEWS:
            view = "overview"
        view_headers = {
            "overview": FA_SKATER_OVERVIEW,
            "offense": FA_SKATER_OFFENSE,
            "defense": FA_SKATER_DEFENSE,
            "mental": FA_SKATER_MENTAL,
            "physical": FA_SKATER_PHYSICAL,
        }
        active_headers = view_headers[view]

    attr_keys = frozenset(h[2] for h in active_headers)
    valid_sorts = frozenset({"rank", "player", "age", "abi", "pot", *attr_keys})
    sort_default_desc = frozenset({"rank", "abi", "pot", *attr_keys})

    sort_col = request.args.get("sort") or "pot"
    order = request.args.get("order") or "desc"
    if sort_col not in valid_sorts:
        sort_col = "pot"
    if order not in ("asc", "desc"):
        order = "desc"

    fa_expanded = request.args.get("expanded") == "1"
    page_limit = 80

    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    ud_cap = undrafted_prospects_max_age(league_slug)
    pool = fetch_free_agent_players(
        session, role, age_ref=age_ref, undrafted_max_age=ud_cap, league_slug=league_slug
    )

    items: list[dict[str, object]] = []
    for pl in pool:
        rr = get_player_ratings_row(pl.fhm_player_id)
        attrs: dict[str, float | None] = {}
        attrs_display: dict[str, object | None] = {}
        for _full, _abbr, key in active_headers:
            raw_cell = rr.get(key) if rr else None
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
    elif sort_col == "age":

        def age_key(it: dict) -> tuple:
            pl = it["pl"]
            ag = it["age"]
            if ag is None:
                sentinel = float("-inf") if rev else float("inf")
                return (sentinel, pl.full_name or "", pl.id)
            return (float(ag), (pl.full_name or "").lower(), pl.id)

        items.sort(key=age_key, reverse=rev)
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

    rows_out: list[dict[str, object]] = []
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
    if fa_expanded or total_n <= page_limit:
        display_rows = rows_out
    else:
        display_rows = rows_out[:page_limit]

    return render_template(
        "free_agents.html",
        fa_rows=display_rows,
        fa_total=total_n,
        fa_page_limit=page_limit,
        fa_expanded=fa_expanded,
        fa_role=role,
        fa_view=view,
        fa_headers=active_headers,
        fa_sort=sort_col,
        fa_order=order,
        fa_sort_desc_defaults=sort_default_desc,
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
    list[dict[str, object]],
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

    age_ref = season_age_reference_date(season)

    def _fmt_height_inches(height_inches: int | None) -> str:
        if height_inches is None:
            return "—"
        try:
            h = int(height_inches)
        except (TypeError, ValueError):
            return "—"
        if h <= 0:
            return "—"
        return f"{h // 12}'{h % 12}\""

    def _shoots_label(raw: str | None) -> str:
        txt = (raw or "").strip().lower()
        if txt.startswith("l"):
            return "Left"
        if txt.startswith("r"):
            return "Right"
        return (raw or "—").strip() or "—"

    def _line_player_card(pl: Player | None) -> dict[str, object] | None:
        if not pl:
            return None
        rr = get_player_ratings_row(pl.fhm_player_id)
        is_goalie = _player_is_goalie_position(pl)
        if is_goalie:
            cat = goalie_category_averages(rr)
            attrs = {
                "goa": int(round(cat.get("goa"))) if cat.get("goa") is not None else None,
                "men": int(round(cat.get("men"))) if cat.get("men") is not None else None,
            }
        else:
            cat = skater_category_averages(rr)
            attrs = {
                "off": int(round(cat.get("off"))) if cat.get("off") is not None else None,
                "def": int(round(cat.get("def"))) if cat.get("def") is not None else None,
                "phy": int(round(cat.get("phy"))) if cat.get("phy") is not None else None,
                "men": int(round(cat.get("men"))) if cat.get("men") is not None else None,
            }
        return {
            "player": pl,
            "is_goalie": is_goalie,
            "age": _player_age_years(pl.birth_date, age_ref),
            "shoots": _shoots_label(pl.shoots_catches),
            "height": _fmt_height_inches(pl.height_inches),
            "weight": int(pl.weight_lbs) if pl.weight_lbs is not None else None,
            "attrs": attrs,
        }

    def _slot(label: str, key: str) -> dict[str, object]:
        return {"label": label, "card": _line_player_card(lp(key))}

    lines_sections: list[dict[str, object]] = [
        {
            "title": "Even Strength",
            "line_label": "1st Line",
            "layout": "five",
            "slots": [
                _slot("LW", "ES L1 LW"),
                _slot("C", "ES L1 C"),
                _slot("RW", "ES L1 RW"),
                _slot("LD", "ES L1 LD"),
                _slot("RD", "ES L1 RD"),
            ],
        },
        {
            "title": "Even Strength",
            "line_label": "2nd Line",
            "layout": "five",
            "slots": [
                _slot("LW", "ES L2 LW"),
                _slot("C", "ES L2 C"),
                _slot("RW", "ES L2 RW"),
                _slot("LD", "ES L2 LD"),
                _slot("RD", "ES L2 RD"),
            ],
        },
        {
            "title": "Even Strength",
            "line_label": "3rd Line",
            "layout": "five",
            "slots": [
                _slot("LW", "ES L3 LW"),
                _slot("C", "ES L3 C"),
                _slot("RW", "ES L3 RW"),
                _slot("LD", "ES L3 LD"),
                _slot("RD", "ES L3 RD"),
            ],
        },
        {
            "title": "Even Strength",
            "line_label": "4th Line",
            "layout": "five",
            "slots": [
                _slot("LW", "ES L4 LW"),
                _slot("C", "ES L4 C"),
                _slot("RW", "ES L4 RW"),
                _slot("LD", "ES L4 LD"),
                _slot("RD", "ES L4 RD"),
            ],
        },
        {
            "title": "Powerplay",
            "line_label": "Unit 1 (5v4)",
            "layout": "five",
            "slots": [
                _slot("LW", "PP5on4 L1 LW"),
                _slot("C", "PP5on4 L1 C"),
                _slot("RW", "PP5on4 L1 RW"),
                _slot("LD", "PP5on4 L1 LD"),
                _slot("RD", "PP5on4 L1 RD"),
            ],
        },
        {
            "title": "Powerplay",
            "line_label": "Unit 2 (5v4)",
            "layout": "five",
            "slots": [
                _slot("LW", "PP5on4 L2 LW"),
                _slot("C", "PP5on4 L2 C"),
                _slot("RW", "PP5on4 L2 RW"),
                _slot("LD", "PP5on4 L2 LD"),
                _slot("RD", "PP5on4 L2 RD"),
            ],
        },
        {
            "title": "Penalty Kill",
            "line_label": "Unit 1 (4v5)",
            "layout": "four",
            "slots": [
                _slot("F1", "PK4on5 L1 F1"),
                _slot("F2", "PK4on5 L1 F2"),
                _slot("LD", "PK4on5 L1 LD"),
                _slot("RD", "PK4on5 L1 RD"),
            ],
        },
        {
            "title": "Penalty Kill",
            "line_label": "Unit 2 (4v5)",
            "layout": "four",
            "slots": [
                _slot("F1", "PK4on5 L2 F1"),
                _slot("F2", "PK4on5 L2 F2"),
                _slot("LD", "PK4on5 L2 LD"),
                _slot("RD", "PK4on5 L2 RD"),
            ],
        },
        {
            "title": "Penalty Kill",
            "line_label": "Unit 3 (4v5)",
            "layout": "four",
            "slots": [
                _slot("F1", "PK4on5 L3 F1"),
                _slot("F2", "PK4on5 L3 F2"),
                _slot("LD", "PK4on5 L3 LD"),
                _slot("RD", "PK4on5 L3 RD"),
            ],
        },
        {
            "title": "Goalies",
            "line_label": "Depth",
            "layout": "goalie",
            "slots": [
                _slot("Starter", "Goalie 1"),
                _slot("Backup", "Goalie 2"),
            ],
        },
    ]
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
        flags: list[str] = []
        if c.has_nmc:
            flags.append("NMC")
        if c.has_ntc:
            flags.append("NTC")
        if c.is_elc:
            flags.append("ELC")
        salary_rows.append(
            {
                "player": p,
                "pos": player_positions_display_label(p),
                "age": _player_age_years(p.birth_date, age_ref),
                "salary": int(c.average_salary or 0),
                "group": salary_group,
                "flags": flags,
                "year_cells": year_cells,
                "years_left": contract_years_remaining_major(
                    p.fhm_player_id, season_start_year, raw_import_dir
                ),
            }
        )
    group_order = {"Forwards": 0, "Defensemen": 1, "Goalies": 2, "Minors": 3}
    salary_rows.sort(key=lambda r: (group_order.get(str(r["group"]), 9), -int(r["salary"]), str(r["player"].full_name)))
    return depth_chart, lines_sections, lines_name_to_id, salary_rows, salary_total


def _player_is_goalie_position(player: Player) -> bool:
    raw = (player.position or "").strip().upper().replace("/", " ")
    first = raw.split()[0] if raw else ""
    return first == "G"


def _pick_skater_stat_leader(
    pairs: list[tuple[PlayerSkaterStat, Player]],
    value_fn,
    *,
    maximize: bool,
) -> tuple[PlayerSkaterStat, Player] | None:
    best: tuple[PlayerSkaterStat, Player] | None = None
    best_v: int | float | None = None
    for stat_row, pl in pairs:
        if _player_is_goalie_position(pl):
            continue
        v = value_fn(stat_row)
        if v is None:
            continue
        if best is None or best_v is None:
            best, best_v = (stat_row, pl), v
            continue
        if maximize:
            better = v > best_v
        else:
            better = v < best_v
        if better or (v == best_v and pl.id < best[1].id):
            best, best_v = (stat_row, pl), v
    return best


def _pick_goalie_stat_leader(
    pairs: list[tuple[PlayerGoalieStat, Player]],
    value_fn,
    *,
    maximize: bool,
    min_gp: int = 1,
) -> tuple[PlayerGoalieStat, Player] | None:
    best: tuple[PlayerGoalieStat, Player] | None = None
    best_v: float | None = None
    for stat_row, pl in pairs:
        if not _player_is_goalie_position(pl):
            continue
        if (stat_row.gp or 0) < min_gp:
            continue
        v = value_fn(stat_row)
        if v is None:
            continue
        fv = float(v)
        if best is None or best_v is None:
            best, best_v = (stat_row, pl), fv
            continue
        if maximize:
            better = fv > best_v + 1e-9
        else:
            better = fv < best_v - 1e-9
        tied = abs(fv - best_v) <= 1e-9
        if better or (tied and pl.id < best[1].id):
            best, best_v = (stat_row, pl), fv
    return best


def _fmt_plus_minus_pm(val: int) -> str:
    if val > 0:
        return f"+{val}"
    return str(val)


def _fmt_save_pct_leader(val: float) -> str:
    s = f"{float(val):.3f}"
    return s[1:] if s.startswith("0") else s


def build_team_leader_panel_rows(team: Team, season: Season) -> list[dict[str, object]]:
    sk_pairs = list(
        db.session.execute(
            select(PlayerSkaterStat, Player)
            .join(Player, PlayerSkaterStat.player_id == Player.id)
            .where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.team_id == team.id,
                PlayerSkaterStat.stat_segment == "rs",
            )
        ).all()
    )
    gk_pairs = list(
        db.session.execute(
            select(PlayerGoalieStat, Player)
            .join(Player, PlayerGoalieStat.player_id == Player.id)
            .where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.team_id == team.id,
                PlayerGoalieStat.stat_segment == "rs",
            )
        ).all()
    )
    out: list[dict[str, object]] = []

    b = _pick_skater_stat_leader(sk_pairs, lambda r: r.goals, maximize=True)
    out.append(
        {
            "abbr": "G",
            "player": b[1] if b else None,
            "value": str(b[0].goals) if b else None,
        }
    )
    b = _pick_skater_stat_leader(sk_pairs, lambda r: r.assists, maximize=True)
    out.append(
        {
            "abbr": "AST",
            "player": b[1] if b else None,
            "value": str(b[0].assists) if b else None,
        }
    )
    b = _pick_skater_stat_leader(sk_pairs, lambda r: r.points, maximize=True)
    out.append(
        {
            "abbr": "PTS",
            "player": b[1] if b else None,
            "value": str(b[0].points) if b else None,
        }
    )
    b = _pick_skater_stat_leader(sk_pairs, lambda r: r.pim, maximize=True)
    out.append(
        {
            "abbr": "PIM",
            "player": b[1] if b else None,
            "value": str(b[0].pim) if b else None,
        }
    )
    b = _pick_skater_stat_leader(sk_pairs, lambda r: r.plus_minus, maximize=True)
    out.append(
        {
            "abbr": "+/-",
            "player": b[1] if b else None,
            "value": _fmt_plus_minus_pm(b[0].plus_minus) if b and b[0].plus_minus is not None else None,
        }
    )
    b = _pick_goalie_stat_leader(gk_pairs, lambda r: r.gaa, maximize=False)
    out.append(
        {
            "abbr": "GAA",
            "player": b[1] if b else None,
            "value": f"{float(b[0].gaa):.2f}" if b and b[0].gaa is not None else None,
        }
    )
    b = _pick_goalie_stat_leader(gk_pairs, lambda r: r.sv_pct, maximize=True)
    out.append(
        {
            "abbr": "SV%",
            "player": b[1] if b else None,
            "value": _fmt_save_pct_leader(b[0].sv_pct) if b and b[0].sv_pct is not None else None,
        }
    )
    return out


def _english_ordinal(n: int) -> str:
    n = int(n)
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    m = n % 10
    if m == 1:
        return f"{n}st"
    if m == 2:
        return f"{n}nd"
    if m == 3:
        return f"{n}rd"
    return f"{n}th"


def _normalize_trois_rivieres_spelling(text: str) -> str:
    """Fix FHM/CSV typo: č (U+010D) used instead of è in *Rivières*."""
    if not text:
        return text
    return re.sub(r"(?i)rivič", "riviè", text)


def _hero_city_all_caps(city_or_name: str) -> str:
    """City line on team hero: correct spelling, then ALL CAPS (e.g. TROIS-RIVIÈRES)."""
    return _normalize_trois_rivieres_spelling((city_or_name or "").strip()).upper()


def _dense_rank_by_value(pairs: list[tuple[int, float]], team_id: int, high_good: bool) -> int | None:
    if not pairs:
        return None
    ordered = sorted(pairs, key=lambda x: x[1], reverse=high_good)
    prev_val: float | None = None
    rank = 0
    for idx, (tid, v) in enumerate(ordered, start=1):
        if prev_val is None or abs(v - prev_val) > 1e-9:
            rank = idx
            prev_val = v
        if tid == team_id:
            return rank
    return None


def _standing_gf_g_ga_g_ranks(season_id: int, team_id: int) -> tuple[int | None, int | None]:
    rows = db.session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    rates_gf: list[tuple[int, float]] = []
    rates_ga: list[tuple[int, float]] = []
    for st in rows:
        gpd = st.standing_gp_display()
        if gpd > 0:
            rates_gf.append((st.team_id, float(st.gf) / float(gpd)))
            rates_ga.append((st.team_id, float(st.ga) / float(gpd)))
    return (
        _dense_rank_by_value(rates_gf, team_id, True),
        _dense_rank_by_value(rates_ga, team_id, False),
    )


def _rank_paren(rank: int | None) -> str:
    if rank is None:
        return "—"
    return f"({_english_ordinal(rank)})"


@main_bp.get("/team/<slug>")
def team_page(slug: str):
    team = db.session.scalars(select(Team).where(Team.slug == slug).limit(1)).first()
    if not team:
        abort(404)
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "bowl-fantasy")
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
        arena_name = _normalize_trois_rivieres_spelling(arena_row.arena or "")
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

            div_name_by_pair, div_name_by_id = load_division_display_maps(raw_dir / "divisions.csv")

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
    allowed_team_panels = {
        "roster",
        "depth",
        "ratings",
        "lines",
        "salary",
        "statistics",
        "staff",
        "franchise",
        "season_records",
    }
    if panel not in allowed_team_panels:
        panel = "roster"
    salary_years = [int(season.start_year) + i for i in range(6)] if season and season.start_year else []
    roster = db.session.scalars(
        select(Player).where(Player.current_team_id == team.id).order_by(Player.last_name, Player.first_name)
    ).all()
    age_ref = season_age_reference_date(season)
    roster_ages = {p.id: _player_age_years(p.birth_date, age_ref) for p in roster}

    def _ratings_sort_key(pl: Player) -> tuple[float, str, str]:
        abi = pl.overall_ability
        abi_f = float(abi) if abi is not None else -1.0
        return (-abi_f, (pl.last_name or "").lower(), (pl.first_name or "").lower())

    team_ratings_goalies: list[dict[str, object]] = []
    team_ratings_skaters: list[dict[str, object]] = []
    for pl in sorted([p for p in roster if (p.position or "").strip().upper() == "G"], key=_ratings_sort_key):
        rr = get_player_ratings_row(pl.fhm_player_id)
        team_ratings_goalies.append(
            {
                "player": pl,
                "age": roster_ages.get(pl.id),
                "rr": rr,
            }
        )
    for pl in sorted([p for p in roster if (p.position or "").strip().upper() != "G"], key=_ratings_sort_key):
        rr = get_player_ratings_row(pl.fhm_player_id)
        team_ratings_skaters.append(
            {
                "player": pl,
                "age": roster_ages.get(pl.id),
                "rr": rr,
            }
        )

    staff_coaches, staff_scouts, staff_trainers = get_staff_sections_for_team(team.fhm_team_id)

    franchise_history_sections: list[dict[str, object]] = []
    if panel == "franchise":
        franchise_history_sections = build_franchise_history_sections(team)

    season_records_rs_sections: list[object] = []
    season_records_po_sections: list[object] = []
    if panel == "season_records":
        from app.services.team_season_records import build_team_season_records_bundle

        season_records_rs_sections, season_records_po_sections = build_team_season_records_bundle(
            db.session, team
        )

    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    depth_chart, lines_sections, lines_name_to_id, salary_rows, salary_total = _build_team_lines_views(
        team, roster, season, raw_dir
    )
    team_leader_rows: list[dict[str, object]] = []
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
            "pk_pct": ("pk_pct", True),
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
                    # Kill success %: 100 − (PK GA / SH CH) × 100  ==  (SH CH − PK GA) / SH CH × 100
                    if a.sh_chances and a.sh_chances > 0 and a.pk_goals_against is not None:
                        v = 100.0 - (
                            100.0 * float(a.pk_goals_against) / float(a.sh_chances)
                        )
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
        team_leader_rows = build_team_leader_panel_rows(team, season)
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
    schedule_games: list[Game] = []
    schedule_focus_index = 0
    if season:
        schedule_games = list(
            db.session.scalars(
                select(Game)
                .options(joinedload(Game.home_team), joinedload(Game.away_team))
                .where(
                    Game.season_id == season.id,
                    (Game.home_team_id == team.id) | (Game.away_team_id == team.id),
                )
                .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            ).all()
        )
        schedule_focus_index = 0
        for i, g in enumerate(schedule_games):
            if (g.status or "").lower() != "final":
                schedule_focus_index = i
                break
        else:
            schedule_focus_index = max(0, len(schedule_games) - 1)
    team_mc_panels: dict[str, object] | None = None
    if season and standing:
        st_all = db.session.scalars(select(TeamStanding).where(TeamStanding.season_id == season.id)).all()
        tm_mc: dict[int, Team] = {}
        for st in st_all:
            tt = db.session.get(Team, st.team_id)
            if tt:
                tm_mc[st.team_id] = tt
        team_mc_panels = build_team_page_mc_bundle(db.session, season.id, team.id, tm_mc)
    team_prospects = db.session.scalars(
        select(Prospect).options(joinedload(Prospect.player)).where(Prospect.team_id == team.id)
    ).all()
    hero_city_caps = _hero_city_all_caps(team.city or team.name or "")
    hero_display_name = (team.nickname or team.name or "").strip()
    hero_gf_g: float | None = None
    hero_ga_g: float | None = None
    hero_gf_g_rank_paren = "—"
    hero_ga_g_rank_paren = "—"
    hero_pp_pct: float | None = None
    hero_pk_pct: float | None = None
    hero_pp_rank_paren = "—"
    hero_pk_rank_paren = "—"
    hero_record_suffix = ""
    show_hero_stats_card = bool(standing and season)
    if standing and season:
        gpd = standing.standing_gp_display()
        if gpd > 0:
            hero_gf_g = round(float(standing.gf) / float(gpd), 1)
            hero_ga_g = round(float(standing.ga) / float(gpd), 1)
        rgf, rga = _standing_gf_g_ga_g_ranks(season.id, team.id)
        hero_gf_g_rank_paren = _rank_paren(rgf)
        hero_ga_g_rank_paren = _rank_paren(rga)
    if team_agg:
        if team_agg.pp_chances and team_agg.pp_goals is not None and team_agg.pp_chances > 0:
            hero_pp_pct = round(100.0 * float(team_agg.pp_goals) / float(team_agg.pp_chances), 1)
        if team_agg.sh_chances and team_agg.sh_chances > 0 and team_agg.pk_goals_against is not None:
            hero_pk_pct = round(
                100.0 - (100.0 * float(team_agg.pk_goals_against) / float(team_agg.sh_chances)),
                1,
            )
    hero_pp_rank_paren = _rank_paren(team_rank_rs.get("pp_pct") if team_rank_rs else None)
    hero_pk_rank_paren = _rank_paren(team_rank_rs.get("pk_pct") if team_rank_rs else None)
    if standing and season:
        if division_rank is not None and division_name:
            hero_record_suffix = f" ({_english_ordinal(division_rank)} {division_name})"
        elif division_name:
            hero_record_suffix = f" ({division_name})"
    tmpl_kwargs: dict[str, object] = {
        "team": team,
        "arena_name": arena_name,
        "arena_capacity": arena_capacity,
        "division_name": division_name,
        "division_rank": division_rank,
        "season": season,
        "standing": standing,
        "hero_city_caps": hero_city_caps,
        "hero_display_name": hero_display_name,
        "hero_gf_g": hero_gf_g,
        "hero_ga_g": hero_ga_g,
        "hero_gf_g_rank_paren": hero_gf_g_rank_paren,
        "hero_ga_g_rank_paren": hero_ga_g_rank_paren,
        "hero_pp_pct": hero_pp_pct,
        "hero_pk_pct": hero_pk_pct,
        "hero_pp_rank_paren": hero_pp_rank_paren,
        "hero_pk_rank_paren": hero_pk_rank_paren,
        "hero_record_suffix": hero_record_suffix,
        "show_hero_stats_card": show_hero_stats_card,
        "roster": roster,
        "roster_ages": roster_ages,
        "team_leader_rows": team_leader_rows,
        "schedule_games": schedule_games,
        "schedule_focus_index": schedule_focus_index,
        "team_mc_panels": team_mc_panels,
        "prospects_list": team_prospects,
        "team_agg": team_agg,
        "team_agg_po": team_agg_po,
        "team_rank_rs": team_rank_rs,
        "team_rank_po": team_rank_po,
        "active_panel": panel,
        "depth_chart": depth_chart,
        "lines_sections": lines_sections,
        "lines_name_to_id": lines_name_to_id,
        "salary_rows": salary_rows,
        "salary_total": salary_total,
        "salary_years": salary_years,
        "team_ratings_goalies": team_ratings_goalies,
        "team_ratings_skaters": team_ratings_skaters,
        "staff_coaches": staff_coaches,
        "staff_scouts": staff_scouts,
        "staff_trainers": staff_trainers,
        "staff_coach_columns": STAFF_COACH_COLUMNS,
        "staff_scout_columns": STAFF_SCOUT_COLUMNS,
        "staff_trainer_columns": STAFF_TRAINER_COLUMNS,
        "franchise_history_sections": franchise_history_sections,
        "season_records_rs_sections": season_records_rs_sections,
        "season_records_po_sections": season_records_po_sections,
    }
    if panel == "statistics":
        tmpl_kwargs.update(
            _build_statistics_view_vars(locked_team_id=team.id, locked_team_slug=team.slug)
        )
    return render_template("team.html", **tmpl_kwargs)


def _player_age_years(birth: date | None, as_of: date | None = None) -> int | None:
    if birth is None:
        return None
    ref = as_of if as_of is not None else date.today()
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


def _dedupe_goalie_playoff_career_lines(
    lines: list[PlayerGoalieCareerLine],
) -> list[PlayerGoalieCareerLine]:
    """One playoffs row per (season_year, team_fhm_id, league_fhm_id).

    FHM career imports include both *ps* and *po* files (and active vs *retired_po*), which
    produced duplicate seasons on the player page. Skater playoffs use only *po* /
    *retired_po*; we match that and, if both *po* and *retired_po* exist for the same key,
    keep *po* (season CSV) over *retired_po*.
    """
    if not lines:
        return []
    rank = {"po": 0, "retired_po": 1}
    ordered = sorted(
        lines,
        key=lambda ln: (
            -ln.season_year,
            ln.team_fhm_id,
            ln.league_fhm_id,
            rank.get(ln.career_source, 9),
        ),
    )
    out: list[PlayerGoalieCareerLine] = []
    seen: set[tuple[int, int, int]] = set()
    for ln in ordered:
        key = (ln.season_year, ln.team_fhm_id, ln.league_fhm_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


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
    career_po_gk = _dedupe_goalie_playoff_career_lines(
        [ln for ln in gk_career_lines if ln.career_source in ("po", "retired_po")]
    )
    pos = (player.position or "").strip().upper()
    is_goalie = pos.startswith("G")
    has_sk_career = bool(career_rs_sk or career_po_sk)
    has_gk_career = bool(career_rs_gk or career_po_gk)
    # Career tables: show goalie panels if position is G or goalie CSV rows exist (FHM sometimes mislabels G as C/D).
    show_goalie_career_sections = is_goalie or has_gk_career
    show_skater_career_sections = has_sk_career or (not is_goalie and not has_gk_career)
    # Game log: prefer skater box scores when we have skater career data; else goalie if position G or only goalie career.
    use_goalie_game_log = is_goalie or (has_gk_career and not has_sk_career)
    if player.retired:
        game_log = []
    elif use_goalie_game_log:
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
    contract_through_season = contract_final_season_label_from_remaining(
        contract_years_left, season_start_year
    )
    roster_header_team = main_league_roster_team(contract_team, current_team)
    player_award_badges = player_history_award_badges(db.session, player.id)
    bowl_league_ids = bowl_nhl_league_ids(db.session)
    career_rs_sk_bowl = [ln for ln in career_rs_sk if ln.league_fhm_id in bowl_league_ids]
    career_po_sk_bowl = [ln for ln in career_po_sk if ln.league_fhm_id in bowl_league_ids]
    career_rs_gk_bowl = [ln for ln in career_rs_gk if ln.league_fhm_id in bowl_league_ids]
    career_po_gk_bowl = [ln for ln in career_po_gk if ln.league_fhm_id in bowl_league_ids]
    career_rs_sk_totals = skater_career_lines_totals(career_rs_sk_bowl) if career_rs_sk_bowl else None
    career_po_sk_totals = skater_career_lines_totals(career_po_sk_bowl) if career_po_sk_bowl else None
    career_rs_gk_totals = goalie_career_lines_totals(career_rs_gk_bowl) if career_rs_gk_bowl else None
    career_po_gk_totals = goalie_career_lines_totals(career_po_gk_bowl) if career_po_gk_bowl else None
    return render_template(
        "player.html",
        player=player,
        season=season,
        career_rs_sk=career_rs_sk,
        career_po_sk=career_po_sk,
        career_rs_gk=career_rs_gk,
        career_po_gk=career_po_gk,
        career_rs_sk_totals=career_rs_sk_totals,
        career_po_sk_totals=career_po_sk_totals,
        career_rs_gk_totals=career_rs_gk_totals,
        career_po_gk_totals=career_po_gk_totals,
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
        show_goalie_career_sections=show_goalie_career_sections,
        show_skater_career_sections=show_skater_career_sections,
        use_goalie_game_log=use_goalie_game_log,
        rating_avgs_skater=rating_avgs_skater,
        rating_avgs_goalie=rating_avgs_goalie,
        contract_years_left=contract_years_left,
        contract_through_season=contract_through_season,
        player_award_badges=player_award_badges,
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

"""Orchestrate CSV imports in dependency order."""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Allow running as script from repo root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, or_, select  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config, league_by_slug, make_league_config  # noqa: E402
from app.models import (  # noqa: E402
    Draft,
    DraftPick,
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAward,
    HistoryChampion,
    ImportLog,
    PenaltyEvent,
    Player,
    PlayerGoalieStat,
    PlayerSkaterStat,
    Prospect,
    ScoringEvent,
    Season,
    Team,
    TeamStanding,
    db,
)
from app.services.player_headshot import canonical_player_headshot_basename  # noqa: E402
from app.services.rebuild import refresh_after_import  # noqa: E402
from scripts.import_pipeline.encoding_utils import (  # noqa: E402
    cell_val,
    read_csv_normalized,
    to_bool,
    to_float,
    to_int,
)

log = logging.getLogger("bowl.import")


def _sync_team_logos_from_raw(raw_dir: Path, app) -> int:
    """Copy team logo images from optional raw subfolders into this league's static team folder."""
    dest = Path(app.config["TEAM_LOGOS_DIR"])
    dest.mkdir(parents=True, exist_ok=True)
    exts = {".png", ".webp", ".jpg", ".jpeg", ".svg"}
    n = 0
    for sub in ("team_logos", "logos", "Team Logos"):
        src = raw_dir / sub
        if not src.is_dir():
            continue
        for f in src.iterdir():
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            try:
                shutil.copy2(f, dest / f.name)
                n += 1
            except OSError as exc:
                log.warning("Could not copy logo %s: %s", f.name, exc)
        if n:
            log.info("Copied %s logo file(s) from %s -> %s", n, src, dest)
            return n
    return 0


def _run_post_import_safeguards() -> None:
    """Run regression checks that protect UI data integrity after imports."""
    mod_name = "tests.test_depth_chart_org_guard"
    if importlib.util.find_spec(mod_name) is None:
        log.warning(
            "Skipping post-import safeguard %s (module not found). "
            "Deploy the `tests/` package with your app if you want this check on the server.",
            mod_name,
        )
        return
    suite = unittest.defaultTestLoader.loadTestsFromName(mod_name)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError(f"Post-import safeguard failed: {mod_name}")


def _slug(name: str, abbrev: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in (abbrev or name).lower()).strip("-")
    return base or "team"


def import_teams(raw_dir: Path, app) -> int:
    path = raw_dir / "teams.csv"
    if not path.exists():
        log.warning("Skipping teams.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    required = {"name", "abbreviation"}
    cols = set(df.columns)
    if not required.issubset(cols):
        log.error("teams.csv missing columns: %s", required - cols)
        return 0
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        name = cell_val(r, "name", "team_name")
        abbrev = cell_val(r, "abbreviation", "abbr", "team_abbr")
        if not name or not abbrev:
            continue
        fhm = cell_val(r, "fhm_team_id", "team_id", "id")
        slug = cell_val(r, "slug") or _slug(name, abbrev)
        if fhm:
            existing = db.session.scalars(
                select(Team).where(
                    or_(Team.fhm_team_id == fhm, Team.slug == slug, Team.abbreviation == abbrev)
                ).limit(1)
            ).first()
        else:
            existing = db.session.scalars(
                select(Team).where(or_(Team.slug == slug, Team.abbreviation == abbrev)).limit(1)
            ).first()
        if existing:
            t = existing
        else:
            t = Team(slug=slug, abbreviation=abbrev[:8], name=name)
            db.session.add(t)
        t.name = name
        t.abbreviation = abbrev[:8]
        t.slug = slug
        t.city = cell_val(r, "city")
        t.nickname = cell_val(r, "nickname", "nick")
        t.fhm_team_id = fhm
        t.logo_path = cell_val(r, "logo_path", "logo")
        t.primary_color = cell_val(r, "primary_color", "primary")
        t.secondary_color = cell_val(r, "secondary_color", "secondary")
        n += 1
    db.session.commit()
    return n


def import_seasons(raw_dir: Path, app) -> int:
    path = raw_dir / "seasons.csv"
    if not path.exists():
        log.warning("Skipping seasons.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        label = cell_val(r, "label", "season", "name")
        if not label:
            continue
        fhm = cell_val(r, "fhm_season_id", "season_id", "id")
        existing = None
        if fhm:
            existing = db.session.scalars(
                select(Season).where(Season.fhm_season_id == fhm).limit(1)
            ).first()
        if not existing:
            existing = db.session.scalars(select(Season).where(Season.label == label).limit(1)).first()
        if existing:
            s = existing
        else:
            s = Season(label=label)
            db.session.add(s)
        s.label = label
        s.fhm_season_id = fhm
        s.start_year = to_int(cell_val(r, "start_year"))
        s.end_year = to_int(cell_val(r, "end_year"))
        s.is_current = to_bool(cell_val(r, "is_current", "current"))
        n += 1
    db.session.commit()
    return n


def _team_by_fhm_or_abbr(key: str | None) -> Team | None:
    if not key:
        return None
    t = db.session.scalars(select(Team).where(Team.fhm_team_id == key).limit(1)).first()
    if t:
        return t
    return db.session.scalars(select(Team).where(Team.abbreviation == key).limit(1)).first()


def _season_by_fhm_or_label(key: str | None) -> Season | None:
    if not key:
        return None
    s = db.session.scalars(select(Season).where(Season.fhm_season_id == key).limit(1)).first()
    if s:
        return s
    return db.session.scalars(select(Season).where(Season.label == key).limit(1)).first()


def _season_for_player_aggregate_import(raw_key: str | None) -> Season | None:
    """Resolve CSV season cell for ``player_*_stats.csv`` rows.

    Mirrors ``import_team_standings``: use ``get_current_season()`` when the cell is
    absent or does not match a row, and prefer the **current** sim season when the CSV
    still points at a former ``Season`` row but the site has advanced (``is_current`` /
    later ``start_year``).
    """
    from app.services.seasons import get_current_season

    key = (raw_key or "").strip()
    resolved = _season_by_fhm_or_label(key) if key else None
    cur = get_current_season()
    if not resolved:
        return cur
    if not cur:
        return resolved
    if bool(cur.is_current) and not bool(resolved.is_current):
        return cur
    rs, cs = resolved.start_year, cur.start_year
    if rs is not None and cs is not None and cs > rs:
        return cur
    return resolved


def import_players(raw_dir: Path, app) -> int:
    path = raw_dir / "players.csv"
    if not path.exists():
        log.warning("Skipping players.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        fn = cell_val(r, "first_name", "firstname", "fname")
        ln = cell_val(r, "last_name", "lastname", "lname")
        full = cell_val(r, "full_name", "name", "player_name")
        if not full and fn and ln:
            full = f"{fn} {ln}"
        if not full:
            continue
        if not fn:
            parts = full.split()
            fn = parts[0] if parts else "?"
            ln = " ".join(parts[1:]) if len(parts) > 1 else "?"
        if not ln:
            ln = "?"
        fhm = cell_val(r, "fhm_player_id", "player_id", "id")
        existing = None
        if fhm:
            existing = db.session.scalars(
                select(Player).where(Player.fhm_player_id == fhm).limit(1)
            ).first()
        if not existing:
            existing = db.session.scalars(
                select(Player).where(Player.full_name == full).limit(1)
            ).first()
        tid_key = cell_val(r, "current_team_id", "team_id", "team")
        team = _team_by_fhm_or_abbr(tid_key)
        if existing:
            p = existing
        else:
            p = Player(first_name=fn or "?", last_name=ln or "?", full_name=full)
            db.session.add(p)
        p.first_name = fn or p.first_name
        p.last_name = ln or p.last_name
        p.full_name = full
        p.fhm_player_id = fhm
        p.position = cell_val(r, "position", "pos")
        p.shoots_catches = cell_val(r, "shoots_catches", "shoots", "catches")
        p.nationality = cell_val(r, "nationality", "nation")
        bd = cell_val(r, "birth_date", "dob", "born")
        if bd:
            try:
                from datetime import date as ddate

                p.birth_date = ddate.fromisoformat(bd[:10])
            except ValueError:
                pass
        p.status = cell_val(r, "status")
        p.current_team_id = team.id if team else None
        raw_hs = cell_val(r, "headshot_path", "headshot")
        p.headshot_path = raw_hs
        if not (p.headshot_path or "").strip() and p.birth_date:
            p.headshot_path = canonical_player_headshot_basename(p)
        raw_j = cell_val(r, "jersey_number", "jersey", "jersey_no", "number")
        if raw_j is not None and str(raw_j).strip() != "":
            jn = to_int(raw_j)
            if jn is not None:
                p.jersey_number = jn
        n += 1
    db.session.commit()
    return n


def import_team_standings(raw_dir: Path, app) -> int:
    path = raw_dir / "team_standings.csv"
    if not path.exists():
        log.warning("Skipping team_standings.csv (not found)")
        return 0
    from app.services.seasons import get_current_season

    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        sk = cell_val(r, "season_id", "season", "fhm_season_id")
        season = _season_by_fhm_or_label(sk) if sk else None
        if not season:
            season = get_current_season()
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr", "abbreviation"))
        if not team:
            slug = cell_val(r, "slug")
            if slug:
                team = db.session.scalars(select(Team).where(Team.slug == slug).limit(1)).first()
        if not team:
            tn = cell_val(r, "team_name", "name", "team")
            if tn:
                team = db.session.scalars(select(Team).where(Team.name == tn).limit(1)).first()
        if not season or not team:
            continue
        st = db.session.scalars(
            select(TeamStanding).where(
                TeamStanding.season_id == season.id, TeamStanding.team_id == team.id
            ).limit(1)
        ).first()
        if not st:
            st = TeamStanding(season_id=season.id, team_id=team.id)
            db.session.add(st)
        st.gp = to_int(cell_val(r, "gp", "games_played"), 0) or 0
        st.w = to_int(cell_val(r, "w", "wins"), 0) or 0
        st.l = to_int(cell_val(r, "l", "losses"), 0) or 0
        st.ties = to_int(cell_val(r, "t", "ties", "tie"), 0) or 0
        st.otl = to_int(cell_val(r, "otl", "ot_losses"), 0) or 0
        st.shootout_wins = to_int(cell_val(r, "sow", "shootout_wins"), 0) or 0
        st.shootout_losses = to_int(cell_val(r, "sol", "shootout_losses"), 0) or 0
        st.pts = to_int(cell_val(r, "pts", "points"), 0) or 0
        st.gf = to_int(cell_val(r, "gf", "goals_for"), 0) or 0
        st.ga = to_int(cell_val(r, "ga", "goals_against"), 0) or 0
        st.streak = cell_val(r, "streak")
        st.conference = cell_val(r, "conference")
        st.division = cell_val(r, "division")
        pct = to_float(cell_val(r, "win_pct", "pct"))
        if pct is None and st.gp:
            pct = st.pts / (2.0 * st.gp)
        st.win_pct = pct
        n += 1
    db.session.commit()
    return n


def import_games(raw_dir: Path, app) -> int:
    path = raw_dir / "games.csv"
    if not path.exists():
        log.warning("Skipping games.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        season = _season_by_fhm_or_label(cell_val(r, "season_id", "season"))
        home = _team_by_fhm_or_abbr(cell_val(r, "home_team_id", "home", "home_abbr"))
        away = _team_by_fhm_or_abbr(cell_val(r, "away_team_id", "away", "away_abbr"))
        if not season or not home or not away:
            continue
        fhm = cell_val(r, "fhm_game_id", "game_id", "id")
        existing = (
            db.session.scalars(select(Game).where(Game.fhm_game_id == fhm).limit(1)).first()
            if fhm
            else None
        )
        if not existing:
            g = Game(
                season_id=season.id,
                home_team_id=home.id,
                away_team_id=away.id,
            )
            db.session.add(g)
        else:
            g = existing
        g.fhm_game_id = fhm
        g.season_id = season.id
        g.home_team_id = home.id
        g.away_team_id = away.id
        gd = cell_val(r, "game_date", "date")
        if gd:
            try:
                from datetime import date as ddate

                g.game_date = ddate.fromisoformat(gd[:10])
            except ValueError:
                pass
        g.home_score = to_int(cell_val(r, "home_score", "home_goals"))
        g.away_score = to_int(cell_val(r, "away_score", "away_goals"))
        g.status = (cell_val(r, "status") or "final").lower()
        g.went_to_overtime = to_bool(cell_val(r, "went_to_overtime", "ot", "overtime"))
        g.went_to_shootout = to_bool(cell_val(r, "went_to_shootout", "so", "shootout"))
        g.home_shots = to_int(cell_val(r, "home_shots"))
        g.away_shots = to_int(cell_val(r, "away_shots"))
        n += 1
    db.session.commit()
    return n


def _player_by_fhm(key: str | None) -> Player | None:
    if not key:
        return None
    return db.session.scalars(select(Player).where(Player.fhm_player_id == key).limit(1)).first()


def import_skater_stats(raw_dir: Path, app) -> int:
    path = raw_dir / "player_skater_stats.csv"
    if not path.exists():
        log.warning("Skipping player_skater_stats.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        season = _season_for_player_aggregate_import(cell_val(r, "season_id", "season"))
        player = _player_by_fhm(cell_val(r, "player_id", "fhm_player_id"))
        if not season or not player:
            continue
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        row_db = db.session.scalars(
            select(PlayerSkaterStat).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.player_id == player.id,
                PlayerSkaterStat.stat_segment == "rs",
            ).limit(1)
        ).first()
        if not row_db:
            row_db = PlayerSkaterStat(
                season_id=season.id, player_id=player.id, stat_segment="rs"
            )
            db.session.add(row_db)
        row_db.team_id = team.id if team else None
        row_db.gp = to_int(cell_val(r, "gp"), 0) or 0
        row_db.goals = to_int(cell_val(r, "goals", "g"), 0) or 0
        row_db.assists = to_int(cell_val(r, "assists", "a"), 0) or 0
        row_db.points = to_int(cell_val(r, "points", "pts"), 0) or 0
        row_db.pim = to_int(cell_val(r, "pim", "penalty_minutes"), 0) or 0
        row_db.plus_minus = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
        row_db.shots = to_int(cell_val(r, "shots", "sog"))
        row_db.ppg = to_int(cell_val(r, "ppg", "pp_goals"))
        row_db.shg = to_int(cell_val(r, "shg", "sh_goals"))
        row_db.gwg = to_int(cell_val(r, "gwg"))
        n += 1
    db.session.commit()
    return n


def import_goalie_stats(raw_dir: Path, app) -> int:
    path = raw_dir / "player_goalie_stats.csv"
    if not path.exists():
        log.warning("Skipping player_goalie_stats.csv (not found)")
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        season = _season_for_player_aggregate_import(cell_val(r, "season_id", "season"))
        player = _player_by_fhm(cell_val(r, "player_id", "fhm_player_id"))
        if not season or not player:
            continue
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        row_db = db.session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.player_id == player.id,
                PlayerGoalieStat.stat_segment == "rs",
            ).limit(1)
        ).first()
        if not row_db:
            row_db = PlayerGoalieStat(
                season_id=season.id, player_id=player.id, stat_segment="rs"
            )
            db.session.add(row_db)
        row_db.team_id = team.id if team else None
        row_db.gp = to_int(cell_val(r, "gp"), 0) or 0
        row_db.wins = to_int(cell_val(r, "wins", "w"), 0) or 0
        row_db.losses = to_int(cell_val(r, "losses", "l"), 0) or 0
        row_db.otl = to_int(cell_val(r, "otl"), 0) or 0
        row_db.ga = to_int(cell_val(r, "ga", "goals_against"), 0) or 0
        row_db.sa = to_int(cell_val(r, "sa", "shots_against", "saves"), 0) or 0
        row_db.so = to_int(cell_val(r, "so", "shutouts"), 0) or 0
        row_db.gaa = to_float(cell_val(r, "gaa"))
        row_db.sv_pct = to_float(cell_val(r, "sv_pct", "save_pct"))
        row_db.gsaa = to_float(
            cell_val(r, "gsaa", "goals_saved_above_average", "goals_saved_above_avg")
        )
        n += 1
    db.session.commit()
    return n


def _game_by_fhm(key: str | None) -> Game | None:
    if not key:
        return None
    return db.session.scalars(select(Game).where(Game.fhm_game_id == key).limit(1)).first()


def import_game_skater_stats(raw_dir: Path, app) -> int:
    path = raw_dir / "game_skater_stats.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        game = _game_by_fhm(cell_val(r, "game_id", "fhm_game_id"))
        player = _player_by_fhm(cell_val(r, "player_id"))
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        if not game or not player or not team:
            continue
        row_db = db.session.scalars(
            select(GameSkaterStat).where(
                GameSkaterStat.game_id == game.id, GameSkaterStat.player_id == player.id
            ).limit(1)
        ).first()
        if not row_db:
            row_db = GameSkaterStat(game_id=game.id, player_id=player.id, team_id=team.id)
            db.session.add(row_db)
        row_db.goals = to_int(cell_val(r, "goals", "g"), 0) or 0
        row_db.assists = to_int(cell_val(r, "assists", "a"), 0) or 0
        row_db.shots = to_int(cell_val(r, "shots"), 0) or 0
        row_db.pim = to_int(cell_val(r, "pim"), 0) or 0
        row_db.toi_seconds = to_int(cell_val(r, "toi_seconds", "toi"))
        row_db.plus_minus = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
        n += 1
    db.session.commit()
    return n


def import_game_goalie_stats(raw_dir: Path, app) -> int:
    path = raw_dir / "game_goalie_stats.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        game = _game_by_fhm(cell_val(r, "game_id", "fhm_game_id"))
        player = _player_by_fhm(cell_val(r, "player_id"))
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        if not game or not player or not team:
            continue
        row_db = db.session.scalars(
            select(GameGoalieStat).where(
                GameGoalieStat.game_id == game.id, GameGoalieStat.player_id == player.id
            ).limit(1)
        ).first()
        if not row_db:
            row_db = GameGoalieStat(game_id=game.id, player_id=player.id, team_id=team.id)
            db.session.add(row_db)
        row_db.saves = to_int(cell_val(r, "saves"), 0) or 0
        row_db.shots_against = to_int(cell_val(r, "shots_against", "sa"), 0) or 0
        row_db.goals_allowed = to_int(cell_val(r, "goals_allowed", "ga"), 0) or 0
        row_db.decision = cell_val(r, "decision", "result")
        n += 1
    db.session.commit()
    return n


def import_scoring_events(raw_dir: Path, app) -> int:
    path = raw_dir / "scoring_events.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        game = _game_by_fhm(cell_val(r, "game_id", "fhm_game_id"))
        if not game:
            continue
        ev = ScoringEvent(
            game_id=game.id,
            period=to_int(cell_val(r, "period"), 1) or 1,
            time_elapsed=cell_val(r, "time_elapsed", "time"),
            scorer_player_id=_player_by_fhm(cell_val(r, "scorer_player_id", "scorer_id")).id
            if _player_by_fhm(cell_val(r, "scorer_player_id", "scorer_id"))
            else None,
            assist1_player_id=_player_by_fhm(cell_val(r, "assist1_player_id", "assist1_id")).id
            if _player_by_fhm(cell_val(r, "assist1_player_id", "assist1_id"))
            else None,
            assist2_player_id=_player_by_fhm(cell_val(r, "assist2_player_id", "assist2_id")).id
            if _player_by_fhm(cell_val(r, "assist2_player_id", "assist2_id"))
            else None,
            scoring_team_id=_team_by_fhm_or_abbr(cell_val(r, "scoring_team_id", "team_abbr")).id
            if _team_by_fhm_or_abbr(cell_val(r, "scoring_team_id", "team_abbr"))
            else None,
            strength=cell_val(r, "strength", "pp"),
        )
        db.session.add(ev)
        n += 1
    db.session.commit()
    return n


def import_penalty_events(raw_dir: Path, app) -> int:
    path = raw_dir / "penalty_events.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        game = _game_by_fhm(cell_val(r, "game_id", "fhm_game_id"))
        if not game:
            continue
        pl = _player_by_fhm(cell_val(r, "player_id"))
        tm = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        ev = PenaltyEvent(
            game_id=game.id,
            period=to_int(cell_val(r, "period"), 1) or 1,
            time_elapsed=cell_val(r, "time_elapsed", "time"),
            player_id=pl.id if pl else None,
            team_id=tm.id if tm else None,
            minutes=to_int(cell_val(r, "minutes", "pim")),
            infraction=cell_val(r, "infraction", "penalty"),
        )
        db.session.add(ev)
        n += 1
    db.session.commit()
    return n


def import_prospects(raw_dir: Path, app) -> int:
    path = raw_dir / "prospects.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        fhm = cell_val(r, "fhm_prospect_id", "id")
        player = _player_by_fhm(cell_val(r, "player_id"))
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        pr = Prospect(
            fhm_prospect_id=fhm,
            player_id=player.id if player else None,
            team_id=team.id if team else None,
            rank=to_int(cell_val(r, "rank")),
            tier=cell_val(r, "tier"),
            notes=cell_val(r, "notes"),
        )
        db.session.add(pr)
        n += 1
    db.session.commit()
    return n


def import_drafts(raw_dir: Path, app) -> int:
    path = raw_dir / "drafts.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        label = cell_val(r, "label", "name") or "Draft"
        season = _season_by_fhm_or_label(cell_val(r, "season_id", "season"))
        d = Draft(label=label, year=to_int(cell_val(r, "year")), season_id=season.id if season else None)
        db.session.add(d)
        db.session.flush()
        n += 1
    db.session.commit()
    return n


def import_draft_picks(raw_dir: Path, app) -> int:
    path = raw_dir / "draft_picks.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        draft_label = cell_val(r, "draft_label", "draft")
        draft_id = to_int(cell_val(r, "draft_id"))
        draft = None
        if draft_id:
            draft = db.session.get(Draft, draft_id)
        if not draft and draft_label:
            draft = db.session.scalars(
                select(Draft).where(Draft.label == draft_label).limit(1)
            ).first()
        if not draft:
            draft = db.session.scalars(select(Draft).order_by(Draft.id.desc()).limit(1)).first()
        if not draft:
            continue
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        player = _player_by_fhm(cell_val(r, "player_id"))
        pick = DraftPick(
            draft_id=draft.id,
            overall_pick=to_int(cell_val(r, "overall_pick", "pick"), 0) or 0,
            round=to_int(cell_val(r, "round")),
            team_id=team.id if team else None,
            player_id=player.id if player else None,
        )
        db.session.add(pick)
        n += 1
    db.session.commit()
    return n


def _history_awards_csv_path(raw_dir: Path) -> Path | None:
    """Pick a history-awards CSV (same columns).

    ``history_awards.sheet.csv`` is preferred (wide-sheet output + continuation rows). When you
    maintain ``history_awards.csv`` by hand, run
    ``python scripts/align_history_awards_to_player_master.py --raw-dir <league>/raw`` first:
    it merges manual CSV cells into the sheet file and cross-references ``player_master.csv``.
    """
    for name in ("history_awards.sheet.csv", "history_awards.csv", "awards_history.csv"):
        p = raw_dir / name
        if p.is_file():
            return p
    return None


def import_history_awards(
    raw_dir: Path,
    app,
    *,
    csv_path: Path | None = None,
    replace_award_substring: str | None = None,
    replace_all: bool = False,
) -> int:
    """Import ``history_awards.csv`` rows.

    If ``replace_all`` is True, delete **all** ``HistoryAward`` rows, then import every
    CSV row (full re-import). Do not combine with ``replace_award_substring``.

    If ``replace_award_substring`` is set (e.g. ``\"JENNINGS\"``), delete existing
    ``HistoryAward`` rows whose ``award_name`` matches (case-insensitive substring),
    then import **only** CSV rows whose award name contains that substring. All other
    awards are left unchanged.
    """
    path = csv_path.resolve() if csv_path else _history_awards_csv_path(raw_dir)
    if path is None or not path.is_file():
        return 0
    log.info("Loading history awards from %s", path.name)
    df = read_csv_normalized(path)
    needle = (replace_award_substring or "").strip()
    if replace_all:
        if needle:
            raise ValueError("replace_all cannot be combined with replace_award_substring.")
        db.session.execute(delete(HistoryAward))
        db.session.commit()
        log.info("Removed all history_awards rows before full CSV re-import.")
    elif needle:
        sub = needle.upper()
        db.session.execute(delete(HistoryAward).where(HistoryAward.award_name.ilike(f"%{needle}%")))
        db.session.commit()
        log.info("Removed existing history awards matching %r before partial re-import.", needle)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        award_cell = (cell_val(r, "award_name", "award") or "").strip()
        if needle and sub not in award_cell.upper():
            continue
        season_key = cell_val(r, "season_id", "season")
        season = _season_by_fhm_or_label(season_key)
        sheet_pref: list[str] = []
        if not season:
            # Fictional trophy years (e.g. 1979-80) often do not match a single imported FHM season row.
            # When exactly one season exists in the DB, attach awards to it and preserve the sheet label in notes.
            all_seasons = db.session.scalars(select(Season)).all()
            if len(all_seasons) == 1:
                season = all_seasons[0]
                if season_key:
                    sheet_pref.append(f"sheet_season={season_key}")
        if not season:
            continue
        # Same key as ``player_master.csv`` ``PlayerId`` (stored as ``Player.fhm_player_id``), not DB ``players.id``.
        pid_raw = cell_val(r, "player_id", "fhm_player_id", "playerid")
        pid_st = (pid_raw or "").strip()
        pid_lower = pid_st.lower()
        # Spreadsheets often export absent IDs as the literal ``null`` / ``none`` / ``nan``.
        sheet_player_null = pid_lower in ("null", "none", "nan")
        player_key = None if not pid_st or sheet_player_null else pid_st
        player = _player_by_fhm(player_key)
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        staff_fhm_raw = cell_val(r, "staff_id", "staff_fhm_id", "fhm_staff_id")
        staff_fhm_id = (staff_fhm_raw or "").strip() or None
        notes_val = cell_val(r, "notes")
        if sheet_player_null:
            tag = "no_winner=1"
            nv_low = (notes_val or "").lower()
            if "no_winner=1" not in nv_low:
                notes_val = f"{notes_val}; {tag}" if notes_val else tag
        if sheet_pref:
            merged = "; ".join(sheet_pref)
            if notes_val:
                merged = f"{merged}; {notes_val}"
            notes_val = merged
        a = HistoryAward(
            season_id=season.id,
            award_name=cell_val(r, "award_name", "award") or "Award",
            player_id=player.id if player else None,
            team_id=team.id if team else None,
            staff_fhm_id=staff_fhm_id,
            notes=notes_val,
        )
        db.session.add(a)
        n += 1
    db.session.commit()
    return n


def import_history_champions(raw_dir: Path, app) -> int:
    path = raw_dir / "history_champions.csv"
    if not path.exists():
        return 0
    df = read_csv_normalized(path)
    n = 0
    for _, row in df.iterrows():
        r = row.to_dict()
        season = _season_by_fhm_or_label(cell_val(r, "season_id", "season"))
        team = _team_by_fhm_or_abbr(cell_val(r, "team_id", "team_abbr"))
        if not season or not team:
            continue
        c = HistoryChampion(
            season_id=season.id,
            team_id=team.id,
            trophy=cell_val(r, "trophy", "title"),
        )
        db.session.add(c)
        n += 1
    db.session.commit()
    return n


STEPS = [
    ("teams", import_teams),
    ("seasons", import_seasons),
    ("players", import_players),
    ("team_standings", import_team_standings),
    ("games", import_games),
    ("player_skater_stats", import_skater_stats),
    ("player_goalie_stats", import_goalie_stats),
    ("game_skater_stats", import_game_skater_stats),
    ("game_goalie_stats", import_game_goalie_stats),
    ("scoring_events", import_scoring_events),
    ("penalty_events", import_penalty_events),
    ("prospects", import_prospects),
    ("drafts", import_drafts),
    ("draft_picks", import_draft_picks),
    ("history_awards", import_history_awards),
    ("history_champions", import_history_champions),
]


def run_import(raw_dir: Path | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    slug = (os.environ.get("LEAGUE_SLUG") or "bowl-fantasy").strip()
    if league_by_slug(slug):
        app = create_app(make_league_config(slug))
        log.info("Import using league slug %r (DB + static paths match mounted app).", slug)
    else:
        log.warning("LEAGUE_SLUG %r is not in LEAGUES registry; using default Config.", slug)
        app = create_app(Config)
    raw = Path(raw_dir or app.config["RAW_IMPORT_DIR"])
    if not raw.is_dir():
        log.error("Raw import directory does not exist: %s", raw)
        return
    _sync_team_logos_from_raw(raw, app)
    with app.app_context():
        from scripts.import_pipeline.fhm_loader import is_fhm_export_dir, run_fhm_import

        if is_fhm_export_dir(raw):
            log.info("Detected FHM export layout (team_data.csv, semicolon CSVs).")
            ilog = ImportLog(
                file_name="fhm_export_bundle",
                started_at=datetime.utcnow(),
                status="started",
            )
            db.session.add(ilog)
            db.session.commit()
            counts: dict = {}
            try:
                counts = run_fhm_import(raw, app, league_filter=0)
                overlay = raw / "team_standings.csv"
                if overlay.is_file():
                    log.info("Applying team_standings.csv overlay after FHM import.")
                    counts["team_standings_overlay"] = import_team_standings(raw, app)
                if _history_awards_csv_path(raw) is not None:
                    counts["history_awards"] = import_history_awards(raw, app)
                hc = raw / "history_champions.csv"
                if hc.is_file():
                    log.info("Applying history_champions.csv after FHM import.")
                    counts["history_champions"] = import_history_champions(raw, app)
                total = sum(counts.values())
                ilog.rows_processed = total
                ilog.status = "success"
                ilog.message = str(counts)
            except Exception as e:
                log.exception("FHM import failed")
                ilog.status = "error"
                ilog.message = str(e)
            ilog.finished_at = datetime.utcnow()
            db.session.commit()
            refresh_after_import(db.engine)
            _run_post_import_safeguards()
            log.info("FHM import finished. Counts: %s", counts if ilog.status == "success" else {})
            return

        total = 0
        for name, fn in STEPS:
            ilog = ImportLog(file_name=f"{name}.csv", started_at=datetime.utcnow(), status="started")
            db.session.add(ilog)
            db.session.commit()
            try:
                count = fn(raw, app)
                total += count
                ilog.rows_processed = count
                ilog.status = "success"
                ilog.message = f"{count} rows"
            except Exception as e:
                log.exception("Import step %s failed", name)
                ilog.status = "error"
                ilog.message = str(e)
            ilog.finished_at = datetime.utcnow()
            db.session.commit()
        refresh_after_import(db.engine)
        _run_post_import_safeguards()
        log.info("Import finished. Total row operations (approx): %s", total)


if __name__ == "__main__":
    run_import()

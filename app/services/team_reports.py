"""League-wide team rating averages from active skater rosters + player_ratings.csv."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app, has_request_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import Player, Team
from app.services.player_rating_avgs import (
    DEF_KEYS,
    MENTAL_KEYS_SKATER,
    OFF_KEYS,
    OVERVIEW_KEYS,
    PHYS_KEYS,
    _float_cell,
)
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row
from app.services.seasons import get_current_season, season_age_reference_date
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized


@dataclass(frozen=True)
class TeamReportColumn:
    """One sortable metric on the Team Reports page."""

    key: str
    label: str
    kind: str  # count | age | height | weight | attr | abi | pot


@dataclass(frozen=True)
class TeamReportCategory:
    key: str
    label: str
    columns: tuple[TeamReportColumn, ...]


@dataclass(frozen=True)
class TeamReportRow:
    team: Team
    player_count: int
    values: dict[str, float | None]


def _player_age_years(birth: date | None, as_of: date) -> int | None:
    if birth is None:
        return None
    return as_of.year - birth.year - ((as_of.month, as_of.day) < (birth.month, birth.day))


def _is_skater(pl: Player) -> bool:
    return (pl.position or "").strip().upper() != "G"


def _avg(vals: list[float]) -> float | None:
    if not vals:
        return None
    return sum(vals) / len(vals)


def _col(key: str, label: str, kind: str) -> TeamReportColumn:
    return TeamReportColumn(key=key, label=label, kind=kind)


def team_report_categories() -> tuple[TeamReportCategory, ...]:
    """Category tabs aligned with skater player-page groupings."""
    overview_rating_labels = {
        "skating": "Skating",
        "shooting": "Shooting",
        "playmaking": "Playmaking",
        "defending": "Defending",
        "physicality": "Physicality",
        "conditioning": "Conditioning",
        "character": "Character",
        "hockey_sense": "Hockey Sense",
    }
    overview_cols: list[TeamReportColumn] = [
        _col("player_count", "Players", "count"),
        _col("age", "Age", "age"),
        _col("height", "Height", "height"),
        _col("weight", "Weight", "weight"),
        _col("ability", "Ability", "abi"),
        _col("potential", "Potential", "pot"),
    ]
    for k in OVERVIEW_KEYS:
        overview_cols.append(_col(k, overview_rating_labels[k], "attr"))

    offense_labels = {
        "screening": "Screening",
        "getting_open": "Getting Open",
        "passing": "Passing",
        "puck_handling": "Puck Handling",
        "shooting_accuracy": "Shot Accuracy",
        "shooting_range": "Shot Range",
        "offensive_read": "Offensive Read",
    }
    defense_labels = {
        "checking": "Checking",
        "faceoffs": "Faceoffs",
        "hitting": "Hitting",
        "positioning": "Positioning",
        "shot_blocking": "Shot Blocking",
        "stickchecking": "Stickchecking",
        "defensive_read": "Defensive Read",
    }
    mental_labels = {
        "aggression": "Aggression",
        "bravery": "Bravery",
        "determination": "Determination",
        "teamplayer": "Team Player",
        "leadership": "Leadership",
        "temperament": "Temperament",
        "professionalism": "Professionalism",
    }
    physical_labels = {
        "acceleration": "Acceleration",
        "agility": "Agility",
        "balance": "Balance",
        "speed": "Speed",
        "stamina": "Stamina",
        "strength": "Strength",
        "fighting": "Fighting",
    }

    def _cat(key: str, label: str, keys: tuple[str, ...], labels: dict[str, str]) -> TeamReportCategory:
        cols = [_col("player_count", "Players", "count")]
        cols.extend(_col(k, labels[k], "attr") for k in keys)
        return TeamReportCategory(key=key, label=label, columns=tuple(cols))

    return (
        TeamReportCategory(key="overview", label="Overview", columns=tuple(overview_cols)),
        _cat("offense", "Offense", OFF_KEYS, offense_labels),
        _cat("defense", "Defense", DEF_KEYS, defense_labels),
        _cat("mental", "Mental", MENTAL_KEYS_SKATER, mental_labels),
        _cat("physical", "Physical", PHYS_KEYS, physical_labels),
    )


def _load_ratings_by_fhm_id() -> dict[str, dict[str, Any]]:
    """Full ``player_ratings.csv`` index for the active league import folder."""
    raw_dir = (
        Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
        if has_request_context()
        else Path(Config.RAW_IMPORT_DIR)
    )
    path = raw_dir / "player_ratings.csv"
    if not path.is_file():
        return {}
    df = read_csv_normalized(path)
    by_id: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = cell_val(r, "playerid")
        if pid:
            by_id[str(pid).strip()] = r
    return by_id


def _metric_value_for_player(
    pl: Player,
    rr: dict[str, Any] | None,
    col: TeamReportColumn,
    *,
    age_ref: date,
) -> float | None:
    if col.key == "player_count":
        return None
    if col.kind == "age":
        a = _player_age_years(pl.birth_date, age_ref)
        return float(a) if a is not None else None
    if col.kind == "height":
        h = pl.height_inches
        return float(h) if h is not None and int(h) > 0 else None
    if col.kind == "weight":
        w = pl.weight_lbs
        return float(w) if w is not None and int(w) > 0 else None
    if col.kind == "abi":
        if rr:
            v = fhm_abi_pot_float(rr.get("ability"))
            if v is not None:
                return v
        if pl.overall_ability is not None:
            return float(pl.overall_ability)
        return None
    if col.kind == "pot":
        if rr:
            v = fhm_abi_pot_float(rr.get("potential"))
            if v is not None:
                return v
        if pl.overall_potential is not None:
            return float(pl.overall_potential)
        return None
    if col.kind == "attr" and rr:
        return _float_cell(rr.get(col.key))
    return None


def build_team_report_rows(session: Session) -> list[TeamReportRow]:
    """One row per team: averages over active non-goalie roster skaters."""
    season = get_current_season()
    age_ref = season_age_reference_date(season)
    teams = list(session.scalars(select(Team).order_by(Team.name)).all())
    ratings_by_id = _load_ratings_by_fhm_id()
    all_cols = [c for cat in team_report_categories() for c in cat.columns]

    roster = session.scalars(
        select(Player).where(Player.current_team_id.isnot(None)).order_by(Player.last_name)
    ).all()
    by_team: dict[int, list[Player]] = {}
    for pl in roster:
        if not _is_skater(pl) or pl.current_team_id is None:
            continue
        by_team.setdefault(int(pl.current_team_id), []).append(pl)

    rows: list[TeamReportRow] = []
    for team in teams:
        skaters = by_team.get(int(team.id), [])
        values: dict[str, float | None] = {"player_count": float(len(skaters))}
        for col in all_cols:
            if col.key == "player_count":
                continue
            nums: list[float] = []
            for pl in skaters:
                fid = str(pl.fhm_player_id or "").strip()
                rr = ratings_by_id.get(fid) if fid else None
                if rr is None and fid:
                    rr = get_player_ratings_row(fid)
                v = _metric_value_for_player(pl, rr, col, age_ref=age_ref)
                if v is not None:
                    nums.append(v)
            values[col.key] = _avg(nums)
        rows.append(TeamReportRow(team=team, player_count=len(skaters), values=values))

    rows.sort(key=lambda r: (r.team.full_display_name() or "").lower())
    return rows


def format_team_report_display(value: float | None, kind: str) -> str:
    """Human-readable cell text for templates."""
    if value is None:
        return "—"
    if kind == "count":
        return str(int(round(value)))
    if kind == "age":
        return f"{value:.1f}"
    if kind == "height":
        inches = int(round(value))
        if inches <= 0:
            return "—"
        return f"{inches // 12}'{inches % 12}\""
    if kind == "weight":
        return str(int(round(value)))
    if kind in ("abi", "pot"):
        return f"{value:.1f}"
    if kind == "attr":
        return f"{value:.2f}"
    return f"{value:.2f}"

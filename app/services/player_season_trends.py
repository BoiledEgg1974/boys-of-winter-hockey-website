"""Season trend rows for player profiles: skater GP + G/A + Pts + GR; goalie GP + L/T + W/SO + GR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import PlayerGoalieCareerLine


def _season_float_attr(ln: Any, attr: str) -> float | None:
    v = getattr(ln, attr, None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_skater_career_gr_lookup(raw_dir: Path, fhm_player_id: str | int | None) -> dict[tuple[int, int, int], float]:
    """Map (season_year, team_fhm_id, league_fhm_id) → GR from FHM career CSVs.

    Used when ``PlayerSkaterCareerLine.game_rating`` is still null (e.g. before a DB re-import).
    Retired file is read first; active ``rs`` overwrites on duplicate keys so active import wins.
    """
    from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_float, to_int

    if fhm_player_id is None or str(fhm_player_id).strip() == "":
        return {}
    target = to_int(str(fhm_player_id).strip())
    if target is None:
        return {}

    out: dict[tuple[int, int, int], float] = {}
    for filename in (
        "player_skater_retired_career_stats_rs.csv",
        "player_skater_career_stats_rs.csv",
    ):
        path = raw_dir / filename
        if not path.exists():
            continue
        df = read_csv_normalized(path)
        if df.empty or "playerid" not in df.columns:
            continue
        sub = df[df["playerid"].astype(str).str.strip() == str(target)]
        for _, row in sub.iterrows():
            r = row.to_dict()
            year = to_int(cell_val(r, "year"))
            tm = to_int(cell_val(r, "team_id", "teamid"))
            lid = to_int(cell_val(r, "league_id", "leagueid"))
            if year is None or tm is None or lid is None:
                continue
            gr = to_float(cell_val(r, "gr", "game_rating"))
            if gr is None:
                continue
            out[(year, tm, lid)] = float(gr)
    return out


def build_player_season_trend_rows(
    _session: Session,
    career_rs_lines: list[Any],
    skater_gr_lookup: dict[tuple[int, int, int], float] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(rows, goalie_mode)`` for the profile chart.

    Skaters: **grey GP** bar; stacked **G** and **A**; line = **Pts** (G+A); orange = **GR** (0–100).
    Bars and Pts share one numeric axis (max of GP and G+A).

    Goalies: **grey bar = GP**; **red** = L and **green** = T; **yellow** = wins; **light blue** = shutouts;
    **orange** = game rating (0–100 scale).
    """
    if not career_rs_lines:
        return [], False

    goalie_mode = isinstance(career_rs_lines[0], PlayerGoalieCareerLine)

    def _sort_key(ln: Any) -> tuple[int, int, int]:
        return (
            int(getattr(ln, "season_year", 0) or 0),
            int(getattr(ln, "team_fhm_id", 0) or 0),
            int(getattr(ln, "league_fhm_id", 0) or 0),
        )

    lines = sorted(career_rs_lines, key=_sort_key)
    out: list[dict[str, Any]] = []

    for ln in lines:
        sy = int(ln.season_year)
        label_short = f"{sy}/{(sy + 1) % 100:02d}"
        year_hyphen = f"{sy}-{(sy + 1) % 100:02d}"

        if isinstance(ln, PlayerGoalieCareerLine):
            w = int(ln.wins or 0)
            l = int(ln.losses or 0)
            t = int(ln.ties_otl or 0)
            so = int(ln.shutouts or 0)
            gp = max(int(ln.gp or 0), w + l + t)
            if gp <= 0 and w + l + t + so <= 0:
                continue
            out.append(
                {
                    "season_year": sy,
                    "label": label_short,
                    "year_label_hyphen": year_hyphen,
                    "gk_gp": gp,
                    "gk_w": w,
                    "gk_l": l,
                    "gk_t": t,
                    "gk_so": so,
                    "game_rating": _season_float_attr(ln, "game_rating"),
                }
            )
        else:
            g = int(ln.goals or 0)
            a = int(ln.assists or 0)
            pim = int(ln.pim or 0)
            pts_line = g + a
            if g + a + pim <= 0:
                continue
            stack_chart = g + a
            sk_gp = int(ln.gp or 0)
            gr = _season_float_attr(ln, "game_rating")
            if gr is None and skater_gr_lookup is not None:
                gr = skater_gr_lookup.get((sy, int(ln.team_fhm_id), int(ln.league_fhm_id)))
            out.append(
                {
                    "season_year": sy,
                    "label": label_short,
                    "year_label_hyphen": year_hyphen,
                    "g": g,
                    "a": a,
                    "pim": pim,
                    "so": 0,
                    "pts": pts_line,
                    "stack_total": stack_chart,
                    "sk_gp": sk_gp,
                    "game_rating": gr,
                }
            )

    return out, goalie_mode

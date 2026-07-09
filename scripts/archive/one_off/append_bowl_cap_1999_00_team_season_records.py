"""Append 1999-00 bowl-cap team season rows + awards from FHM career CSV exports.

Derives regular-season W/L/OTL, GF/GA, and basic special-teams totals from
``player_*_career_stats_rs.csv``. Playoff finish labels use max playoff skater GP.

Run from repo root::

  python scripts/archive/one_off/append_bowl_cap_1999_00_team_season_records.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RAW = REPO / "data" / "imports" / "raw" / "bowl_cap"
YEAR_LABEL = "1999-00"
SEASON_YEAR = "1999"
LEAGUE_ID = "0"
GP = 82

PLAYOFF_RESULT_BY_MAX_PO_GP = {
    26: "BOWL CUP CHAMPION",
    21: "Lost Cup Finals",
    19: "Lost Conference Finals",
    16: "Lost Conference Finals",
    13: "Lost Conference Semi-Finals",
    12: "Lost Conference Semi-Finals",
    7: "Lost Conference Quarter-Finals",
    6: "Lost Conference Quarter-Finals",
    5: "Lost Conference Quarter-Finals",
}


@dataclass
class TeamAgg:
    w: int = 0
    l: int = 0
    otl: int = 0
    gf: int = 0
    ga: int = 0
    pim: int = 0
    ppg: int = 0
    shg: int = 0
    sog: int = 0
    sa: int = 0
    conf_id: str = ""
    div_id: str = ""
    max_po_gp: int = 0


def conf_div_names(conf_id: str, div_id: str) -> tuple[str, str]:
    conf = "Eastern" if conf_id == "0" else "Western"
    eastern = ("Northeast", "Atlantic", "Southeast")
    western = ("Central", "Pacific", "Northwest")
    try:
        idx = int(div_id)
    except ValueError:
        return conf, ""
    divs = eastern if conf_id == "0" else western
    if 0 <= idx < len(divs):
        return conf, divs[idx]
    return conf, ""


def load_team_meta() -> dict[str, TeamAgg]:
    out: dict[str, TeamAgg] = {}
    with open(RAW / "team_data.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("LeagueId") != LEAGUE_ID:
                continue
            out[row["TeamId"]] = TeamAgg(
                conf_id=row.get("Conference Id", ""),
                div_id=row.get("Division Id", ""),
            )
    return out


def aggregate(teams: dict[str, TeamAgg]) -> None:
    with open(RAW / "player_goalie_career_stats_rs.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("Year") != SEASON_YEAR or row.get("League Id") != LEAGUE_ID:
                continue
            tid = row["Team Id"]
            t = teams.setdefault(tid, TeamAgg())
            t.w += int(row.get("W") or 0)
            t.l += int(row.get("L") or 0)
            t.otl += int(row.get("T/OL") or 0)
            t.ga += int(row.get("GA") or 0)
            t.sa += int(row.get("SA") or 0)

    with open(RAW / "player_skater_career_stats_rs.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("Year") != SEASON_YEAR or row.get("League Id") != LEAGUE_ID:
                continue
            tid = row["Team Id"]
            t = teams.setdefault(tid, TeamAgg())
            t.gf += int(row.get("G") or 0)
            t.pim += int(row.get("PIM") or 0)
            t.ppg += int(row.get("PP G") or 0)
            t.shg += int(row.get("SH G") or 0)
            t.sog += int(row.get("SOG") or 0)

    with open(RAW / "player_skater_career_stats_po.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("Year") != SEASON_YEAR or row.get("League Id") != LEAGUE_ID:
                continue
            tid = row["Team Id"]
            t = teams.setdefault(tid, TeamAgg())
            t.max_po_gp = max(t.max_po_gp, int(row.get("GP") or 0))


def playoff_result(t: TeamAgg) -> str:
    if t.max_po_gp <= 0:
        return "Missed Playoffs"
    return PLAYOFF_RESULT_BY_MAX_PO_GP.get(t.max_po_gp, "Made Playoffs")


def team_row(tid: str, t: TeamAgg) -> str:
    pts = t.w * 2 + t.otl
    gd = t.gf - t.ga
    conf, div = conf_div_names(t.conf_id, t.div_id)
    pim_g = round(t.pim / GP, 2) if GP else ""
    return ",".join(
        [
            YEAR_LABEL,
            tid,
            "",
            "",
            conf,
            "",
            div,
            "",
            str(GP),
            str(t.w),
            str(t.l),
            str(t.otl),
            str(pts),
            str(t.gf),
            str(t.ga),
            str(gd),
            playoff_result(t),
            str(pim_g),
            str(t.ppg),
            "",
            "",
            str(t.shg),
            "",
            "",
            "",
            "",
            str(t.sog),
            str(t.sa),
        ]
    )


def append_team_season_records(teams: dict[str, TeamAgg]) -> int:
    path = RAW / "team_season_records_template.csv"
    text = path.read_text(encoding="utf-8")
    if YEAR_LABEL in text:
        print(f"{path.name} already contains {YEAR_LABEL}; skipping team rows")
        return 0
    active = [
        (tid, t)
        for tid, t in teams.items()
        if (t.w + t.l + t.otl) > 0 or t.gf > 0 or t.ga > 0
    ]
    lines = [team_row(tid, t) for tid, t in sorted(active, key=lambda kv: int(kv[0]))]
    with open(path, "a", encoding="utf-8", newline="") as f:
        if not text.endswith("\n"):
            f.write("\n")
        f.write("\n".join(lines))
        f.write("\n")
    print(f"Appended {len(lines)} rows to {path.name}")
    return len(lines)


def append_awards() -> int:
    sheet = RAW / "history_awards.sheet.csv"
    text = sheet.read_text(encoding="utf-8")
    if f"{YEAR_LABEL}," in text:
        print(f"{sheet.name} already contains {YEAR_LABEL}; skipping awards")
        return 0
    awards = [
        (YEAR_LABEL, "TED LINDSAY  TROPHY", "1491", "", ""),
        (YEAR_LABEL, "ART ROSS TROPHY", "1491", "", ""),
        (YEAR_LABEL, "RICHARD TROPHY", "14033", "", ""),
        (YEAR_LABEL, "LADY BYNG TROPHY", "13050", "", ""),
        (YEAR_LABEL, "MASTERTON TROPHY", "494", "", ""),
        (YEAR_LABEL, "MARK MESSIER LEADERSHIP AWARD", "32", "", ""),
        (YEAR_LABEL, "WILLIAM JENNINGS  TROPHY", "1737", "", ""),
        (YEAR_LABEL, "BOWL RISING STAR", "14529", "", ""),
        (YEAR_LABEL, "CALDER TROPHY", "15065", "", ""),
        (YEAR_LABEL, "LANGWAY TROPHY", "134", "", ""),
        (YEAR_LABEL, "BOURQUE TROPHY", "709", "", ""),
        (YEAR_LABEL, "HART TROPHY", "1491", "", ""),
        (YEAR_LABEL, "CONN SMYTHE TROPHY", "12995", "", ""),
        (YEAR_LABEL, "NORRIS TROPHY", "955", "", ""),
        (YEAR_LABEL, "SELKE TROPHY", "960", "", ""),
        (YEAR_LABEL, "VEZINA TROPHY", "1737", "", ""),
        (YEAR_LABEL, "JIM GREGORY TROPHY", "", "15", "", ""),
        (YEAR_LABEL, "JACK ADAMS TROPHY", "", "15", "1", ""),
        (YEAR_LABEL, "PLUS/MINUS TROPHY", "14033", "", ""),
        (YEAR_LABEL, "ROGER CROZIER SAVING GRACE TROPHY", "1737", "", ""),
        (YEAR_LABEL, "THE MASTERS' GREEN JACKET", "13012", "", ""),
        (YEAR_LABEL, "CLARENCE CAMPBELL TROPHY", "", "", "23", ""),
        (YEAR_LABEL, "PRINCE OF WALES TROPHY", "", "", "15", ""),
        (YEAR_LABEL, "BOWL CUP TROPHY", "", "", "15", ""),
        (YEAR_LABEL, "BOILEDEGG'S TROPHY", "", "", "15", ""),
    ]
    with open(sheet, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not text.endswith("\n"):
            f.write("\n")
        w.writerows(awards)
    print(f"Appended {len(awards)} award rows to {sheet.name}")
    return len(awards)


def main() -> None:
    teams = load_team_meta()
    aggregate(teams)
    append_team_season_records(teams)
    append_awards()


if __name__ == "__main__":
    main()

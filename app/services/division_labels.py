"""Division display names from divisions.csv (conference + division disambiguation)."""
from __future__ import annotations

import csv
from pathlib import Path


def load_division_display_maps(div_csv: Path) -> tuple[dict[tuple[int, int], str], dict[int, str]]:
    """Parse divisions.csv like the standings page: only rows with League Id blank or 0."""
    div_name_by_pair: dict[tuple[int, int], str] = {}
    div_name_by_id: dict[int, str] = {}
    if not div_csv.is_file():
        return div_name_by_pair, div_name_by_id
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
        return {}, {}
    return div_name_by_pair, div_name_by_id


def team_division_display_label(
    st,
    team,
    div_name_by_pair: dict[tuple[int, int], str],
    div_name_by_id: dict[int, str],
) -> str:
    """Same resolution as standings view: TeamStanding.division, then CSV (conf, div) / div id."""
    div_label = (st.division or "").strip()
    if team is not None and team.fhm_division_id is not None:
        did = int(team.fhm_division_id)
        cid = int(team.fhm_conference_id) if team.fhm_conference_id is not None else None
        if cid is not None and (cid, did) in div_name_by_pair:
            div_label = div_name_by_pair[(cid, did)]
        elif did in div_name_by_id:
            div_label = div_name_by_id[did]
    return div_label


def division_group_key_for_standing(
    st,
    team,
    div_name_by_pair: dict[tuple[int, int], str],
    div_name_by_id: dict[int, str],
) -> str:
    if div_name_by_pair or div_name_by_id:
        return (team_division_display_label(st, team, div_name_by_pair, div_name_by_id) or "").strip() or "League"
    return (st.division or "").strip() or "League"

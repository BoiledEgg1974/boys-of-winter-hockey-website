"""Era-accurate static logos for BOWL-Cap history awards when normal team linkage is missing.

Covers team trophies (BoiledEgg's, Prince of Wales, Campbell, Bowl Cup) via ``unresolved_team=``
plus curated Jim Gregory rows (executive / note-only ``unresolved_team=`` labels).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from flask import has_app_context

from app.services.history_coach_awards import _parse_unresolved_team, is_jim_gregory_award

_SHEET_SEASON_LABEL_RE = re.compile(r"^(\d{4})-(\d{2})$")

# Normalized award titles (see ``TEAM_HISTORY_AWARD_TITLES`` in ``history_team_awards``) that may use era logos.
_CAP_TEAM_AWARDS_WITH_ERA_LOGOS: frozenset[str] = frozenset(
    (
        "BOILEDEGG'S TROPHY",
        "PRINCE OF WALES TROPHY",
        "CLARENCE CAMPBELL TROPHY",
        "BOWL CUP TROPHY",
    )
)

# (sheet season label, normalized ``unresolved_team=`` text) -> static path under app/static/
_CAP_TEAM_ERA_LOGOS: dict[tuple[str, str], str] = {
    ("1989-90", "hartford whalers"): "logos/history_awards/bowl_cap/boiledeggs_hartford_whalers_1989-1992.png",
    ("1990-91", "hartford whalers"): "logos/history_awards/bowl_cap/boiledeggs_hartford_whalers_1989-1992.png",
    ("1991-92", "hartford whalers"): "logos/history_awards/bowl_cap/boiledeggs_hartford_whalers_1989-1992.png",
    # Same vintage Jets PNG (1990s mark) for early-90s Campbell rows and 1993–94.
    ("1990-91", "winnipeg jets"): "logos/history_awards/bowl_cap/boiledeggs_winnipeg_jets_1993-94.png",
    ("1991-92", "winnipeg jets"): "logos/history_awards/bowl_cap/boiledeggs_winnipeg_jets_1993-94.png",
    ("1993-94", "winnipeg jets"): "logos/history_awards/bowl_cap/boiledeggs_winnipeg_jets_1993-94.png",
}

# Jim Gregory: ``unresolved_team=`` often holds a league username; map specific (season, label) → logo.
_JIM_GREGORY_ERA_LOGOS: dict[tuple[str, str], str] = {
    ("1989-90", "tidus mino"): "logos/history_awards/bowl_cap/boiledeggs_hartford_whalers_1989-1992.png",
}
_TEAM_ID_LABEL_RE = re.compile(r"^#?\s*(\d+)\s*$")


def _norm_award_title(name: str | None) -> str:
    return " ".join((name or "").upper().split())


def _norm_team_label(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _sheet_season_from_notes(notes: str | None) -> str | None:
    for part in (notes or "").split(";"):
        p = part.strip()
        if p.startswith("sheet_season="):
            tok = p.split("=", 1)[1].strip().split(";")[0].strip()
            if _SHEET_SEASON_LABEL_RE.match(tok):
                return tok
    return None


def _season_label_for_match(award: object) -> str | None:
    tok = _sheet_season_from_notes(award.notes)
    if tok:
        return tok
    se = getattr(award, "season", None)
    if se is not None and (se.label or "").strip():
        lab = (se.label or "").strip()
        if _SHEET_SEASON_LABEL_RE.match(lab):
            return lab
    return None


def _team_id_from_notes_label(label: str | None) -> str | None:
    m = _TEAM_ID_LABEL_RE.match((label or "").strip())
    return m.group(1) if m else None


@lru_cache(maxsize=8)
def _team_name_overrides_by_id(raw_dir_s: str) -> dict[str, list[tuple[str, str]]]:
    """Load `{team_id: [(season_label, team_name_override), ...]}` from team season template."""
    out: dict[str, list[tuple[str, str]]] = {}
    raw_dir = Path(raw_dir_s)
    p = raw_dir / "team_season_records_template.csv"
    if not p.is_file():
        return out
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            import csv

            rdr = csv.DictReader(f, delimiter=delim)
            for row in rdr:
                tid = (row.get("Team ID") or row.get("team_id") or "").strip()
                nm = (row.get("Team Name Override") or row.get("team_name_override") or "").strip()
                season = (row.get("Year") or row.get("season") or "").strip()
                if not tid or not nm:
                    continue
                out.setdefault(tid, []).append((season, nm))
    except Exception:
        return {}
    return out


def _team_name_from_template(team_id: str, season_label: str | None) -> str | None:
    from flask import current_app

    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR") or "")).resolve()
    by_id = _team_name_overrides_by_id(str(raw_dir))
    rows = by_id.get(str(team_id).strip()) or []
    if not rows:
        return None
    if season_label:
        for s, nm in rows:
            if s == season_label:
                return nm
    return rows[0][1]


def _historical_logo_rel_for_team_id(team_id: str) -> str | None:
    """Resolve `*-t<ID>` logo from `app/static/logos/teams/bowl_historical`."""
    from flask import current_app

    hist_dir = Path(current_app.root_path) / "static" / "logos" / "teams" / "bowl_historical"
    if not hist_dir.is_dir():
        return None
    tid = str(team_id or "").strip()
    if not tid:
        return None
    for p in hist_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in (".png", ".webp", ".jpg", ".jpeg", ".svg"):
            continue
        if p.stem.lower().endswith(f"-t{tid.lower()}"):
            return f"logos/teams/bowl_historical/{p.name}"
    return None


def history_team_award_notes_team_label(award: object) -> str | None:
    """Plain-text team name from ``unresolved_team=`` when the row has no :class:`~app.models.Team``."""
    if getattr(award, "team", None):
        return None
    raw = _parse_unresolved_team(award.notes)
    tid = _team_id_from_notes_label(raw)
    if tid:
        season = _season_label_for_match(award)
        name = _team_name_from_template(tid, season)
        if name:
            return name
    return raw


def history_team_award_era_logo_static_relpath(award: object) -> str | None:
    """Return ``app/static``-relative path for a curated era logo, or ``None`` to use normal team assets."""
    if not has_app_context():
        return None
    from flask import current_app

    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    if league_slug not in ("bowl-cap", "bowl-historical"):
        return None
    if getattr(award, "team", None):
        return None
    team_txt = _parse_unresolved_team(award.notes)
    team_id = _team_id_from_notes_label(team_txt)
    if team_id:
        hit = _historical_logo_rel_for_team_id(team_id)
        if hit:
            return hit
    if league_slug != "bowl-cap":
        return None
    if _norm_award_title(award.award_name) not in _CAP_TEAM_AWARDS_WITH_ERA_LOGOS:
        return None
    season = _season_label_for_match(award)
    if not season or not team_txt:
        return None
    return _CAP_TEAM_ERA_LOGOS.get((season, _norm_team_label(team_txt)))


def history_jim_gregory_era_logo_static_relpath(award: object) -> str | None:
    """Era logo beside the Team column for note-only Jim Gregory rows (no ``team_id``)."""
    if not has_app_context():
        return None
    from flask import current_app

    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    if league_slug not in ("bowl-cap", "bowl-historical"):
        return None
    if not is_jim_gregory_award(getattr(award, "award_name", None)):
        return None
    if getattr(award, "team", None):
        return None
    season = _season_label_for_match(award)
    who = _parse_unresolved_team(award.notes)
    tid = _team_id_from_notes_label(who)
    if tid:
        hit = _historical_logo_rel_for_team_id(tid)
        if hit:
            return hit
    if league_slug != "bowl-cap":
        return None
    if not season or not who:
        return None
    return _JIM_GREGORY_ERA_LOGOS.get((season, _norm_team_label(who)))

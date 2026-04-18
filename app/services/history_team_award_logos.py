"""Era-accurate static logos for BOWL-Cap history awards when normal team linkage is missing.

Covers team trophies (BoiledEgg's, Prince of Wales, Campbell, Bowl Cup) via ``unresolved_team=``
plus curated Jim Gregory rows (executive / note-only ``unresolved_team=`` labels).
"""

from __future__ import annotations

import re

from flask import has_app_context

from app.services.history_coach_awards import _parse_unresolved_team, is_jim_gregory_award

_SHEET_SEASON_LABEL_RE = re.compile(r"^(\d{4})-(\d{2})$")

# Normalized award titles (see ``_TEAM_HISTORY_AWARD_TITLES`` in routes) that may use era logos.
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


def history_team_award_notes_team_label(award: object) -> str | None:
    """Plain-text team name from ``unresolved_team=`` when the row has no :class:`~app.models.Team``."""
    if getattr(award, "team", None):
        return None
    return _parse_unresolved_team(award.notes)


def history_team_award_era_logo_static_relpath(award: object) -> str | None:
    """Return ``app/static``-relative path for a curated era logo, or ``None`` to use normal team assets."""
    if not has_app_context():
        return None
    from flask import current_app

    if current_app.config.get("LEAGUE_SLUG") != "bowl-cap":
        return None
    if getattr(award, "team", None):
        return None
    if _norm_award_title(award.award_name) not in _CAP_TEAM_AWARDS_WITH_ERA_LOGOS:
        return None
    season = _season_label_for_match(award)
    team_txt = _parse_unresolved_team(award.notes)
    if not season or not team_txt:
        return None
    return _CAP_TEAM_ERA_LOGOS.get((season, _norm_team_label(team_txt)))


def history_jim_gregory_era_logo_static_relpath(award: object) -> str | None:
    """Era logo beside the Team column for note-only Jim Gregory rows (no ``team_id``)."""
    if not has_app_context():
        return None
    from flask import current_app

    if current_app.config.get("LEAGUE_SLUG") != "bowl-cap":
        return None
    if not is_jim_gregory_award(getattr(award, "award_name", None)):
        return None
    if getattr(award, "team", None):
        return None
    season = _season_label_for_match(award)
    who = _parse_unresolved_team(award.notes)
    if not season or not who:
        return None
    return _JIM_GREGORY_ERA_LOGOS.get((season, _norm_team_label(who)))

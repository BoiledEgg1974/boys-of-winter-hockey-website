"""Player profile trophy strip: aggregate ``HistoryAward`` rows with counts and tooltip text.

Logic mirrors ``routes.main`` history award dedupe and trophy asset resolution so badges stay
consistent with the League History page across all league sites. Rows are deduped **per**
``award_name`` (same as each history card), not across all awards at once.

**BOWL Cup:** ``BOWL CUP TROPHY`` is usually stored as a team award (no ``player_id``). We infer
championships by matching each cup row’s winning ``team_id`` / ``unresolved_team=`` label and
``sheet_season`` start year to the player’s skater or goalie career lines (same team, same season
year as the sheet label).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from flask import current_app
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import HistoryAward, PlayerGoalieCareerLine, PlayerSkaterCareerLine, Team
from app.services.history_coach_awards import _parse_unresolved_team
from app.services.history_team_awards import is_team_history_award

_SHEET_SEASON_LABEL_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _slugify_award_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _history_award_trophy_scan_dirs(static_root: Path, league_slug: str) -> tuple[Path, ...]:
    return (
        static_root / "img" / "trophies" / league_slug,
        static_root / "img" / "history" / "trophies" / league_slug,
    )


_TROPHY_STEM_ALIASES: dict[str, tuple[str, ...]] = {
    "boiledegg_s_trophy": ("boiledeggs_trophy",),
    "the_masters_green_jacket": ("masters_green_jacket",),
}

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


def _build_trophy_stem_map(static_root: Path, league_slug: str) -> dict[str, str]:
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


def _trophy_rel_from_map(stem_map: dict[str, str], award_name: str) -> str | None:
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


def _history_award_sheet_season_from_notes(notes: str | None) -> str | None:
    for part in (notes or "").split(";"):
        p = part.strip()
        if p.startswith("sheet_season="):
            tok = p.split("=", 1)[1].strip().split(";")[0].strip()
            if _SHEET_SEASON_LABEL_RE.match(tok):
                return tok
    return None


def _history_award_year_token(a: HistoryAward) -> object:
    tok = _history_award_sheet_season_from_notes(a.notes)
    if tok:
        return tok
    if getattr(a, "season", None) is not None and (a.season.label or "").strip():
        return (a.season.label or "").strip()
    return int(a.season_id)


def _history_award_year_sort_key(a: HistoryAward) -> tuple[int, int, int]:
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
    return (_history_award_year_token(a), a.player_id, (a.notes or "").strip())


def _history_award_dedupe_rank(a: HistoryAward) -> tuple[int, int, int, int, int]:
    return (
        1 if (getattr(a, "staff_fhm_id", None) or "").strip() else 0,
        1 if a.team_id is not None else 0,
        1 if a.player_id is not None else 0,
        len((a.notes or "").strip()),
        -a.id,
    )


def _dedupe_history_awards(rows: list[HistoryAward]) -> list[HistoryAward]:
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
    """Same rules as ``routes.main._collapse_same_trophy_year_history_awards`` (keep in sync)."""
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


def _norm_award_title(s: str) -> str:
    return " ".join((s or "").upper().split())


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

_AWARD_NAME_ALIASES: dict[str, str] = {
    "LANGWY TROPHY": "LANGWAY TROPHY",
}


def _award_panel_sort_index(award_name: str) -> int:
    key = _norm_award_title(award_name)
    key = _AWARD_NAME_ALIASES.get(key, key)
    for i, canonical in enumerate(_AWARD_PANEL_ORDER):
        if _norm_award_title(canonical) == key:
            return i
    return len(_AWARD_PANEL_ORDER) + 1


_BOWL_CUP_TITLE = "BOWL CUP TROPHY"


def _season_start_year_from_award_notes(notes: str | None) -> int | None:
    tok = _history_award_sheet_season_from_notes(notes)
    if not tok:
        return None
    m = _SHEET_SEASON_LABEL_RE.match(tok)
    return int(m.group(1)) if m else None


def _cup_winning_team_db_id(session: Session, a: HistoryAward) -> int | None:
    if a.team_id is not None:
        return int(a.team_id)
    label = (_parse_unresolved_team(a.notes) or "").strip().lower()
    if not label:
        return None
    for t in session.scalars(select(Team)).all():
        ab = (t.abbreviation or "").strip().lower()
        nm = (t.name or "").strip().lower()
        disp = t.full_display_name().strip().lower()
        if label == ab or label == nm or label == disp:
            return int(t.id)
    for t in session.scalars(select(Team)).all():
        disp = t.full_display_name().strip().lower()
        nm = (t.name or "").strip().lower()
        if len(label) >= 8 and (label in disp or label in nm):
            return int(t.id)
    return None


def _player_on_team_in_season(session: Session, player_id: int, team_db_id: int, season_year: int) -> bool:
    tm = session.get(Team, team_db_id)
    if not tm:
        return False
    team_clauses = [PlayerSkaterCareerLine.team_id == team_db_id]
    if tm.fhm_team_id:
        try:
            team_clauses.append(PlayerSkaterCareerLine.team_fhm_id == int(str(tm.fhm_team_id).strip()))
        except ValueError:
            pass
    n_sk = session.scalar(
        select(func.count())
        .select_from(PlayerSkaterCareerLine)
        .where(
            PlayerSkaterCareerLine.player_id == player_id,
            PlayerSkaterCareerLine.season_year == season_year,
            or_(*team_clauses),
        )
    )
    if n_sk and int(n_sk) > 0:
        return True
    g_clauses = [PlayerGoalieCareerLine.team_id == team_db_id]
    if tm.fhm_team_id:
        try:
            g_clauses.append(PlayerGoalieCareerLine.team_fhm_id == int(str(tm.fhm_team_id).strip()))
        except ValueError:
            pass
    n_gk = session.scalar(
        select(func.count())
        .select_from(PlayerGoalieCareerLine)
        .where(
            PlayerGoalieCareerLine.player_id == player_id,
            PlayerGoalieCareerLine.season_year == season_year,
            or_(*g_clauses),
        )
    )
    return bool(n_gk and int(n_gk) > 0)


def _player_bowl_cup_season_labels(session: Session, player_id: int) -> list[str]:
    """Season labels (``YYYY-YY``) where ``HistoryAward`` BOWL CUP winner matches roster career lines."""
    cup_rows = [
        a
        for a in session.scalars(
            select(HistoryAward)
            .options(joinedload(HistoryAward.season), joinedload(HistoryAward.team))
            .where(HistoryAward.award_name.ilike("%bowl%cup%trophy%"))
        ).all()
        if _norm_award_title(a.award_name or "") == _BOWL_CUP_TITLE
    ]
    if not cup_rows:
        return []
    cup_clean = _collapse_same_trophy_year_history_awards(_dedupe_history_awards(cup_rows))
    labels: list[str] = []
    for a in cup_clean:
        tid = _cup_winning_team_db_id(session, a)
        if tid is None:
            continue
        y = _season_start_year_from_award_notes(a.notes)
        if y is None and getattr(a, "season", None) is not None and a.season.start_year is not None:
            y = int(a.season.start_year)
        if y is None:
            continue
        if not _player_on_team_in_season(session, player_id, tid, y):
            continue
        labels.append(_display_season_for_badge(a))
    # De-dupe labels while preserving sort order (re-import dupes)
    seen: set[str] = set()
    uniq: list[str] = []
    for lab in labels:
        if lab in seen:
            continue
        seen.add(lab)
        uniq.append(lab)

    def _lab_key(lab: str) -> tuple[int, int]:
        m = _SHEET_SEASON_LABEL_RE.match(lab)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (0, 0)

    uniq.sort(key=_lab_key, reverse=True)
    return uniq


def _display_season_for_badge(a: HistoryAward) -> str:
    tok = _history_award_sheet_season_from_notes(a.notes)
    if tok:
        return tok
    if getattr(a, "season", None) is not None and (a.season.label or "").strip():
        lab = (a.season.label or "").strip()
        if _SHEET_SEASON_LABEL_RE.match(lab):
            return lab
        return lab
    return str(a.season_id)


def player_history_award_badges(session: Session, player_id: int) -> list[dict]:
    """Return badge dicts for template: ``award_name``, ``count``, ``trophy_rel``, ``tooltip``."""
    rows = session.scalars(
        select(HistoryAward)
        .options(joinedload(HistoryAward.season))
        .where(HistoryAward.player_id == player_id)
    ).all()
    # Dedupe **within** each award name (same as League History panels). A global dedupe would
    # wrongly merge different trophies that share the same ``sheet_season=`` and ``player_id``.
    by_name_raw: dict[str, list[HistoryAward]] = defaultdict(list)
    for a in rows:
        name = (a.award_name or "").strip() or "Award"
        by_name_raw[name].append(a)
    by_name: dict[str, list[HistoryAward]] = {
        name: _collapse_same_trophy_year_history_awards(_dedupe_history_awards(group))
        for name, group in by_name_raw.items()
    }

    static_root = Path(str(current_app.static_folder or ""))
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "bowl-fantasy")
    stem_map = _build_trophy_stem_map(static_root, league_slug)

    out: list[dict] = []
    for award_name, wins in by_name.items():
        wins_sorted = sorted(wins, key=_history_award_year_sort_key, reverse=True)
        tip_parts = [f"{_display_season_for_badge(a)} · {award_name}" for a in wins_sorted]
        tooltip = "; ".join(tip_parts)
        rel = _trophy_rel_from_map(stem_map, award_name)
        out.append(
            {
                "award_name": award_name,
                "count": len(wins_sorted),
                "trophy_rel": rel,
                "tooltip": tooltip,
            }
        )

    cup_labels = _player_bowl_cup_season_labels(session, player_id)
    if cup_labels:
        n_cup = len(cup_labels)
        tip_cup = "; ".join(f"{lab} · {_BOWL_CUP_TITLE}" for lab in cup_labels)
        rel_cup = _trophy_rel_from_map(stem_map, _BOWL_CUP_TITLE)
        merged = False
        for d in out:
            if _norm_award_title(str(d["award_name"])) == _BOWL_CUP_TITLE:
                d["count"] = int(d["count"]) + n_cup
                prev = (d.get("tooltip") or "").strip()
                d["tooltip"] = "; ".join(x for x in (prev, tip_cup) if x) if prev else tip_cup
                merged = True
                break
        if not merged:
            out.append(
                {
                    "award_name": _BOWL_CUP_TITLE,
                    "count": n_cup,
                    "trophy_rel": rel_cup,
                    "tooltip": tip_cup,
                }
            )

    if not out:
        return []
    out.sort(key=lambda d: (_award_panel_sort_index(str(d["award_name"])), _norm_award_title(str(d["award_name"]))))
    return out


def team_history_award_badges(session: Session, team: Team) -> list[dict]:
    """Aggregate team trophies for the franchise hero strip (same dict shape as ``player_history_award_badges``)."""
    tid = int(team.id)
    bowl_like = HistoryAward.award_name.ilike("%bowl%cup%trophy%")
    team_id_match = HistoryAward.team_id == tid
    null_team_names = or_(
        bowl_like,
        HistoryAward.award_name.ilike("%prince%wales%"),
        HistoryAward.award_name.ilike("%campbell%trophy%"),
        HistoryAward.award_name.ilike("%boiledegg%"),
    )
    null_team_awards = and_(HistoryAward.team_id.is_(None), null_team_names)
    rows = session.scalars(
        select(HistoryAward)
        .options(joinedload(HistoryAward.season))
        .where(or_(team_id_match, null_team_awards))
    ).all()

    filtered: list[HistoryAward] = []
    for a in rows:
        if not is_team_history_award(a.award_name):
            continue
        if _cup_winning_team_db_id(session, a) != tid:
            continue
        filtered.append(a)

    by_name_raw: dict[str, list[HistoryAward]] = defaultdict(list)
    for a in filtered:
        name = (a.award_name or "").strip() or "Award"
        by_name_raw[name].append(a)
    by_name: dict[str, list[HistoryAward]] = {
        name: _collapse_same_trophy_year_history_awards(_dedupe_history_awards(group))
        for name, group in by_name_raw.items()
    }

    static_root = Path(str(current_app.static_folder or ""))
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "bowl-fantasy")
    stem_map = _build_trophy_stem_map(static_root, league_slug)

    out: list[dict] = []
    for award_name, wins in by_name.items():
        wins_sorted = sorted(wins, key=_history_award_year_sort_key, reverse=True)
        tip_parts = [f"{_display_season_for_badge(a)} · {award_name}" for a in wins_sorted]
        tooltip = "; ".join(tip_parts)
        rel = _trophy_rel_from_map(stem_map, award_name)
        out.append(
            {
                "award_name": award_name,
                "count": len(wins_sorted),
                "trophy_rel": rel,
                "tooltip": tooltip,
            }
        )

    if not out:
        return []
    out.sort(key=lambda d: (_award_panel_sort_index(str(d["award_name"])), _norm_award_title(str(d["award_name"]))))
    return out

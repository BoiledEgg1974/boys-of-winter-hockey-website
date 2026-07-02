"""Admin CRUD for historical team season rows, awards, and all-star teams."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import HistoryAllStar, HistoryAward, Player, Season, Team, TeamSeasonRecord
from app.services.history_coach_awards import (
    _parse_display_name,
    _parse_unresolved_player,
    _parse_unresolved_team,
    is_jack_adams_award,
    is_jim_gregory_award,
    is_staff_history_award,
)

HISTORY_SOURCE_ADMIN = "admin"
HISTORY_SOURCE_CSV = "csv"

_SHEET_SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")
_HISTORY_SHEET_PLACEHOLDER_FHM = "__bowl_hist_all_stars_sheet__"

ALL_STAR_SLOT_DEFAULTS: tuple[tuple[int, str], ...] = (
    (1, "G"),
    (2, "LD"),
    (3, "RD"),
    (4, "LW"),
    (5, "C"),
    (6, "RW"),
)


def normalized_award_name_key(name: str | None) -> str:
    """Case/whitespace-insensitive key for admin award dropdown choices."""
    return " ".join(str(name or "").split()).upper()


def award_name_choices_from_names(names: list[str | None]) -> list[str]:
    """Sorted, de-duplicated award names for the admin dropdown."""
    by_key: dict[str, str] = {}
    for raw in names:
        cleaned = " ".join(str(raw or "").split())
        key = normalized_award_name_key(cleaned)
        if key and key not in by_key:
            by_key[key] = cleaned
    return sorted(by_key.values(), key=lambda s: s.upper())


def sheet_season_from_notes(notes: str | None) -> str | None:
    """Parse ``sheet_season=YYYY-YY`` from award/all-star notes."""
    for part in (notes or "").split(";"):
        p = part.strip()
        if p.lower().startswith("sheet_season="):
            tok = p.split("=", 1)[1].strip().split(";")[0].strip()
            if _SHEET_SEASON_RE.match(tok):
                return tok
    return None


def _merge_notes_tag(notes: str | None, key: str, value: str) -> str:
    """Set or replace a semicolon-separated ``key=value`` tag (case-insensitive key)."""
    key_l = key.lower()
    pieces: list[str] = []
    for part in (notes or "").split(";"):
        p = part.strip()
        if not p or p.split("=", 1)[0].strip().lower() == key_l:
            continue
        pieces.append(p)
    pieces.append(f"{key}={value}")
    return "; ".join(pieces)


def _remove_notes_tag(notes: str | None, key: str) -> str:
    key_l = key.lower()
    pieces: list[str] = []
    for part in (notes or "").split(";"):
        p = part.strip()
        if not p or p.split("=", 1)[0].strip().lower() == key_l:
            continue
        pieces.append(p)
    return "; ".join(pieces)


def gm_user_id_from_award_notes(notes: str | None) -> int | None:
    for part in (notes or "").split(";"):
        p = part.strip()
        if p.lower().startswith("gm_user_id="):
            try:
                return int(p.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def gm_history_username(user: Any) -> str:
    """League username for ``unresolved_team`` / ``unresolved_player`` tags (matches CSV imports)."""
    for attr in ("username", "discord_name"):
        v = (getattr(user, attr, None) or "").strip()
        if v:
            return v
    email = (getattr(user, "email", None) or "").strip()
    return email or "—"


def apply_gm_winner_to_award_notes(
    award_name: str,
    notes: str | None,
    *,
    gm_username: str,
    gm_display: str,
    gm_user_id: int,
) -> str:
    """Stamp GM winner metadata into award notes for League History display."""
    out = _merge_notes_tag(notes, "gm_user_id", str(gm_user_id))
    out = _merge_notes_tag(out, "display_name", gm_display)
    if is_jim_gregory_award(award_name):
        out = _merge_notes_tag(out, "unresolved_team", gm_username)
        out = _remove_notes_tag(out, "unresolved_player")
    elif is_jack_adams_award(award_name):
        out = _merge_notes_tag(out, "unresolved_player", gm_username)
        out = _remove_notes_tag(out, "unresolved_team")
    elif is_staff_history_award(award_name):
        out = _merge_notes_tag(out, "unresolved_team", gm_username)
    return out


def staff_award_winner_admin_label(award: HistoryAward) -> str:
    """Short winner label for the Season Awards admin table."""
    if is_staff_history_award(award.award_name):
        if is_jim_gregory_award(award.award_name):
            label = _parse_unresolved_team(award.notes) or _parse_display_name(award.notes)
        elif is_jack_adams_award(award.award_name):
            label = _parse_unresolved_player(award.notes) or _parse_display_name(award.notes)
        else:
            label = _parse_display_name(award.notes) or _parse_unresolved_team(award.notes)
        if label:
            return label
    if (award.staff_fhm_id or "").strip():
        return (award.staff_fhm_id or "").strip()
    if award.player and (award.player.full_name or "").strip():
        return award.player.full_name.strip()
    return "—"


def merge_sheet_season_notes(notes: str | None, season_label: str) -> str:
    """Ensure ``sheet_season=`` tag is present and matches ``season_label``."""
    lab = (season_label or "").strip()
    tag = f"sheet_season={lab}" if lab else ""
    pieces: list[str] = []
    for part in (notes or "").split(";"):
        p = part.strip()
        if not p or p.lower().startswith("sheet_season="):
            continue
        pieces.append(p)
    if tag:
        pieces.insert(0, tag)
    return "; ".join(pieces) if pieces else (tag or "")


def _label_start_year(label: str | None) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d{4})", str(label))
    return int(m.group(1)) if m else None


def _season_by_label_substring(session: Session, key: str | None) -> Season | None:
    k = (key or "").strip()
    if not k or "%" in k:
        return None
    return session.scalars(select(Season).where(Season.label.like(f"%{k}%")).limit(1)).first()


def _ensure_sheet_placeholder_season(session: Session) -> Season:
    fid = _HISTORY_SHEET_PLACEHOLDER_FHM
    s = session.scalars(select(Season).where(Season.fhm_season_id == fid).limit(1)).first()
    if s:
        return s
    s = Season(
        label="Historical — all-star sheet (import anchor)",
        fhm_season_id=fid,
        start_year=None,
        end_year=None,
        is_current=False,
    )
    session.add(s)
    session.flush()
    return s


def resolve_season_for_label(session: Session, season_label: str, *, league_slug: str) -> Season | None:
    """Best-effort ``Season`` FK for admin history rows (mirrors import heuristics)."""
    key = (season_label or "").strip()
    if not key:
        return None
    s = session.scalars(select(Season).where(Season.label == key).limit(1)).first()
    if s:
        return s
    sy = _label_start_year(key)
    if sy is not None:
        candidates = list(session.scalars(select(Season).where(Season.start_year == sy)).all())
        narrow = [
            c
            for c in candidates
            if c.end_year is None or (int(c.end_year) - int(c.start_year) <= 2)
        ]
        if narrow:
            return narrow[0]
        if candidates:
            return candidates[0]
    if league_slug == "bowl-historical":
        hit = _season_by_label_substring(session, key)
        if hit:
            return hit
    all_seasons = session.scalars(select(Season)).all()
    if len(all_seasons) == 1:
        return all_seasons[0]
    if league_slug == "bowl-historical":
        return _ensure_sheet_placeholder_season(session)
    return None


def award_matches_season_label(award: HistoryAward, year_label: str) -> bool:
    sheet = sheet_season_from_notes(award.notes)
    if sheet:
        return sheet == year_label
    if award.season and (award.season.label or "").strip() == year_label:
        return True
    return False


def list_awards_for_season_label(session: Session, season_label: str) -> list[HistoryAward]:
    """All DB awards whose sheet season (or season label) matches ``season_label``."""
    rows = list(
        session.scalars(
            select(HistoryAward)
            .options(
                joinedload(HistoryAward.player),
                joinedload(HistoryAward.team),
                joinedload(HistoryAward.season),
            )
            .order_by(HistoryAward.award_name.asc(), HistoryAward.id.asc())
        ).all()
    )
    return [a for a in rows if award_matches_season_label(a, season_label)]


def list_team_season_records(
    session: Session,
    *,
    season_filter: str | None = None,
    limit: int = 500,
) -> list[TeamSeasonRecord]:
    q = (
        select(TeamSeasonRecord)
        .options(joinedload(TeamSeasonRecord.team))
        .order_by(
            TeamSeasonRecord.season_year_label.desc(),
            TeamSeasonRecord.id.desc(),
        )
    )
    if season_filter:
        q = q.where(TeamSeasonRecord.season_year_label == season_filter.strip())
    return list(session.scalars(q.limit(limit)).all())


def list_history_awards_admin(
    session: Session,
    *,
    season_filter: str | None = None,
    limit: int = 500,
) -> list[HistoryAward]:
    if season_filter:
        return list_awards_for_season_label(session, season_filter.strip())[:limit]
    return list(
        session.scalars(
            select(HistoryAward)
            .options(
                joinedload(HistoryAward.player),
                joinedload(HistoryAward.team),
                joinedload(HistoryAward.season),
            )
            .order_by(HistoryAward.id.desc())
            .limit(limit)
        ).all()
    )


def list_all_stars_admin(
    session: Session,
    *,
    season_filter: str | None = None,
    limit: int = 500,
) -> list[HistoryAllStar]:
    q = (
        select(HistoryAllStar)
        .options(
            joinedload(HistoryAllStar.player),
            joinedload(HistoryAllStar.team),
            joinedload(HistoryAllStar.season),
        )
        .order_by(
            HistoryAllStar.season_label.desc(),
            HistoryAllStar.team_rank.asc(),
            HistoryAllStar.slot.asc(),
        )
    )
    if season_filter:
        q = q.where(HistoryAllStar.season_label == season_filter.strip())
    return list(session.scalars(q.limit(limit)).all())


def _opt_int(raw: str | None) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _opt_float(raw: str | None) -> float | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _stamp_admin_row(row: Any, user_id: int | None) -> None:
    row.source = HISTORY_SOURCE_ADMIN
    row.updated_at = datetime.utcnow()
    row.updated_by_user_id = user_id


def upsert_team_season_record(
    session: Session,
    *,
    record_id: int | None,
    season_year_label: str,
    team_id: int | None,
    team_fhm_id_csv: str | None,
    team_name_override: str | None,
    conference_id: int | None,
    conference_override: str | None,
    division_id: int | None,
    division_override: str | None,
    logo_file_override: str | None,
    gp: int | None,
    w: int | None,
    l: int | None,
    t_otl: int | None,
    pts: int | None,
    gf: int | None,
    ga: int | None,
    goal_diff: int | None,
    result: str | None,
    pim_per_game: float | None,
    ppg: int | None,
    ppg_against: int | None,
    pp_chances: int | None,
    shg: int | None,
    shg_against: int | None,
    sh_chances: int | None,
    pp_pct: float | None,
    pk_pct: float | None,
    shots_for: int | None,
    shots_against: int | None,
    user_id: int | None,
) -> TeamSeasonRecord:
    label = (season_year_label or "").strip()
    if not label:
        raise ValueError("Season label is required.")
    if team_id is None and not (team_name_override or "").strip():
        raise ValueError("Select a team or enter a team name override.")
    row = session.get(TeamSeasonRecord, record_id) if record_id else None
    if row is None:
        row = TeamSeasonRecord(season_year_label=label)
    row.season_year_label = label
    row.start_year = _label_start_year(label)
    row.team_id = team_id
    row.team_fhm_id_csv = (team_fhm_id_csv or "").strip() or None
    row.team_name_override = (team_name_override or "").strip() or None
    row.conference_id = conference_id
    row.conference_override = (conference_override or "").strip() or None
    row.division_id = division_id
    row.division_override = (division_override or "").strip() or None
    row.logo_file_override = (logo_file_override or "").strip() or None
    row.gp = gp
    row.w = w
    row.l = l
    row.t_otl = t_otl
    row.pts = pts
    row.gf = gf
    row.ga = ga
    row.goal_diff = goal_diff
    row.result = (result or "").strip() or None
    row.pim_per_game = pim_per_game
    row.ppg = ppg
    row.ppg_against = ppg_against
    row.pp_chances = pp_chances
    row.shg = shg
    row.shg_against = shg_against
    row.sh_chances = sh_chances
    row.pp_pct = pp_pct
    row.pk_pct = pk_pct
    row.shots_for = shots_for
    row.shots_against = shots_against
    _stamp_admin_row(row, user_id)
    session.add(row)
    session.flush()
    return row


def delete_team_season_record(session: Session, record_id: int) -> bool:
    row = session.get(TeamSeasonRecord, record_id)
    if row is None:
        return False
    session.delete(row)
    return True


def upsert_history_award(
    session: Session,
    *,
    award_id: int | None,
    season_label: str,
    league_slug: str,
    award_name: str,
    player_id: int | None,
    team_id: int | None,
    staff_fhm_id: str | None,
    notes: str | None,
    user_id: int | None,
) -> HistoryAward:
    label = (season_label or "").strip()
    name = (award_name or "").strip()
    if not label:
        raise ValueError("Season label is required.")
    if not name:
        raise ValueError("Award name is required.")
    season = resolve_season_for_label(session, label, league_slug=league_slug)
    if season is None:
        raise ValueError(f"No season row found for {label!r}.")
    row = session.get(HistoryAward, award_id) if award_id else None
    if row is None:
        row = HistoryAward(season_id=int(season.id))
    row.season_id = int(season.id)
    row.award_name = name
    row.player_id = player_id
    row.team_id = team_id
    row.staff_fhm_id = (staff_fhm_id or "").strip() or None
    row.notes = merge_sheet_season_notes(notes, label) or None
    _stamp_admin_row(row, user_id)
    session.add(row)
    session.flush()
    return row


def delete_history_award(session: Session, award_id: int) -> bool:
    row = session.get(HistoryAward, award_id)
    if row is None:
        return False
    session.delete(row)
    return True


def upsert_all_star_slot(
    session: Session,
    *,
    row_id: int | None,
    season_label: str,
    league_slug: str,
    team_rank: int,
    slot: int,
    position: str,
    player_id: int | None,
    team_id: int | None,
    notes: str | None,
    user_id: int | None,
) -> HistoryAllStar:
    label = (season_label or "").strip()
    if not label:
        raise ValueError("Season label is required.")
    if team_rank not in (1, 2):
        raise ValueError("Team rank must be First (1) or Second (2).")
    if slot < 1 or slot > 6:
        raise ValueError("Slot must be between 1 and 6.")
    season = resolve_season_for_label(session, label, league_slug=league_slug)
    if season is None:
        raise ValueError(f"No season row found for {label!r}.")
    pos = (position or "?").strip()[:32] or "?"
    existing = None
    if row_id:
        existing = session.get(HistoryAllStar, row_id)
    if existing is None:
        existing = session.scalars(
            select(HistoryAllStar).where(
                HistoryAllStar.season_label == label,
                HistoryAllStar.team_rank == team_rank,
                HistoryAllStar.slot == slot,
            )
        ).first()
    if existing is None:
        existing = HistoryAllStar(
            season_id=int(season.id),
            season_label=label,
            team_rank=team_rank,
            slot=slot,
        )
    existing.season_id = int(season.id)
    existing.season_label = label
    existing.team_rank = team_rank
    existing.slot = slot
    existing.position = pos
    existing.player_id = player_id
    existing.team_id = team_id
    existing.notes = merge_sheet_season_notes(notes, label) or None
    _stamp_admin_row(existing, user_id)
    session.add(existing)
    session.flush()
    return existing


def delete_all_star_row(session: Session, row_id: int) -> bool:
    row = session.get(HistoryAllStar, row_id)
    if row is None:
        return False
    session.delete(row)
    return True


def season_label_choices_for_admin(session: Session) -> list[str]:
    """Distinct season labels for the awards admin season picker."""
    labels: set[str] = set()
    for raw in session.scalars(select(HistoryAllStar.season_label).distinct()).all():
        s = (raw or "").strip()
        if s:
            labels.add(s)
    for raw in session.scalars(select(TeamSeasonRecord.season_year_label).distinct()).all():
        s = (raw or "").strip()
        if s:
            labels.add(s)
    for award in session.scalars(select(HistoryAward)).all():
        sheet = sheet_season_from_notes(award.notes)
        if sheet:
            labels.add(sheet)
        elif award.season and (award.season.label or "").strip():
            labels.add((award.season.label or "").strip())
    for season in session.scalars(select(Season)).all():
        if season.start_year is not None:
            y = int(season.start_year)
            labels.add(f"{y}-{(y + 1) % 100:02d}")
        elif (season.label or "").strip():
            labels.add((season.label or "").strip())
    return sorted(labels, reverse=True)


def all_stars_by_slot_for_season(
    session: Session,
    season_label: str,
    team_rank: int,
) -> dict[int, HistoryAllStar]:
    rows = list_all_stars_admin(session, season_filter=season_label.strip())
    return {
        int(r.slot): r
        for r in rows
        if int(r.team_rank) == int(team_rank)
    }


def save_all_star_batch(
    session: Session,
    form,
    *,
    season_label: str,
    league_slug: str,
    team_rank: int,
    user_id: int,
) -> tuple[int, list[str]]:
    """Upsert filled all-star slots from admin form fields ``player_id_N``, etc."""
    saved = 0
    errors: list[str] = []
    for slot_num, default_pos in ALL_STAR_SLOT_DEFAULTS:
        pos = (form.get(f"position_{team_rank}_{slot_num}") or default_pos).strip()
        pid_raw = (form.get(f"player_id_{team_rank}_{slot_num}") or "").strip()
        tid_raw = (form.get(f"team_id_{team_rank}_{slot_num}") or "").strip()
        notes_slot = (form.get(f"notes_{team_rank}_{slot_num}") or "").strip() or None
        if not pid_raw and not tid_raw and not notes_slot:
            continue
        try:
            player_id = int(pid_raw) if pid_raw else None
        except ValueError:
            player_id = None
        try:
            team_id = int(tid_raw) if tid_raw else None
        except ValueError:
            team_id = None
        try:
            upsert_all_star_slot(
                session,
                row_id=None,
                season_label=season_label,
                league_slug=league_slug,
                team_rank=team_rank,
                slot=slot_num,
                position=pos,
                player_id=player_id,
                team_id=team_id,
                notes=notes_slot,
                user_id=user_id,
            )
            saved += 1
        except ValueError as exc:
            errors.append(str(exc))
    return saved, errors


def delete_non_admin_history_awards(session: Session) -> int:
    """Remove CSV-sourced awards before a full CSV re-import."""
    result = session.execute(
        delete(HistoryAward).where(
            or_(
                HistoryAward.source.is_(None),
                HistoryAward.source != HISTORY_SOURCE_ADMIN,
            )
        )
    )
    return int(result.rowcount or 0)


def delete_non_admin_all_stars(session: Session) -> int:
    result = session.execute(
        delete(HistoryAllStar).where(
            or_(
                HistoryAllStar.source.is_(None),
                HistoryAllStar.source != HISTORY_SOURCE_ADMIN,
            )
        )
    )
    return int(result.rowcount or 0)


def delete_non_admin_team_season_records(session: Session) -> int:
    result = session.execute(
        delete(TeamSeasonRecord).where(
            or_(
                TeamSeasonRecord.source.is_(None),
                TeamSeasonRecord.source != HISTORY_SOURCE_ADMIN,
            )
        )
    )
    return int(result.rowcount or 0)


def parse_team_season_form(form: Any) -> dict[str, Any]:
    """Extract team season record fields from a Flask form."""
    team_id = _opt_int(form.get("team_id"))
    team_fhm = (form.get("team_fhm_id_csv") or "").strip() or None
    return {
        "season_year_label": (form.get("season_year_label") or "").strip(),
        "team_id": team_id,
        "team_fhm_id_csv": team_fhm,
        "team_name_override": (form.get("team_name_override") or "").strip() or None,
        "conference_id": _opt_int(form.get("conference_id")),
        "conference_override": (form.get("conference_override") or "").strip() or None,
        "division_id": _opt_int(form.get("division_id")),
        "division_override": (form.get("division_override") or "").strip() or None,
        "logo_file_override": (form.get("logo_file_override") or "").strip() or None,
        "gp": _opt_int(form.get("gp")),
        "w": _opt_int(form.get("w")),
        "l": _opt_int(form.get("l")),
        "t_otl": _opt_int(form.get("t_otl")),
        "pts": _opt_int(form.get("pts")),
        "gf": _opt_int(form.get("gf")),
        "ga": _opt_int(form.get("ga")),
        "goal_diff": _opt_int(form.get("goal_diff")),
        "result": (form.get("result") or "").strip() or None,
        "pim_per_game": _opt_float(form.get("pim_per_game")),
        "ppg": _opt_int(form.get("ppg")),
        "ppg_against": _opt_int(form.get("ppg_against")),
        "pp_chances": _opt_int(form.get("pp_chances")),
        "shg": _opt_int(form.get("shg")),
        "shg_against": _opt_int(form.get("shg_against")),
        "sh_chances": _opt_int(form.get("sh_chances")),
        "pp_pct": _opt_float(form.get("pp_pct")),
        "pk_pct": _opt_float(form.get("pk_pct")),
        "shots_for": _opt_int(form.get("shots_for")),
        "shots_against": _opt_int(form.get("shots_against")),
    }

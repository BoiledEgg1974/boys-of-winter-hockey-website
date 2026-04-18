"""Resolve Jack Adams (coach) history rows against FHM ``staff_master`` / ``staff_ratings``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoryAward, Team
from app.services.team_staff_csv import _read_staff_ratings_by_id, _staff_role_bucket
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int


@dataclass(frozen=True)
class CoachAwardDisplay:
    """Display-only coach row for League History (not a :class:`~app.models.Player`)."""

    full_name: str
    team: Team | None


_JACK_ADAMS_NORM = "JACK ADAMS TROPHY"
_JIM_GREGORY_NORM = "JIM GREGORY TROPHY"


def is_jack_adams_award(award_name: str | None) -> bool:
    return " ".join((award_name or "").upper().split()) == _JACK_ADAMS_NORM


def is_jim_gregory_award(award_name: str | None) -> bool:
    return " ".join((award_name or "").upper().split()) == _JIM_GREGORY_NORM


def is_staff_history_award(award_name: str | None) -> bool:
    """Awards that use ``coach_display`` (staff from ``staff_master`` / FHM staff id)."""
    return is_jack_adams_award(award_name) or is_jim_gregory_award(award_name)


def _parse_unresolved_player(notes: str | None) -> str | None:
    if not notes:
        return None
    for part in notes.split(";"):
        p = part.strip()
        if p.lower().startswith("unresolved_player="):
            return p.split("=", 1)[1].strip() or None
    return None


def _parse_unresolved_team(notes: str | None) -> str | None:
    """Wide trophy sheets label the GM column as *Team Name*; values are often league usernames."""
    if not notes:
        return None
    for part in notes.split(";"):
        p = part.strip()
        if p.lower().startswith("unresolved_team="):
            return p.split("=", 1)[1].strip() or None
    return None


def _staff_display_name(m: dict) -> str:
    """First + last only (no ``nick_name``) so History panels stay clean, e.g. Jack Adams not Jack Adams “Trader Jack”."""
    fn = (cell_val(m, "first_name") or "").strip()
    ln = (cell_val(m, "last_name") or "").strip()
    return f"{fn} {ln}".strip() or "—"


def _norm_person_key(s: str) -> str:
    return " ".join(s.lower().split())


def _coach_candidates(raw_dir: Path) -> list[dict]:
    """All staff rows whose primary role is coach (includes retired and team -1 for history lookup)."""
    mp = raw_dir / "staff_master.csv"
    rp = raw_dir / "staff_ratings.csv"
    if not mp.is_file() or not rp.is_file():
        return []
    ratings_by_id = _read_staff_ratings_by_id(rp)
    out: list[dict] = []
    master_df = read_csv_normalized(mp)
    for _, mrow in master_df.iterrows():
        m = mrow.to_dict()
        sid = cell_val(m, "staffid")
        if not sid:
            continue
        rr = ratings_by_id.get(str(sid).strip())
        if _staff_role_bucket(rr) != "coaches":
            continue
        tid_raw = cell_val(m, "teamid")
        tid = None if tid_raw is None else str(tid_raw).strip()
        if tid in ("", "-1"):
            tid = None
        retired = to_int(cell_val(m, "retired"), 0) == 1
        co = to_int(cell_val(rr, "coach"), 0) or 0 if rr else 0
        out.append(
            {
                "staffid": str(sid).strip(),
                "teamid": tid,
                "full_name": _staff_display_name(m),
                "first": (cell_val(m, "first_name") or "").strip().lower(),
                "last": (cell_val(m, "last_name") or "").strip().lower(),
                "retired": retired,
                "coach_rating": co,
            }
        )
    return out


def _pick_best_match(candidates: list[dict], query: str) -> dict | None:
    q = query.strip()
    if not q:
        return None
    q_key = _norm_person_key(q)
    q_parts = q_key.split()
    exact = [c for c in candidates if _norm_person_key(c["full_name"]) == q_key]
    if exact:
        cand = exact
    else:
        # First token = first name, rest = last name (e.g. "Scotty Bowman")
        if len(q_parts) >= 2:
            f0, l0 = q_parts[0], " ".join(q_parts[1:])
            cand = [c for c in candidates if c["first"] == f0 and c["last"] == l0]
        else:
            cand = [c for c in candidates if q_key in _norm_person_key(c["full_name"])]
    if not cand:
        return None
    cand.sort(key=lambda c: (c["retired"], -int(c["coach_rating"] or 0), int(c["staffid"])))
    return cand[0]


def _staff_row_by_fhm_id(raw_dir: Path, staff_fhm_id: str) -> dict | None:
    """Look up any staff row in ``staff_master.csv`` by FHM ``StaffId`` (includes retired, any role)."""
    sid_target = (staff_fhm_id or "").strip()
    if not sid_target:
        return None
    mp = raw_dir / "staff_master.csv"
    if not mp.is_file():
        return None
    master_df = read_csv_normalized(mp)
    for _, mrow in master_df.iterrows():
        m = mrow.to_dict()
        sid = cell_val(m, "staffid")
        if not sid or str(sid).strip() != sid_target:
            continue
        tid_raw = cell_val(m, "teamid")
        tid = None if tid_raw is None else str(tid_raw).strip()
        if tid in ("", "-1"):
            tid = None
        retired = to_int(cell_val(m, "retired"), 0) == 1
        return {
            "staffid": sid_target,
            "teamid": tid,
            "full_name": _staff_display_name(m),
            "retired": retired,
        }
    return None


def _parse_display_name(notes: str | None) -> str | None:
    """Optional ``display_name=…`` in ``notes`` (panel text when set)."""
    if not notes:
        return None
    for part in notes.split(";"):
        p = part.strip()
        if p.lower().startswith("display_name="):
            return p.split("=", 1)[1].strip() or None
    return None


def _jack_adams_csv_label(notes: str | None) -> str:
    """Panel coach text from CSV ``notes``: ``display_name=``, then ``unresolved_player=``, then ``unresolved_team=``."""
    return (
        (_parse_display_name(notes) or _parse_unresolved_player(notes) or _parse_unresolved_team(notes) or "").strip()
    )


def resolve_staff_history_display(
    session: Session,
    award: HistoryAward,
    coach_candidates: list[dict] | None,
    raw_dir: Path,
) -> CoachAwardDisplay | None:
    """Resolve display for staff history awards.

    Jack Adams: ``staff_fhm_id`` (CSV ``staff_id``) looks up ``staff_master`` for team linkage; the
    visible coach name prefers ``display_name=`` / ``unresolved_player=`` / ``unresolved_team=`` in
    ``notes`` when set (e.g. ``Joey``), otherwise the staff card first+last. Team prefers ``award.team``
    from CSV ``team_id``, then the staff row's FHM team.
    """
    if not is_staff_history_award(award.award_name):
        return None
    sid = (getattr(award, "staff_fhm_id", None) or "").strip()
    if sid:
        hit = _staff_row_by_fhm_id(raw_dir, sid)
        if hit:
            team_staff = None
            tid = hit.get("teamid")
            if tid:
                team_staff = session.scalars(select(Team).where(Team.fhm_team_id == str(tid)).limit(1)).first()
            label = _jack_adams_csv_label(award.notes) if is_jack_adams_award(award.award_name) else ""
            name_out = label if label else hit["full_name"]
            team_out = getattr(award, "team", None) or team_staff
            return CoachAwardDisplay(full_name=name_out, team=team_out)
    if is_jack_adams_award(award.award_name):
        cd = resolve_jack_adams_coach(session, award, coach_candidates)
        if cd:
            return cd
        q = _jack_adams_csv_label(award.notes)
        if q:
            return CoachAwardDisplay(full_name=q, team=getattr(award, "team", None))
        return None
    if is_jim_gregory_award(award.award_name):
        q = (_parse_unresolved_team(award.notes) or _parse_unresolved_player(award.notes) or "").strip()
        if q:
            return CoachAwardDisplay(full_name=q, team=getattr(award, "team", None))
    return None


def resolve_jack_adams_coach(
    session: Session,
    award: HistoryAward,
    candidates: list[dict] | None,
) -> CoachAwardDisplay | None:
    """Match ``unresolved_player=…`` or the linked player's name to staff_master coaches for team."""
    if not is_jack_adams_award(award.award_name):
        return None
    if not candidates:
        return None
    label = _jack_adams_csv_label(award.notes)
    q = label
    if not q and award.player and (award.player.full_name or "").strip():
        q = award.player.full_name.strip()
    if not q:
        return None
    hit = _pick_best_match(candidates, q)
    if not hit:
        return None
    team_staff = None
    tid = hit.get("teamid")
    if tid:
        team_staff = session.scalars(select(Team).where(Team.fhm_team_id == str(tid)).limit(1)).first()
    team_out = getattr(award, "team", None) or team_staff
    name_out = label if label else hit["full_name"]
    return CoachAwardDisplay(full_name=name_out, team=team_out)


def attach_coach_award_displays(awards: list[HistoryAward], session: Session, raw_dir: Path) -> None:
    """Set ``award.coach_display`` on Jack Adams / Jim Gregory rows (staff id or name fallback)."""
    raw_dir = Path(raw_dir)
    candidates: list[dict] | None = None
    for a in awards:
        if not is_staff_history_award(a.award_name):
            continue
        if is_jack_adams_award(a.award_name) and candidates is None:
            candidates = _coach_candidates(raw_dir)
        cd = resolve_staff_history_display(session, a, candidates, raw_dir)
        if is_jack_adams_award(a.award_name):
            # Notes label must win over ``player.full_name`` in templates (otherwise ``Joey`` → link shows
            # ``Joey Fortin`` when ``player_id`` matches that skater).
            lab = _jack_adams_csv_label(a.notes)
            if lab:
                team_t = getattr(a, "team", None) or (cd.team if cd else None)
                a.coach_display = CoachAwardDisplay(full_name=lab, team=team_t)  # type: ignore[attr-defined]
            else:
                a.coach_display = cd  # type: ignore[attr-defined]
        else:
            a.coach_display = cd  # type: ignore[attr-defined]

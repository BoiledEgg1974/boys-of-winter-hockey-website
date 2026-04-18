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


def resolve_staff_history_display(
    session: Session,
    award: HistoryAward,
    coach_candidates: list[dict] | None,
    raw_dir: Path,
) -> CoachAwardDisplay | None:
    """Resolve ``HistoryAward.staff_fhm_id`` or Jack Adams name fallback to :class:`CoachAwardDisplay`."""
    if not is_staff_history_award(award.award_name):
        return None
    sid = (getattr(award, "staff_fhm_id", None) or "").strip()
    if sid:
        hit = _staff_row_by_fhm_id(raw_dir, sid)
        if hit:
            team = None
            tid = hit.get("teamid")
            if tid:
                team = session.scalars(select(Team).where(Team.fhm_team_id == str(tid)).limit(1)).first()
            return CoachAwardDisplay(full_name=hit["full_name"], team=team)
    if is_jack_adams_award(award.award_name):
        return resolve_jack_adams_coach(session, award, coach_candidates)
    return None


def resolve_jack_adams_coach(
    session: Session,
    award: HistoryAward,
    candidates: list[dict] | None,
) -> CoachAwardDisplay | None:
    """Match ``unresolved_player=…`` or the linked player's name to staff_master coaches."""
    if not is_jack_adams_award(award.award_name):
        return None
    if not candidates:
        return None
    q = _parse_unresolved_player(award.notes)
    if not q and award.player and (award.player.full_name or "").strip():
        q = award.player.full_name.strip()
    if not q:
        return None
    hit = _pick_best_match(candidates, q)
    if not hit:
        return None
    team = None
    tid = hit.get("teamid")
    if tid:
        team = session.scalars(select(Team).where(Team.fhm_team_id == str(tid)).limit(1)).first()
    return CoachAwardDisplay(full_name=hit["full_name"], team=team)


def attach_coach_award_displays(awards: list[HistoryAward], session: Session, raw_dir: Path) -> None:
    """Set ``award.coach_display`` on Jack Adams / Jim Gregory rows (staff id or name fallback)."""
    raw_dir = Path(raw_dir)
    candidates: list[dict] | None = None
    for a in awards:
        if not is_staff_history_award(a.award_name):
            continue
        if is_jack_adams_award(a.award_name) and not (getattr(a, "staff_fhm_id", None) or "").strip():
            if candidates is None:
                candidates = _coach_candidates(raw_dir)
        cd = resolve_staff_history_display(session, a, candidates, raw_dir)
        a.coach_display = cd  # type: ignore[attr-defined]

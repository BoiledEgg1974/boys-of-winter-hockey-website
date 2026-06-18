"""BOWL Six weekly pick game — slates, lineups, scoring, AP prizes."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.auth_login import active_membership_for_league
from app.league_db import db
from app.models import Game, Player, Team
from app.services.homepage_dashboard import league_calendar_anchor_date
from app.services.postseason_odds import _is_regular_season_game

_log = logging.getLogger(__name__)
from app.services.ap_service import add_ledger_entry
from app.services.bowl_six_scoring import (
    SLOT_ORDER,
    dumps_points,
    position_kind,
    score_lineup_for_slate,
    slot_accepts_position,
)
from app.services.player_snapshot_card import build_player_snapshot_card
from app.services.league_rules import get_rule_value, rule_bool
from app.services.seasons import get_current_season
from app.site_models import (
    ApLedgerEntry,
    BowlSixGameFinal,
    BowlSixLineup,
    BowlSixLineupPick,
    BowlSixLineupScore,
    BowlSixPlayerWeekStat,
    BowlSixSlate,
    GmInAppNotification,
    GmLeagueMembership,
    User,
)

AP_PRIZES = {1: 10, 2: 6, 3: 3}
SEASON_AP_PRIZES = {1: 30, 2: 20, 3: 10}
SEASON_PARTICIPATION_AP = 2
BOWL_SIX_ROSTERS_UNLOCKED_EVENT_KEY = "bowl_six_rosters_unlocked"
BOWL_SIX_LOCK_WARNING_EVENT_KEY = "bowl_six_lock_warning"
BOWL_SIX_LOCK_TZ = ZoneInfo("America/New_York")
BOWL_SIX_LOCK_TZ_LABEL = "ET"
BOWL_SIX_REAL_WEEK_START_DOW = 0  # Monday
BOWL_SIX_DEFAULT_LOCK_TIME_ET = "19:59"
SLOT_LABELS = {
    "gk": "GK",
    "def1": "DEF",
    "def2": "DEF",
    "fwd1": "FWD",
    "fwd2": "FWD",
    "fwd3": "FWD",
}


@dataclass
class LineupValidation:
    ok: bool
    message: str = ""


def bowl_six_enabled(session: Session, league_slug: str) -> bool:
    return rule_bool(session, league_slug, "bowl_six_enabled", default=True)


def season_ap_prize_for_rank(rank: int) -> int:
    """Projected season-end BOWL Six AP prize for a standings rank."""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0
    return SEASON_AP_PRIZES.get(r, SEASON_PARTICIPATION_AP if r > 0 else 0)


def _week_bounds_for_date(d: date, week_start_dow: int) -> tuple[date, date]:
    """Monday=0 .. Sunday=6 style offset from rule (default Monday=0)."""
    dow = int(d.weekday())
    start_dow = int(week_start_dow) % 7
    delta = (dow - start_dow) % 7
    week_start = d - timedelta(days=delta)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _real_bowl_six_week_bounds(now_utc: datetime | None = None) -> tuple[date, date]:
    """Real-world GM competition week: Monday through Sunday, Eastern time."""
    now = now_utc or utcnow_naive()
    today_et = eastern_naive_from_utc_naive(now).date()
    return _week_bounds_for_date(today_et, BOWL_SIX_REAL_WEEK_START_DOW)


def bowl_six_real_season_bounds(today: date | None = None) -> tuple[date, date]:
    """Real-world BOWL Six season window (July 1 through June 30)."""
    if today is None:
        today = eastern_naive_from_utc_naive(utcnow_naive()).date()
    start_year = int(today.year) if int(today.month) >= 7 else int(today.year) - 1
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def _current_scoring_week_bounds(league_session: Session) -> tuple[date, date]:
    """Real-world week used to score BOWL Six slates."""
    return _real_bowl_six_week_bounds()


def slate_scoring_week_bounds(slate: BowlSixSlate) -> tuple[date, date]:
    """Display/scoring dates for BOWL Six: always the real-world slate week."""
    return slate.week_start, slate.week_end


def sync_slate_week_to_league_calendar(
    session: Session,
    league_session: Session,
    league_slug: str,
    slate: BowlSixSlate,
) -> bool:
    """Legacy compatibility shim: fill missing scoring bounds without moving slates."""
    if slate.status in ("scored", "skipped"):
        return False
    changed = False
    if slate.scoring_week_start is None:
        slate.scoring_week_start = slate.week_start
        changed = True
    if slate.scoring_week_end is None:
        slate.scoring_week_end = slate.week_end
        changed = True
    if not changed:
        return False
    return True


def _parse_lock_time(rule_val: str) -> time:
    raw = (rule_val or "00:00").strip()
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h % 24, m % 60)
    except (ValueError, IndexError):
        return time(0, 0)


def utcnow_naive() -> datetime:
    """Naive UTC wall time for lock comparisons and storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _combine_date_time_form(date_str: str, time_str: str) -> datetime | None:
    d = (date_str or "").strip()
    if not d:
        return None
    t = (time_str or "00:00").strip() or "00:00"
    try:
        day = date.fromisoformat(d)
        parts = t.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return datetime.combine(day, time(hour % 24, minute % 60))
    except (ValueError, TypeError):
        return None


def utc_naive_from_eastern(naive_eastern: datetime) -> datetime:
    """Convert Eastern wall time to naive UTC for DB storage."""
    aware = naive_eastern.replace(tzinfo=BOWL_SIX_LOCK_TZ)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def eastern_naive_from_utc_naive(utc_naive: datetime) -> datetime:
    """Convert stored naive UTC to Eastern wall time for forms/display."""
    aware_utc = utc_naive.replace(tzinfo=timezone.utc)
    return aware_utc.astimezone(BOWL_SIX_LOCK_TZ).replace(tzinfo=None)


def parse_lock_at_eastern_form(date_str: str, time_str: str = "00:00") -> datetime | None:
    """Admin date + time in US Eastern; returns naive UTC for storage."""
    naive = _combine_date_time_form(date_str, time_str)
    if naive is None:
        return None
    return utc_naive_from_eastern(naive)


def parse_lock_at_utc_form(date_str: str, time_str: str = "00:00") -> datetime | None:
    """Backward-compatible alias; treats form values as Eastern."""
    return parse_lock_at_eastern_form(date_str, time_str)


def parse_lock_at_utc_iso(raw: str) -> datetime | None:
    """Parse ``2026-05-19T20:00`` or ``2026-05-19T20:00:00Z`` as UTC naive."""
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "").replace("z", "")
    if len(text) == 16:
        text += ":00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def lock_at_iso_z(lock_at: datetime | None) -> str | None:
    if lock_at is None:
        return None
    return lock_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_at_display_eastern(lock_at: datetime | None) -> str:
    if lock_at is None:
        return "—"
    et = eastern_naive_from_utc_naive(lock_at)
    hour = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return f"{et.strftime('%a %b %d, %Y')} {hour}:{et.minute:02d} {ampm} {BOWL_SIX_LOCK_TZ_LABEL}"


def lock_at_eastern_form_values(lock_at: datetime | None) -> dict[str, str]:
    if lock_at is None:
        return {"lock_date": "", "lock_time": ""}
    et = eastern_naive_from_utc_naive(lock_at)
    return {
        "lock_date": et.strftime("%Y-%m-%d"),
        "lock_time": et.strftime("%H:%M"),
    }


def slate_lock_ui(slate: BowlSixSlate | None) -> dict[str, Any]:
    """Hub/lineup lock banner: countdown vs locked message."""
    empty: dict[str, Any] = {
        "show_countdown": False,
        "lock_iso": None,
        "lock_display": "",
        "banner_label": "",
        "banner_value": "",
    }
    if slate is None or slate.status == "skipped":
        return empty
    lock_iso = lock_at_iso_z(slate.lock_at)
    lock_display = lock_at_display_eastern(slate.lock_at)
    if slate.status == "scored":
        return {
            **empty,
            "lock_iso": lock_iso,
            "lock_display": lock_display,
            "banner_label": "Week status",
            "banner_value": "Complete",
        }
    if not lock_time_is_future(slate):
        return {
            "show_countdown": False,
            "lock_iso": lock_iso,
            "lock_display": lock_display,
            "banner_label": "Lineups locked",
            "banner_value": lock_display,
        }
    return {
        "show_countdown": True,
        "lock_iso": lock_iso,
        "lock_display": lock_display,
        "banner_label": "Lineup locks in",
        "banner_value": "",
    }


def default_lock_at(week_start: date, league_slug: str, session: Session) -> datetime:
    raw = get_rule_value(session, league_slug, "bowl_six_lock_time_et", None)
    if not raw:
        raw = get_rule_value(session, league_slug, "bowl_six_lock_time_utc", BOWL_SIX_DEFAULT_LOCK_TIME_ET)
    if str(raw or "").strip() in {"", "00:00"}:
        raw = BOWL_SIX_DEFAULT_LOCK_TIME_ET
    # Align all leagues to the current BOWL Six lock standard. Older installs
    # stored the previous 8:00 PM default as a rule row.
    if str(raw or "").strip() == "20:00":
        raw = BOWL_SIX_DEFAULT_LOCK_TIME_ET
    lock_t = _parse_lock_time(raw)
    return utc_naive_from_eastern(datetime.combine(week_start, lock_t))


def slate_award_at(slate: BowlSixSlate) -> datetime:
    """Naive UTC instant for automatic AP payout: Monday 12:00 AM ET after slate week."""
    award_day = slate.week_end + timedelta(days=1)
    return utc_naive_from_eastern(datetime.combine(award_day, time(0, 0)))


def slate_award_time_reached(slate: BowlSixSlate) -> bool:
    return utcnow_naive() >= slate_award_at(slate)


def _slate_unlock_at(slate: BowlSixSlate) -> datetime:
    """Naive UTC Monday 12:00 AM ET for the slate week."""
    return utc_naive_from_eastern(datetime.combine(slate.week_start, time(0, 0)))


def _slate_lock_warning_at(slate: BowlSixSlate) -> datetime:
    """Naive UTC instant for the 30-minute lock warning."""
    return slate.lock_at - timedelta(minutes=30)


def lock_time_is_future(slate: BowlSixSlate) -> bool:
    return utcnow_naive() < slate.lock_at


def slate_real_scoring_window_utc(slate: BowlSixSlate) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end for real-time BOWL Six scoring."""
    # Normal slates begin immediately after the Monday 7:59 PM ET lock. A
    # per-slate scoring date override starts at midnight on its first date.
    scoring_start = slate.scoring_week_start or slate.week_start
    scoring_end = slate.scoring_week_end or slate.week_end
    start_time = time(20, 0) if scoring_start == slate.week_start else time(0, 0)
    start_et = datetime.combine(scoring_start, start_time)
    end_et = datetime.combine(scoring_end + timedelta(days=1), time(0, 0))
    return utc_naive_from_eastern(start_et), utc_naive_from_eastern(end_et)


def record_bowl_six_game_finals(
    session: Session,
    league_session: Session,
    *,
    league_slug: str,
    game_ids: set[int] | list[int],
    observed_at: datetime | None = None,
) -> int:
    """Persist real-time first-final markers for games that just became final."""
    slug = str(league_slug or "").strip()
    ids = {int(gid) for gid in game_ids if gid}
    if not slug or not ids:
        return 0
    existing = {
        int(row.game_id)
        for row in session.scalars(
            select(BowlSixGameFinal).where(
                BowlSixGameFinal.league_slug == slug,
                BowlSixGameFinal.game_id.in_(ids),
            )
        ).all()
    }
    missing = ids - existing
    if not missing:
        return 0
    seen_at = observed_at or utcnow_naive()
    games = {
        int(g.id): g
        for g in league_session.scalars(select(Game).where(Game.id.in_(missing))).all()
    }
    n = 0
    for gid in missing:
        game = games.get(int(gid))
        if game is None or (game.status or "").lower() != "final":
            continue
        session.add(
            BowlSixGameFinal(
                league_slug=slug,
                game_id=int(gid),
                season_id=int(game.season_id) if game.season_id else None,
                fhm_game_id=str(game.fhm_game_id or "") or None,
                first_final_at=seen_at,
            )
        )
        n += 1
    if n:
        session.flush()
    return n


def sync_slate_lock_status(session: Session, slate: BowlSixSlate) -> None:
    if slate.status in ("scored", "skipped"):
        return
    now = utcnow_naive()
    if now < slate.lock_at and slate.status == "locked":
        slate.status = "open"
        return
    if slate.status == "open" and now >= slate.lock_at:
        slate.status = "locked"


def unlock_slate_for_edits(
    slate: BowlSixSlate,
    *,
    minimum_open_for: timedelta = timedelta(hours=2),
    now: datetime | None = None,
) -> bool:
    """Reopen a slate and make sure its lock deadline still permits edits."""
    slate.status = "open"
    current = now or utcnow_naive()
    if slate.lock_at is None or current >= slate.lock_at:
        slate.lock_at = current + minimum_open_for
        return True
    return False


def _gm_role_mention_for_league(session: Session, league_slug: str) -> str:
    try:
        from app.services.discord_events import get_league_bot_config

        cfg = get_league_bot_config(session, league_slug)
        rid = str(getattr(cfg, "gm_role_id", "") or "").strip()
    except Exception:
        rid = ""
    return f"<@&{rid}>" if rid else "@GM"


def _bowl_six_reminder_payload(
    session: Session,
    slate: BowlSixSlate,
    *,
    event_key: str,
) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url

    league_slug = str(slate.league_slug or "")
    role = _gm_role_mention_for_league(session, league_slug)
    hub_url = build_league_public_url(league_slug, "/bowl-six") or f"/{league_slug}/bowl-six"
    lock_display = lock_at_display_eastern(slate.lock_at)
    week_label = str(slate.label or "").strip() or f"Week of {slate.week_start.isoformat()}"
    if event_key == BOWL_SIX_ROSTERS_UNLOCKED_EVENT_KEY:
        title = "BOWL Six rosters are unlocked"
        body = (
            f"{role} BOWL Six rosters are unlocked for {week_label}.\n"
            f"Lineups lock Monday at {lock_display}.\n{hub_url}"
        )
    else:
        title = "BOWL Six rosters lock in 30 minutes"
        body = (
            f"{role} Final reminder: BOWL Six rosters lock in 30 minutes "
            f"at {lock_display}.\n{hub_url}"
        )
    return {
        "title": title,
        "body": body,
        "body_preview": body[:280],
        "url": hub_url,
        "slate_id": int(slate.id),
        "week_start": slate.week_start.isoformat() if slate.week_start else "",
        "week_label": week_label,
        "lock_display": lock_display,
        "gm_role_mention": role,
        "has_image": False,
    }


def maybe_enqueue_bowl_six_roster_reminders(
    session: Session,
    league_slug: str,
    *,
    now: datetime | None = None,
) -> int:
    """Queue unlock and 30-minute lock reminders once per active weekly slate."""
    if not bowl_six_enabled(session, league_slug):
        return 0
    slate = get_or_create_current_slate(session, league_slug)
    if slate is None or slate.status in ("scored", "skipped"):
        return 0
    now_utc = now or utcnow_naive()
    queued = 0
    from app.services.discord_events import enqueue_discord_event

    reminders = (
        (
            BOWL_SIX_ROSTERS_UNLOCKED_EVENT_KEY,
            _slate_unlock_at(slate),
            f"bowl_six:slate:{int(slate.id)}:rosters_unlocked",
        ),
        (
            BOWL_SIX_LOCK_WARNING_EVENT_KEY,
            _slate_lock_warning_at(slate),
            f"bowl_six:slate:{int(slate.id)}:lock_warning_30",
        ),
    )
    for event_key, trigger_at, source_id in reminders:
        if now_utc < trigger_at:
            continue
        if event_key == BOWL_SIX_LOCK_WARNING_EVENT_KEY and now_utc >= slate.lock_at:
            continue
        row = enqueue_discord_event(
            session,
            league_slug=league_slug,
            event_key=event_key,
            payload=_bowl_six_reminder_payload(session, slate, event_key=event_key),
            created_by_user_id=None,
            source_type="bowl_six_roster_reminder",
            source_id=source_id,
        )
        if row is not None:
            queued += 1
    return queued


def extend_slate_lock_at(
    session: Session,
    *,
    league_slug: str,
    slate_id: int,
    lock_date: str,
    lock_time: str,
) -> tuple[bool, str]:
    """Set lock deadline from Eastern date/time fields. Returns (ok, message)."""
    lock_at = parse_lock_at_eastern_form(lock_date, lock_time)
    if lock_at is None:
        return False, "Invalid lock date or time."
    slate = session.scalar(
        select(BowlSixSlate).where(
            BowlSixSlate.id == int(slate_id),
            BowlSixSlate.league_slug == league_slug,
        )
    )
    if slate is None:
        return False, "Invalid slate for this league."
    slate.lock_at = lock_at
    sync_slate_lock_status(session, slate)
    session.flush()
    return True, lock_at_display_eastern(lock_at)


def get_slate(session: Session, slate_id: int) -> BowlSixSlate | None:
    return session.get(BowlSixSlate, int(slate_id))


def list_slates(session: Session, league_slug: str, *, limit: int = 20) -> list[BowlSixSlate]:
    return list(
        session.scalars(
            select(BowlSixSlate)
            .where(BowlSixSlate.league_slug == league_slug)
            .order_by(BowlSixSlate.week_start.desc())
            .limit(limit)
        ).all()
    )


def get_or_create_current_slate(
    session: Session,
    league_slug: str,
    league_session: Session | None = None,
) -> BowlSixSlate | None:
    if not bowl_six_enabled(session, league_slug):
        return None
    league_session = league_session or db.session
    week_start, week_end = _real_bowl_six_week_bounds()
    scoring_start, scoring_end = _current_scoring_week_bounds(league_session)
    slate = session.scalar(
        select(BowlSixSlate)
        .where(
            BowlSixSlate.league_slug == league_slug,
            BowlSixSlate.week_start == week_start,
        )
        .limit(1)
    )
    if slate is None:
        slate = BowlSixSlate(
            league_slug=league_slug,
            week_start=week_start,
            week_end=week_end,
            scoring_week_start=scoring_start,
            scoring_week_end=scoring_end,
            lock_at=default_lock_at(week_start, league_slug, session),
            status="open",
            label=f"Week of {week_start.isoformat()}",
        )
        session.add(slate)
        session.flush()
    if slate.week_start == week_start and slate.week_end == week_end:
        legacy_midnight_lock = utc_naive_from_eastern(datetime.combine(week_start, time(0, 0)))
        legacy_8pm_lock = utc_naive_from_eastern(datetime.combine(week_start, time(20, 0)))
        if slate.lock_at in (legacy_midnight_lock, legacy_8pm_lock):
            slate.lock_at = default_lock_at(week_start, league_slug, session)
        if slate.scoring_week_start is None or slate.scoring_week_end is None:
            slate.scoring_week_start = scoring_start
            slate.scoring_week_end = scoring_end
    sync_slate_week_to_league_calendar(session, league_session, league_slug, slate)
    sync_slate_lock_status(session, slate)
    return slate


def ensure_current_slate_after_finalization(
    session: Session,
    league_session: Session,
    finalized_slate: BowlSixSlate,
) -> BowlSixSlate | None:
    """After scoring an old week, open the current week with an empty slate."""
    week_start, _week_end = _real_bowl_six_week_bounds()
    if week_start <= finalized_slate.week_start:
        return None
    return get_or_create_current_slate(
        session,
        str(finalized_slate.league_slug or ""),
        league_session=league_session,
    )


def prior_submitted_slate_for_user(
    session: Session, league_slug: str, user_id: int, before_slate: BowlSixSlate
) -> BowlSixSlate | None:
    return session.scalar(
        select(BowlSixSlate)
        .join(BowlSixLineup, BowlSixLineup.slate_id == BowlSixSlate.id)
        .where(
            BowlSixSlate.league_slug == league_slug,
            BowlSixSlate.status != "skipped",
            BowlSixSlate.week_start < before_slate.week_start,
            BowlSixLineup.user_id == int(user_id),
            BowlSixLineup.submitted_at.is_not(None),
        )
        .order_by(BowlSixSlate.week_start.desc())
        .limit(1)
    )


def blocked_player_ids_from_prior_slate(
    session: Session, league_slug: str, user_id: int, slate: BowlSixSlate
) -> set[int]:
    prev = prior_submitted_slate_for_user(session, league_slug, user_id, slate)
    if prev is None:
        return set()
    lineup = session.scalars(
        select(BowlSixLineup)
        .where(BowlSixLineup.slate_id == prev.id, BowlSixLineup.user_id == int(user_id))
        .options(joinedload(BowlSixLineup.picks))
        .limit(1)
    ).unique().first()
    if not lineup or not lineup.picks:
        return set()
    return {int(p.player_id) for p in lineup.picks}


def get_lineup(session: Session, slate_id: int, user_id: int) -> BowlSixLineup | None:
    return session.scalars(
        select(BowlSixLineup)
        .where(BowlSixLineup.slate_id == int(slate_id), BowlSixLineup.user_id == int(user_id))
        .options(joinedload(BowlSixLineup.picks), joinedload(BowlSixLineup.score))
        .limit(1)
    ).unique().first()


def lineup_is_editable(slate: BowlSixSlate) -> bool:
    sync_slate_lock_status(db.session, slate)
    if slate.status in ("scored", "skipped"):
        return False
    return lock_time_is_future(slate)


def validate_lineup_picks(
    session: Session,
    league_session: Session,
    *,
    league_slug: str,
    slate: BowlSixSlate,
    user_id: int,
    picks: dict[str, int],
    captain_player_id: int | None,
) -> LineupValidation:
    if not lineup_is_editable(slate):
        return LineupValidation(False, "This slate is locked — lineups can no longer be changed.")
    if set(picks.keys()) != set(SLOT_ORDER):
        return LineupValidation(False, "Select exactly six players (1 GK, 2 DEF, 3 FWD).")
    player_ids = list(picks.values())
    if len(set(player_ids)) != 6:
        return LineupValidation(False, "Each slot must be a different player.")
    blocked = blocked_player_ids_from_prior_slate(session, league_slug, user_id, slate)
    blocked_names: list[str] = []
    team_counts: dict[int, int] = {}
    for slot in SLOT_ORDER:
        pid = int(picks[slot])
        if pid in blocked:
            p = league_session.get(Player, pid)
            blocked_names.append(p.full_name if p else f"Player #{pid}")
        player = league_session.get(Player, pid)
        if player is None:
            return LineupValidation(False, f"Unknown player in slot {SLOT_LABELS.get(slot, slot)}.")
        if not slot_accepts_position(slot, player.position):
            return LineupValidation(
                False,
                f"{player.full_name} cannot play slot {SLOT_LABELS.get(slot, slot)}.",
            )
        tid = player.current_team_id
        if tid:
            team_counts[int(tid)] = team_counts.get(int(tid), 0) + 1
    if blocked_names:
        return LineupValidation(
            False,
            "Cannot reuse from last slate: " + ", ".join(blocked_names[:3])
            + ("…" if len(blocked_names) > 3 else ""),
        )
    for tid, cnt in team_counts.items():
        if cnt > 3:
            team = league_session.get(Team, tid)
            name = team.full_display_name() if team else f"Team {tid}"
            return LineupValidation(False, f"No more than 3 players from {name}.")
    if captain_player_id is not None:
        cap = int(captain_player_id)
        if cap not in player_ids:
            return LineupValidation(False, "Captain must be one of your six picks.")
        cap_player = league_session.get(Player, cap)
        if cap_player and position_kind(cap_player.position) == "gk":
            return LineupValidation(False, "Goalies cannot be captain.")
    return LineupValidation(True)


def save_lineup(
    session: Session,
    league_session: Session,
    *,
    league_slug: str,
    slate: BowlSixSlate,
    user_id: int,
    picks: dict[str, int],
    captain_player_id: int | None,
) -> LineupValidation:
    v = validate_lineup_picks(
        session,
        league_session,
        league_slug=league_slug,
        slate=slate,
        user_id=user_id,
        picks=picks,
        captain_player_id=captain_player_id,
    )
    if not v.ok:
        return v
    lineup = get_lineup(session, slate.id, user_id)
    if lineup is None:
        lineup = BowlSixLineup(slate_id=slate.id, user_id=int(user_id))
        session.add(lineup)
        session.flush()
    session.execute(delete(BowlSixLineupPick).where(BowlSixLineupPick.lineup_id == lineup.id))
    for slot in SLOT_ORDER:
        session.add(
            BowlSixLineupPick(
                lineup_id=lineup.id,
                slot=slot,
                player_id=int(picks[slot]),
            )
        )
    lineup.captain_player_id = int(captain_player_id) if captain_player_id else None
    lineup.submitted_at = datetime.utcnow()
    session.flush()
    return LineupValidation(True)


def rs_games_in_slate_week(league_session: Session, slate: BowlSixSlate) -> list[Game]:
    """Regular-season games first observed final during the slate's real-time scoring window."""
    season = get_current_season()
    if season is None:
        return []
    window_start, window_end = slate_real_scoring_window_utc(slate)
    markers = list(
        db.session.scalars(
            select(BowlSixGameFinal).where(
                BowlSixGameFinal.league_slug == slate.league_slug,
                BowlSixGameFinal.first_final_at >= window_start,
                BowlSixGameFinal.first_final_at < window_end,
            )
        ).all()
    )
    game_ids = {int(m.game_id) for m in markers}
    if not game_ids:
        return []
    rows = list(
        league_session.scalars(
            select(Game).where(
                Game.season_id == int(season.id),
                Game.id.in_(game_ids),
            )
        ).all()
    )
    return [
        g
        for g in rows
        if (g.status or "").lower() == "final" and _is_regular_season_game(g.game_type)
    ]


def reset_slate_scoring_state(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> dict[str, int]:
    """Clear stored BOWL Six scores and game markers without deleting submitted lineups."""
    lineup_ids = select(BowlSixLineup.id).where(BowlSixLineup.slate_id == int(slate.id))
    score_result = session.execute(
        delete(BowlSixLineupScore).where(BowlSixLineupScore.lineup_id.in_(lineup_ids))
    )
    player_stat_result = session.execute(
        delete(BowlSixPlayerWeekStat).where(BowlSixPlayerWeekStat.slate_id == int(slate.id))
    )

    marker_removed = 0
    season = get_current_season()
    if season is not None:
        games = list(
            league_session.scalars(
                select(Game).where(
                    Game.season_id == int(season.id),
                    Game.game_date.isnot(None),
                    Game.game_date >= slate.week_start,
                    Game.game_date <= slate.week_end,
                )
            ).all()
        )
        game_ids = [int(g.id) for g in games if _is_regular_season_game(g.game_type)]
        if game_ids:
            marker_result = session.execute(
                delete(BowlSixGameFinal).where(
                    BowlSixGameFinal.league_slug == str(slate.league_slug or ""),
                    BowlSixGameFinal.game_id.in_(game_ids),
                )
            )
            marker_removed = int(marker_result.rowcount or 0)

    session.flush()
    return {
        "lineup_scores": int(score_result.rowcount or 0),
        "player_week_stats": int(player_stat_result.rowcount or 0),
        "game_markers": marker_removed,
    }


def _backfill_active_slate_final_markers_from_legacy_window(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> int:
    """One-time migration aid for already-locked slates created before real-time tracking."""
    if slate.status not in ("locked", "open"):
        return 0
    existing = session.scalar(
        select(func.count())
        .select_from(BowlSixGameFinal)
        .where(BowlSixGameFinal.league_slug == slate.league_slug)
    ) or 0
    if int(existing) > 0:
        return 0
    if not slate.scoring_week_start or not slate.scoring_week_end:
        return 0
    if (slate.scoring_week_start, slate.scoring_week_end) == (slate.week_start, slate.week_end):
        return 0
    season = get_current_season()
    if season is None:
        return 0
    rows = list(
        league_session.scalars(
            select(Game).where(
                Game.season_id == int(season.id),
                Game.game_date.isnot(None),
                Game.game_date >= slate.scoring_week_start,
                Game.game_date <= slate.scoring_week_end,
                Game.status == "final",
            )
        )
    )
    game_ids = [int(g.id) for g in rows if _is_regular_season_game(g.game_type)]
    return record_bowl_six_game_finals(
        session,
        league_session,
        league_slug=str(slate.league_slug or ""),
        game_ids=game_ids,
        observed_at=max(utcnow_naive(), slate_real_scoring_window_utc(slate)[0]),
    )


def _marker_observed_at_for_slate(slate: BowlSixSlate, now: datetime | None = None) -> datetime:
    """Clamp a marker timestamp into the slate's real-time scoring window."""
    now = now or utcnow_naive()
    window_start, window_end = slate_real_scoring_window_utc(slate)
    if now < window_start:
        return window_start
    if now >= window_end:
        return window_end - timedelta(seconds=1)
    return now


def _sync_slate_week_final_markers(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> int:
    """Backfill missing real-time markers for finals in the active slate scoring week.

    CSV re-imports often leave games ``final`` without a status transition, so
    ``import_games`` never records markers. This runs on every auto-update (including
    post-import) so BOWL Six scoring and Discord leaders can refresh immediately.
    """
    if slate.status not in ("locked", "open"):
        return 0
    season = get_current_season()
    if season is None:
        return 0
    week_start = slate.scoring_week_start or slate.week_start
    week_end = slate.scoring_week_end or slate.week_end
    if not week_start or not week_end:
        return 0
    rows = list(
        league_session.scalars(
            select(Game).where(
                Game.season_id == int(season.id),
                Game.game_date.isnot(None),
                Game.game_date >= week_start,
                Game.game_date <= week_end,
                Game.status == "final",
            )
        ).all()
    )
    game_ids = [int(g.id) for g in rows if _is_regular_season_game(g.game_type)]
    if not game_ids:
        return 0
    return record_bowl_six_game_finals(
        session,
        league_session,
        league_slug=str(slate.league_slug or ""),
        game_ids=game_ids,
        observed_at=_marker_observed_at_for_slate(slate),
    )


def _record_current_calendar_final_markers_for_active_slate(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> int:
    """Catch up finals for the active slate after imports (see ``_sync_slate_week_final_markers``)."""
    return _sync_slate_week_final_markers(session, league_session, slate)


def rs_game_ids_for_slate(league_session: Session, slate: BowlSixSlate) -> list[int]:
    return [
        int(g.id)
        for g in rs_games_in_slate_week(league_session, slate)
        if (g.status or "").lower() == "final"
    ]


def slate_week_rs_games_complete(league_session: Session, slate: BowlSixSlate) -> bool:
    """Real-time BOWL Six weeks complete at Sunday 11:59 PM ET."""
    _, window_end = slate_real_scoring_window_utc(slate)
    return utcnow_naive() >= window_end


def refresh_player_week_stats(session: Session, slate: BowlSixSlate, league_session: Session) -> None:
    season = get_current_season()
    if season is None:
        return
    game_ids = rs_game_ids_for_slate(league_session, slate)
    session.execute(delete(BowlSixPlayerWeekStat).where(BowlSixPlayerWeekStat.slate_id == slate.id))
    pick_rows = session.execute(
        select(BowlSixLineupPick.player_id, func.count())
        .join(BowlSixLineup, BowlSixLineup.id == BowlSixLineupPick.lineup_id)
        .where(BowlSixLineup.slate_id == slate.id, BowlSixLineup.submitted_at.is_not(None))
        .group_by(BowlSixLineupPick.player_id)
    ).all()
    pick_counts = {int(pid): int(cnt) for pid, cnt in pick_rows}
    all_pids = set(pick_counts.keys())
    if game_ids and all_pids:
        from app.services.bowl_six_scoring import player_points_in_games

        pts_map = player_points_in_games(
            league_session,
            season_id=int(season.id),
            player_ids=all_pids,
            game_ids=game_ids,
        )
    else:
        pts_map = {}
    seen_pids = set(pick_counts.keys()) | set(pts_map.keys())
    for pid in seen_pids:
        session.add(
            BowlSixPlayerWeekStat(
                slate_id=slate.id,
                player_id=int(pid),
                fantasy_points=float(pts_map.get(pid, 0.0)),
                pick_count=int(pick_counts.get(pid, 0)),
            )
        )


def refresh_slate_lineup_scores(
    session: Session, league_session: Session, slate: BowlSixSlate
) -> int:
    """Recalculate submitted lineup totals from final RS games in the week."""
    if slate.status == "skipped":
        return 0
    season = get_current_season()
    if season is None:
        return 0
    game_ids = rs_game_ids_for_slate(league_session, slate)
    lineups = list(
        session.scalars(
            select(BowlSixLineup)
            .where(BowlSixLineup.slate_id == slate.id, BowlSixLineup.submitted_at.is_not(None))
            .options(joinedload(BowlSixLineup.picks))
        )
        .unique()
        .all()
    )
    n = 0
    for lineup in lineups:
        picks = {p.slot: int(p.player_id) for p in lineup.picks}
        if len(picks) < 6:
            continue
        total, payload = score_lineup_for_slate(
            league_session,
            season=season,
            picks=picks,
            captain_player_id=lineup.captain_player_id,
            game_ids=game_ids,
        )
        existing = lineup.score
        if existing:
            existing.total_points = total
            existing.points_json = dumps_points(payload)
            existing.scored_at = datetime.utcnow()
        else:
            session.add(
                BowlSixLineupScore(
                    lineup_id=lineup.id,
                    total_points=total,
                    points_json=dumps_points(payload),
                )
            )
        n += 1
    return n


def finalize_slate(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    notify: bool = True,
) -> int:
    """Mark slate scored, refresh pool stats, sync AP prizes; optionally notify GMs once."""
    if slate.status == "skipped":
        return 0
    was_scored = slate.status == "scored"
    n = refresh_slate_lineup_scores(session, league_session, slate)
    slate.scoring_version = int(slate.scoring_version or 0) + 1
    refresh_player_week_stats(session, slate, league_session)
    slate.status = "scored"
    sync_bowl_six_slate_ap_awards(session, slate)
    if notify and not was_scored:
        notify_slate_scored(session, slate)
    ensure_current_slate_after_finalization(session, league_session, slate)
    return n


def score_slate(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    notify: bool | None = None,
) -> int:
    """Lock if needed, refresh scores, finalize (AP + optional GM notifications)."""
    if slate.status == "skipped":
        return 0
    if slate.status == "open":
        sync_slate_lock_status(session, slate)
    if slate.status == "open":
        slate.status = "locked"
    if notify is None:
        notify = slate.status != "scored"
    return finalize_slate(session, league_session, slate, notify=notify)


def auto_update_bowl_six_slates(
    session: Session, league_session: Session, league_slug: str
) -> list[str]:
    """Refresh locked/scored slates after imports or page loads. Returns log lines."""
    from app.sqlite_retry import write_with_sqlite_retry

    if not bowl_six_enabled(session, league_slug):
        return []
    lookback = date.today() - timedelta(days=21)
    slate_ids = [
        int(s.id)
        for s in session.scalars(
            select(BowlSixSlate)
            .where(
                BowlSixSlate.league_slug == league_slug,
                BowlSixSlate.status.in_(["open", "locked", "scored"]),
                BowlSixSlate.week_end >= lookback,
            )
            .order_by(BowlSixSlate.week_start.desc())
        ).all()
    ]
    notes: list[str] = []
    for slate_id in slate_ids:
        try:

            def _update_slate(sid: int = slate_id) -> str | None:
                row = session.get(BowlSixSlate, sid)
                if row is None:
                    return None
                return _auto_update_single_slate(session, league_session, row)

            note = write_with_sqlite_retry(session, _update_slate)
            if note:
                notes.append(note)
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            _log.exception("BOWL Six auto-update failed for slate %s", slate_id)
    return notes


def _enqueue_bowl_six_discord_leaders_safe(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    force: bool = False,
) -> None:
    try:
        from app.services.bowl_six_discord import maybe_enqueue_bowl_six_leaders_discord

        maybe_enqueue_bowl_six_leaders_discord(
            session, league_session, slate, force=force
        )
    except Exception:
        _log.exception("BOWL Six Discord leaders enqueue failed for slate %s", slate.id)


def refresh_bowl_six_leaders_for_discord_poll(
    session: Session, league_session: Session, league_slug: str
) -> bool:
    """Lightweight bot-poll refresh: rescore current slate stats and queue leaders Discord.

    Unlike ``auto_update_bowl_six_slates``, this does not finalize slates or walk the
  21-day lookback window. Roster unlock reminders already run on every poll; leaders need
    the same cadence so the live embed updates after imports without a site visit.
    """
    if not bowl_six_enabled(session, league_slug):
        return False
    slate = get_or_create_current_slate(session, league_slug, league_session=league_session)
    if slate is None or str(slate.status or "") in ("skipped",):
        return False
    status = str(slate.status or "")
    if status == "open":
        if rs_game_ids_for_slate(league_session, slate):
            refresh_player_week_stats(session, slate, league_session)
            refresh_slate_lineup_scores(session, league_session, slate)
    elif status in ("locked", "scored"):
        refresh_slate_lineup_scores(session, league_session, slate)
        refresh_player_week_stats(session, slate, league_session)
    else:
        return False
    _enqueue_bowl_six_discord_leaders_safe(session, league_session, slate)
    return True


def _auto_update_single_slate(
    session: Session, league_session: Session, slate: BowlSixSlate
) -> str | None:
    if slate.status == "skipped":
        return None
    _backfill_active_slate_final_markers_from_legacy_window(session, league_session, slate)
    _record_current_calendar_final_markers_for_active_slate(session, league_session, slate)
    if sync_slate_week_to_league_calendar(
        session, league_session, str(slate.league_slug), slate
    ):
        session.flush()
    sync_slate_lock_status(session, slate)
    if slate.status == "open":
        n = 0
        if rs_game_ids_for_slate(league_session, slate):
            refresh_player_week_stats(session, slate, league_session)
            n = refresh_slate_lineup_scores(session, league_session, slate)
        _enqueue_bowl_six_discord_leaders_safe(session, league_session, slate)
        if n:
            return (
                f"Week {slate.week_start}: updated {n} lineup(s) "
                "from completed games (lineups still open)."
            )
        return None
    if slate.status == "locked":
        n = refresh_slate_lineup_scores(session, league_session, slate)
        refresh_player_week_stats(session, slate, league_session)
        if slate_award_time_reached(slate):
            finalize_slate(session, league_session, slate, notify=True)
            _enqueue_bowl_six_discord_leaders_safe(
                session, league_session, slate, force=True
            )
            return (
                f"Week {slate.week_start}: finalized ({n} lineups), "
                "real-time award window reached — AP and notifications sent."
            )
        _enqueue_bowl_six_discord_leaders_safe(session, league_session, slate)
        if n:
            return f"Week {slate.week_start}: updated {n} lineup(s) from completed games."
        return None
    if slate.status == "scored":
        n = refresh_slate_lineup_scores(session, league_session, slate)
        refresh_player_week_stats(session, slate, league_session)
        sync_bowl_six_slate_ap_awards(session, slate)
        ensure_current_slate_after_finalization(session, league_session, slate)
        _enqueue_bowl_six_discord_leaders_safe(session, league_session, slate)
        if n:
            return f"Week {slate.week_start}: re-synced {n} lineup(s) after data change."
        return None
    return None


def sync_bowl_six_slate_ap_awards(session: Session, slate: BowlSixSlate) -> None:
    """Award top-3 GM teams AP; reverse prior payouts when podium changes on re-score."""
    if slate.status != "scored":
        return
    ranked = slate_rankings(session, slate)
    desired: dict[int, tuple[int, int]] = {}
    for place, row in enumerate(ranked[:3], start=1):
        uid = int(row["user_id"])
        mem = session.scalar(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == slate.league_slug,
                GmLeagueMembership.user_id == uid,
                GmLeagueMembership.status == "active",
            ).limit(1)
        )
        if mem is None:
            continue
        desired[place] = (int(mem.team_id), int(row["user_id"]))
    prev = {
        1: slate.ap_place1_team_id,
        2: slate.ap_place2_team_id,
        3: slate.ap_place3_team_id,
    }
    version = int(slate.scoring_version or 1)
    for place in (1, 2, 3):
        old_tid = prev.get(place)
        new = desired.get(place)
        new_tid = new[0] if new else None
        prize = AP_PRIZES.get(place, 0)
        if old_tid and old_tid != new_tid:
            add_ledger_entry(
                league_slug=slate.league_slug,
                team_id=int(old_tid),
                delta=-prize,
                reason_code="bowl_six_slate_prize_reversal",
                meta={"slate_id": slate.id, "place": place, "scoring_version": version},
                source_ref=f"bowl_six:slate:{slate.id}:place:{place}:rev:{version}",
            )
        if new_tid and new_tid != old_tid:
            add_ledger_entry(
                league_slug=slate.league_slug,
                team_id=int(new_tid),
                delta=prize,
                reason_code="bowl_six_slate_prize",
                meta={
                    "slate_id": slate.id,
                    "place": place,
                    "user_id": new[1],
                    "scoring_version": version,
                },
                source_ref=f"bowl_six:slate:{slate.id}:place:{place}:award:{version}",
            )
    slate.ap_place1_team_id = desired[1][0] if 1 in desired else None
    slate.ap_place2_team_id = desired[2][0] if 2 in desired else None
    slate.ap_place3_team_id = desired[3][0] if 3 in desired else None


def _bowl_six_award_ledger_exists(
    session: Session,
    slate: BowlSixSlate,
    *,
    place: int,
    team_id: int,
) -> bool:
    prize = AP_PRIZES.get(place, 0)
    if prize <= 0:
        return True
    prefix = f"bowl_six:slate:{int(slate.id)}:place:{place}:award:"
    row_id = session.scalar(
        select(ApLedgerEntry.id)
        .where(
            ApLedgerEntry.league_slug == str(slate.league_slug or ""),
            ApLedgerEntry.team_id == int(team_id),
            ApLedgerEntry.delta == int(prize),
            ApLedgerEntry.reason_code == "bowl_six_slate_prize",
            ApLedgerEntry.source_ref.like(f"{prefix}%"),
        )
        .limit(1)
    )
    return row_id is not None


def ensure_bowl_six_slate_prize_ledgers(session: Session, slate: BowlSixSlate) -> int:
    """Repair missing AP prize ledger rows for an already-scored slate."""
    if slate.status != "scored":
        return 0
    sync_bowl_six_slate_ap_awards(session, slate)
    ranked = slate_rankings(session, slate)
    created = 0
    version = int(slate.scoring_version or 1)
    for place, row in enumerate(ranked[:3], start=1):
        prize = AP_PRIZES.get(place, 0)
        if prize <= 0:
            continue
        team_id = getattr(slate, f"ap_place{place}_team_id", None)
        if not team_id:
            continue
        if _bowl_six_award_ledger_exists(session, slate, place=place, team_id=int(team_id)):
            continue
        added = add_ledger_entry(
            league_slug=str(slate.league_slug or ""),
            team_id=int(team_id),
            delta=prize,
            reason_code="bowl_six_slate_prize",
            meta={
                "slate_id": int(slate.id),
                "place": place,
                "user_id": int(row["user_id"]),
                "scoring_version": version,
                "repair": True,
            },
            source_ref=f"bowl_six:slate:{int(slate.id)}:place:{place}:award:repair:{version}",
        )
        if added is not None:
            created += 1
    return created


def ensure_past_week_bowl_six_prizes(
    session: Session,
    league_session: Session,
    league_slug: str,
) -> dict[str, Any]:
    """Finalize/repair AP prizes for the most recent completed BOWL Six week."""
    slug = str(league_slug or "").strip()
    current_start, _ = _real_bowl_six_week_bounds()
    target_start = current_start - timedelta(days=7)
    slate = session.scalar(
        select(BowlSixSlate)
        .where(
            BowlSixSlate.league_slug == slug,
            BowlSixSlate.week_start == target_start,
        )
        .limit(1)
    )
    if slate is None:
        return {
            "ok": False,
            "message": f"No BOWL Six slate found for week of {target_start.isoformat()}.",
            "slate_id": None,
            "lineups_scored": 0,
            "ledgers_created": 0,
        }
    if slate.status == "skipped":
        return {
            "ok": True,
            "message": f"Week of {slate.week_start.isoformat()} was skipped.",
            "slate_id": int(slate.id),
            "lineups_scored": 0,
            "ledgers_created": 0,
        }
    if not slate_award_time_reached(slate):
        return {
            "ok": False,
            "message": f"Week of {slate.week_start.isoformat()} is not ready for AP payout yet.",
            "slate_id": int(slate.id),
            "lineups_scored": 0,
            "ledgers_created": 0,
        }
    if slate.status != "scored":
        lineups = score_slate(session, league_session, slate, notify=True)
    else:
        lineups = refresh_slate_lineup_scores(session, league_session, slate)
        refresh_player_week_stats(session, slate, league_session)
        sync_bowl_six_slate_ap_awards(session, slate)
        ensure_current_slate_after_finalization(session, league_session, slate)
    ledgers_created = ensure_bowl_six_slate_prize_ledgers(session, slate)
    return {
        "ok": True,
        "message": (
            f"Week of {slate.week_start.isoformat()} is scored; "
            f"AP prize ledgers verified ({ledgers_created} repaired)."
        ),
        "slate_id": int(slate.id),
        "lineups_scored": int(lineups or 0),
        "ledgers_created": int(ledgers_created or 0),
    }


def slate_rankings(session: Session, slate: BowlSixSlate) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            BowlSixLineup.user_id,
            BowlSixLineup.submitted_at,
            BowlSixLineupScore.total_points,
        )
        .join(BowlSixLineupScore, BowlSixLineupScore.lineup_id == BowlSixLineup.id)
        .where(BowlSixLineup.slate_id == slate.id)
        .order_by(
            BowlSixLineupScore.total_points.desc(),
            BowlSixLineup.submitted_at.asc(),
        )
    ).all()
    out: list[dict[str, Any]] = []
    for uid, submitted_at, pts in rows:
        out.append(
            {
                "user_id": int(uid),
                "total_points": float(pts or 0),
                "submitted_at": submitted_at,
            }
        )
    return out


def slate_rankings_in_progress(session: Session, slate: BowlSixSlate) -> list[dict[str, Any]]:
    """Submitted lineups for a locked (not yet scored) slate, including 0 pts before first final game."""
    rows = session.execute(
        select(
            BowlSixLineup.user_id,
            BowlSixLineup.submitted_at,
            BowlSixLineupScore.total_points,
        )
        .outerjoin(BowlSixLineupScore, BowlSixLineupScore.lineup_id == BowlSixLineup.id)
        .where(
            BowlSixLineup.slate_id == slate.id,
            BowlSixLineup.submitted_at.is_not(None),
        )
        .order_by(
            func.coalesce(BowlSixLineupScore.total_points, 0).desc(),
            BowlSixLineup.submitted_at.asc(),
        )
    ).all()
    out: list[dict[str, Any]] = []
    for uid, submitted_at, pts in rows:
        out.append(
            {
                "user_id": int(uid),
                "total_points": float(pts or 0),
                "submitted_at": submitted_at,
            }
        )
    return out


def slate_gm_submission_roster(
    session: Session,
    league_slug: str,
    slate: BowlSixSlate,
) -> dict[str, Any]:
    """Active GMs and whether each saved a valid lineup for this slate (names only, not picks)."""
    from app.services.gm_messaging import gm_discord_name

    memberships = list(
        session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    lineups = {
        int(l.user_id): l
        for l in session.scalars(
            select(BowlSixLineup).where(BowlSixLineup.slate_id == slate.id)
        ).all()
    }
    rows: list[dict[str, Any]] = []
    for mem in memberships:
        user = session.get(User, int(mem.user_id))
        lineup = lineups.get(int(mem.user_id))
        submitted = bool(lineup and lineup.submitted_at)
        rows.append(
            {
                "user_id": int(mem.user_id),
                "team_id": int(mem.team_id),
                "gm_name": gm_discord_name(user) if user else f"User #{mem.user_id}",
                "submitted": submitted,
                "has_captain": bool(lineup and lineup.captain_player_id),
            }
        )
    submitted_count = sum(1 for r in rows if r["submitted"])
    return {
        "rows": rows,
        "total_gms": len(rows),
        "submitted_count": submitted_count,
        "pending_count": len(rows) - submitted_count,
    }


BOWL_SIX_SNAPSHOT_DISPLAY: list[tuple[str, str]] = [
    ("fwd1", "FWD"),
    ("fwd2", "FWD"),
    ("fwd3", "FWD"),
    ("def1", "DEF"),
    ("def2", "DEF"),
    ("gk", "GK"),
]


def bowl_six_lineup_snapshot_slots(
    site_session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    user_id: int,
) -> list[dict[str, Any]] | None:
    """Submitted lineup as snapshot card slots (forwards, defense, goalie), or None."""
    from app.services.seasons import season_age_reference_date

    lineup = site_session.scalars(
        select(BowlSixLineup)
        .where(
            BowlSixLineup.slate_id == slate.id,
            BowlSixLineup.user_id == int(user_id),
            BowlSixLineup.submitted_at.is_not(None),
        )
        .limit(1)
    ).first()
    if lineup is None:
        return None
    pick_rows = list(
        site_session.scalars(
            select(BowlSixLineupPick).where(BowlSixLineupPick.lineup_id == lineup.id)
        ).all()
    )
    pick_by_slot = {p.slot: int(p.player_id) for p in pick_rows}
    if len(pick_by_slot) < 6:
        return None
    captain_id = int(lineup.captain_player_id) if lineup.captain_player_id else None
    age_ref = season_age_reference_date(get_current_season())
    slots: list[dict[str, Any]] = []
    for slot_key, label in BOWL_SIX_SNAPSHOT_DISPLAY:
        pid = pick_by_slot.get(slot_key)
        if not pid:
            slots.append({"slot": slot_key, "label": label, "card": None, "is_captain": False})
            continue
        player = league_session.get(Player, pid)
        if player is None:
            slots.append({"slot": slot_key, "label": label, "card": None, "is_captain": False})
            continue
        team = league_session.get(Team, int(player.current_team_id)) if player.current_team_id else None
        abbr = team.abbreviation if team and team.abbreviation else "—"
        slots.append(
            {
                "slot": slot_key,
                "label": label,
                "card": build_player_snapshot_card(player, team_abbr=abbr, age_ref=age_ref),
                "is_captain": bool(captain_id and captain_id == pid),
            }
        )
    return slots


def slate_gm_submission_roster_enriched(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    slate: BowlSixSlate,
) -> dict[str, Any]:
    """Submission roster with team objects; waiting GMs listed before submitted."""
    roster = slate_gm_submission_roster(site_session, league_slug, slate)
    enriched: list[dict[str, Any]] = []
    for r in roster["rows"]:
        team = league_session.get(Team, int(r["team_id"]))
        sort_name = team.full_display_name() if team else str(r.get("gm_name") or "")
        enriched.append({**r, "team": team, "team_sort": sort_name})
    enriched.sort(key=lambda x: (x["submitted"], x["team_sort"].lower()))
    return {**roster, "rows": enriched}


def slate_week_game_progress(league_session: Session, slate: BowlSixSlate) -> dict[str, int | bool]:
    games = rs_games_in_slate_week(league_session, slate)
    final = len(games)
    return {"total": final, "final": final, "complete": slate_week_rs_games_complete(league_session, slate)}


def gm_season_standings(
    session: Session,
    league_slug: str,
    *,
    season_start: date | None = None,
    season_end: date | None = None,
) -> list[dict[str, Any]]:
    """Cumulative BOWL Six points across scored slates for current active GM teams.

    Historical lineup rows are keyed to the submitting user, but leaderboard display should
    follow the current GM/team assignment. When a team changes GMs, carry that team's
    BOWL Six season points forward to the current active membership instead of showing a
    deleted or inactive user account.
    """
    if season_start is None or season_end is None:
        default_start, default_end = bowl_six_real_season_bounds()
        season_start = season_start or default_start
        season_end = season_end or default_end

    active_memberships = list(
        session.scalars(
            select(GmLeagueMembership)
            .where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.status == "active",
            )
            .order_by(GmLeagueMembership.team_id, GmLeagueMembership.id)
        ).all()
    )
    active_by_team: dict[int, GmLeagueMembership] = {}
    for mem in active_memberships:
        active_by_team.setdefault(int(mem.team_id), mem)
    if not active_by_team:
        return []

    scored_rows = session.execute(
        select(
            BowlSixLineup.user_id,
            BowlSixSlate.id,
            BowlSixLineupScore.total_points,
        )
        .join(BowlSixSlate, BowlSixSlate.id == BowlSixLineup.slate_id)
        .join(BowlSixLineupScore, BowlSixLineupScore.lineup_id == BowlSixLineup.id)
        .where(
            BowlSixSlate.league_slug == league_slug,
            BowlSixSlate.status == "scored",
            BowlSixSlate.week_start >= season_start,
            BowlSixSlate.week_start <= season_end,
        )
    ).all()

    scored_user_ids = {int(uid) for uid, _, _ in scored_rows if uid is not None}
    membership_by_user: dict[int, GmLeagueMembership] = {
        int(mem.user_id): mem for mem in active_memberships
    }
    if scored_user_ids:
        historical_memberships = list(
            session.scalars(
                select(GmLeagueMembership)
                .where(
                    GmLeagueMembership.league_slug == league_slug,
                    GmLeagueMembership.user_id.in_(scored_user_ids),
                )
                .order_by(GmLeagueMembership.user_id, GmLeagueMembership.id.desc())
            ).all()
        )
        historical_memberships.sort(
            key=lambda mem: (
                int(mem.user_id),
                0 if str(mem.status or "") == "active" else 1,
                -int(mem.id or 0),
            )
        )
        for mem in historical_memberships:
            membership_by_user.setdefault(int(mem.user_id), mem)

    points_by_team: dict[int, float] = {}
    scored_slates_by_team: dict[int, set[int]] = {}
    for uid, slate_id, total in scored_rows:
        mem = membership_by_user.get(int(uid))
        if mem is None:
            continue
        team_id = int(mem.team_id)
        if team_id not in active_by_team:
            continue
        points_by_team[team_id] = points_by_team.get(team_id, 0.0) + float(total or 0)
        scored_slates_by_team.setdefault(team_id, set()).add(int(slate_id))

    standings: list[dict[str, Any]] = []
    for team_id, mem in active_by_team.items():
        standings.append(
            {
                "user_id": int(mem.user_id),
                "team_id": team_id,
                "season_points": float(points_by_team.get(team_id, 0.0)),
                "weeks_played": len(scored_slates_by_team.get(team_id, set())),
            }
        )
    standings.sort(key=lambda r: (-float(r["season_points"]), -int(r["weeks_played"]), int(r["team_id"])))

    result: list[dict[str, Any]] = []
    for rank, row in enumerate(standings, start=1):
        result.append(
            {
                "rank": rank,
                "user_id": int(row["user_id"]),
                "team_id": int(row["team_id"]),
                "season_points": float(row["season_points"]),
                "weeks_played": int(row["weeks_played"]),
                "season_ap_award": season_ap_prize_for_rank(rank),
            }
        )
    return result


def top_players_for_slate(
    session: Session, slate: BowlSixSlate, *, limit: int = 5
) -> list[BowlSixPlayerWeekStat]:
    return list(
        session.scalars(
            select(BowlSixPlayerWeekStat)
            .where(BowlSixPlayerWeekStat.slate_id == slate.id)
            .order_by(BowlSixPlayerWeekStat.fantasy_points.desc())
            .limit(limit)
        ).all()
    )


def most_picked_for_slate(
    session: Session, slate: BowlSixSlate, *, limit: int = 5
) -> list[BowlSixPlayerWeekStat]:
    total_lineups = session.scalar(
        select(func.count())
        .select_from(BowlSixLineup)
        .where(BowlSixLineup.slate_id == slate.id, BowlSixLineup.submitted_at.is_not(None))
    ) or 0
    rows = list(
        session.scalars(
            select(BowlSixPlayerWeekStat)
            .where(
                BowlSixPlayerWeekStat.slate_id == slate.id,
                BowlSixPlayerWeekStat.pick_count > 0,
            )
            .order_by(BowlSixPlayerWeekStat.pick_count.desc())
            .limit(limit)
        ).all()
    )
    for r in rows:
        r._pick_pct = (100.0 * r.pick_count / total_lineups) if total_lineups else 0.0  # type: ignore[attr-defined]
    return rows


def notify_slate_scored(session: Session, slate: BowlSixSlate) -> None:
    ranked = slate_rankings(session, slate)
    rank_by_user = {r["user_id"]: i + 1 for i, r in enumerate(ranked)}
    lineups = session.scalars(
        select(BowlSixLineup).where(
            BowlSixLineup.slate_id == slate.id, BowlSixLineup.submitted_at.is_not(None)
        )
    ).all()
    for lineup in lineups:
        rank = rank_by_user.get(int(lineup.user_id))
        pts = 0.0
        if lineup.score:
            pts = float(lineup.score.total_points)
        ap_note = ""
        if rank and rank <= 3:
            ap_note = f" +{AP_PRIZES[rank]} AP"
        notif = GmInAppNotification(
            league_slug=slate.league_slug,
            user_id=int(lineup.user_id),
            kind="bowl_six_scored",
            title=f"BOWL Six week complete — #{rank or '—'}",
            body=f"You scored {pts:.1f} pts this week.{ap_note}",
            article_id=int(slate.id),
        )
        session.add(notif)
        session.flush()
        try:
            from app.services.discord_direct_messages import enqueue_notification_dm

            enqueue_notification_dm(session, league_slug=slate.league_slug, notification=notif)
        except Exception:
            pass


def last_scored_slate(session: Session, league_slug: str) -> BowlSixSlate | None:
    return session.scalar(
        select(BowlSixSlate)
        .where(BowlSixSlate.league_slug == league_slug, BowlSixSlate.status == "scored")
        .order_by(BowlSixSlate.week_start.desc())
        .limit(1)
    )

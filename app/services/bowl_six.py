"""BOWL Six weekly pick game — slates, lineups, scoring, AP prizes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.auth_login import active_membership_for_league
from app.league_db import db
from app.models import Game, Player, Team
from app.services.ap_service import add_ledger_entry
from app.services.bowl_six_scoring import (
    SLOT_ORDER,
    dumps_points,
    position_kind,
    score_lineup_for_slate,
    slot_accepts_position,
)
from app.services.league_rules import get_rule_value, rule_bool, rule_int
from app.services.seasons import get_current_season
from app.site_models import (
    BowlSixLineup,
    BowlSixLineupPick,
    BowlSixLineupScore,
    BowlSixPlayerWeekStat,
    BowlSixSlate,
    GmInAppNotification,
    GmLeagueMembership,
)

AP_PRIZES = {1: 10, 2: 6, 3: 3}
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


def _week_bounds_for_date(d: date, week_start_dow: int) -> tuple[date, date]:
    """Monday=0 .. Sunday=6 style offset from rule (default Monday=0)."""
    dow = int(d.weekday())
    start_dow = int(week_start_dow) % 7
    delta = (dow - start_dow) % 7
    week_start = d - timedelta(days=delta)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _parse_lock_time(rule_val: str) -> time:
    raw = (rule_val or "00:00").strip()
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h % 24, m % 60)
    except (ValueError, IndexError):
        return time(0, 0)


def default_lock_at(week_start: date, league_slug: str, session: Session) -> datetime:
    lock_t = _parse_lock_time(get_rule_value(session, league_slug, "bowl_six_lock_time_utc", "00:00"))
    return datetime.combine(week_start, lock_t)


def sync_slate_lock_status(session: Session, slate: BowlSixSlate) -> None:
    if slate.status in ("scored", "skipped"):
        return
    now = datetime.utcnow()
    if slate.status == "open" and now >= slate.lock_at:
        slate.status = "locked"


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


def get_or_create_current_slate(session: Session, league_slug: str) -> BowlSixSlate | None:
    if not bowl_six_enabled(session, league_slug):
        return None
    today = date.today()
    week_start_dow = rule_int(session, league_slug, "bowl_six_week_start_dow", 0)
    week_start, week_end = _week_bounds_for_date(today, week_start_dow)
    slate = session.scalar(
        select(BowlSixSlate)
        .where(BowlSixSlate.league_slug == league_slug, BowlSixSlate.week_start == week_start)
        .limit(1)
    )
    if slate is None:
        slate = BowlSixSlate(
            league_slug=league_slug,
            week_start=week_start,
            week_end=week_end,
            lock_at=default_lock_at(week_start, league_slug, session),
            status="open",
            label=f"Week of {week_start.isoformat()}",
        )
        session.add(slate)
        session.flush()
    sync_slate_lock_status(session, slate)
    return slate


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
    lineup = session.scalar(
        select(BowlSixLineup)
        .where(BowlSixLineup.slate_id == prev.id, BowlSixLineup.user_id == int(user_id))
        .options(joinedload(BowlSixLineup.picks))
        .limit(1)
    )
    if not lineup or not lineup.picks:
        return set()
    return {int(p.player_id) for p in lineup.picks}


def get_lineup(session: Session, slate_id: int, user_id: int) -> BowlSixLineup | None:
    return session.scalar(
        select(BowlSixLineup)
        .where(BowlSixLineup.slate_id == int(slate_id), BowlSixLineup.user_id == int(user_id))
        .options(joinedload(BowlSixLineup.picks), joinedload(BowlSixLineup.score))
        .limit(1)
    )


def lineup_is_editable(slate: BowlSixSlate) -> bool:
    sync_slate_lock_status(db.session, slate)
    return slate.status == "open" and datetime.utcnow() < slate.lock_at


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


def rs_game_ids_for_slate(league_session: Session, slate: BowlSixSlate) -> list[int]:
    season = get_current_season()
    if season is None:
        return []
    q = select(Game.id).where(
        Game.season_id == int(season.id),
        Game.status == "final",
        Game.game_date.is_not(None),
        Game.game_date >= slate.week_start,
        Game.game_date <= slate.week_end,
    )
    return [int(x) for x in league_session.scalars(q).all()]


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


def score_slate(session: Session, league_session: Session, slate: BowlSixSlate) -> int:
    """Score all submitted lineups; award AP; notify GMs. Returns count scored."""
    if slate.status == "skipped":
        return 0
    if slate.status == "open":
        sync_slate_lock_status(session, slate)
    if slate.status == "open":
        slate.status = "locked"
    season = get_current_season()
    if season is None:
        return 0
    game_ids = rs_game_ids_for_slate(league_session, slate)
    lineups = list(
        session.scalars(
            select(BowlSixLineup)
            .where(BowlSixLineup.slate_id == slate.id, BowlSixLineup.submitted_at.is_not(None))
            .options(joinedload(BowlSixLineup.picks))
        ).all()
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
    slate.scoring_version = int(slate.scoring_version or 0) + 1
    slate.status = "scored"
    refresh_player_week_stats(session, slate, league_session)
    sync_bowl_six_slate_ap_awards(session, slate)
    notify_slate_scored(session, slate)
    return n


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
                source_ref=f"bowl_six:slate:{slate.id}:place:{place}",
            )
    slate.ap_place1_team_id = desired[1][0] if 1 in desired else None
    slate.ap_place2_team_id = desired[2][0] if 2 in desired else None
    slate.ap_place3_team_id = desired[3][0] if 3 in desired else None


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


def gm_season_standings(session: Session, league_slug: str) -> list[dict[str, Any]]:
    """Cumulative BOWL Six points across scored slates this league."""
    rows = session.execute(
        select(
            BowlSixLineup.user_id,
            func.sum(BowlSixLineupScore.total_points),
            func.count(BowlSixLineupScore.id),
        )
        .join(BowlSixSlate, BowlSixSlate.id == BowlSixLineup.slate_id)
        .join(BowlSixLineupScore, BowlSixLineupScore.lineup_id == BowlSixLineup.id)
        .where(
            BowlSixSlate.league_slug == league_slug,
            BowlSixSlate.status == "scored",
        )
        .group_by(BowlSixLineup.user_id)
        .order_by(func.sum(BowlSixLineupScore.total_points).desc())
    ).all()
    result: list[dict[str, Any]] = []
    for uid, total, weeks in rows:
        result.append(
            {
                "user_id": int(uid),
                "season_points": float(total or 0),
                "weeks_played": int(weeks or 0),
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
        session.add(
            GmInAppNotification(
                league_slug=slate.league_slug,
                user_id=int(lineup.user_id),
                kind="bowl_six_scored",
                title=f"BOWL Six week complete — #{rank or '—'}",
                body=f"You scored {pts:.1f} pts this week.{ap_note}",
                article_id=int(slate.id),
            )
        )


def last_scored_slate(session: Session, league_slug: str) -> BowlSixSlate | None:
    return session.scalar(
        select(BowlSixSlate)
        .where(BowlSixSlate.league_slug == league_slug, BowlSixSlate.status == "scored")
        .order_by(BowlSixSlate.week_start.desc())
        .limit(1)
    )

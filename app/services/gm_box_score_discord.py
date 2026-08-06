"""GM-only Discord box score posts (per-team private channels)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, Team
from app.services.discord_events import (
    GM_BOX_SCORE_EVENT_KEY,
    build_league_public_url,
    discord_team_channel_map,
    enqueue_discord_event,
    team_fields_for_discord,
)

_log = logging.getLogger(__name__)

_SCORING_SUMMARY_LIMIT = 8
_TOP_PERF_LIMIT = 3

# Games that flipped to final during FHM import; drained in refresh_after_import
# after boxscore rows are loaded.
_stashed_newly_final_game_ids: set[int] = set()


def stash_newly_final_game_ids(game_ids: set[int] | list[int] | tuple[int, ...]) -> None:
    for raw in game_ids or ():
        try:
            gid = int(raw)
        except (TypeError, ValueError):
            continue
        if gid > 0:
            _stashed_newly_final_game_ids.add(gid)


def drain_stashed_newly_final_game_ids() -> set[int]:
    out = set(_stashed_newly_final_game_ids)
    _stashed_newly_final_game_ids.clear()
    return out


def _fmt_toi(sec: int | None) -> str:
    if sec is None:
        return "—"
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return "—"
    if s < 0:
        return "—"
    return f"{s // 60}:{s % 60:02d}"


def _team_display_name(team: Team | None) -> str:
    if team is None:
        return "Unknown"
    name_fn = getattr(team, "full_display_name", None)
    if callable(name_fn):
        name = str(name_fn() or "").strip()
        if name:
            return name
    city = str(getattr(team, "name", "") or "").strip()
    nick = str(getattr(team, "nickname", "") or "").strip()
    joined = " ".join(p for p in (city, nick) if p).strip()
    return joined or city or "Unknown"


def _pos_label(player: Player | None) -> str:
    raw = str(getattr(player, "position", "") or "").strip().upper()
    if not raw:
        return ""
    # Normalize common FHM forms.
    if raw in {"G", "GOALIE", "GOALTENDER"}:
        return "G"
    if "/" in raw:
        raw = raw.split("/", 1)[0].strip()
    return raw[:3]


def _plus_minus_str(val: int | None) -> str:
    if val is None:
        return "+0"
    try:
        n = int(val)
    except (TypeError, ValueError):
        return "+0"
    if n > 0:
        return f"+{n}"
    return str(n)


def _gr_str(val: float | int | None) -> str:
    if val is None:
        return "—"
    try:
        f = float(val)
    except (TypeError, ValueError):
        return "—"
    if f == int(f):
        return str(int(f))
    return f"{f:.1f}".rstrip("0").rstrip(".")


def _strength_note(strength: str | None) -> str:
    s = str(strength or "").strip().lower()
    if not s or s in {"even", "ev", "5v5"}:
        return ""
    if "pp" in s or "power" in s:
        return " PP"
    if "sh" in s or "short" in s or "pk" in s:
        return " SH"
    if "en" in s or "empty" in s:
        return " EN"
    return f" {str(strength).strip()}"


def _period_label(period: int) -> str:
    if period <= 3:
        return f"P{period}"
    if period == 4:
        return "OT"
    return f"OT{period - 3}"


def _format_date_long(d: date | None) -> str:
    if d is None:
        return ""
    # Wednesday, December 2, 2043 (no leading zero on day)
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def build_gm_box_score_text(
    league_session,
    *,
    game: Game,
    recipient_team: Team,
    league_slug: str = "",
) -> str:
    """Build Discord text body for one recipient franchise."""
    home = league_session.get(Team, int(game.home_team_id))
    away = league_session.get(Team, int(game.away_team_id))
    home_name = _team_display_name(home)
    away_name = _team_display_name(away)
    recipient_name = _team_display_name(recipient_team)

    home_score = int(game.home_score or 0)
    away_score = int(game.away_score or 0)
    date_label = _format_date_long(game.game_date)

    lines: list[str] = []
    title = f"**{recipient_name} Box Score**"
    if date_label:
        title = f"{title} — {date_label}"
    lines.append(title)

    score_line = f"**{away_name} {away_score} @ {home_name} {home_score}**"
    extras: list[str] = []
    if bool(getattr(game, "went_to_shootout", False)):
        extras.append("SO")
    elif bool(getattr(game, "went_to_overtime", False)):
        extras.append("OT")
    if extras:
        score_line = f"{score_line} ({', '.join(extras)})"
    lines.append(score_line)
    lines.append("")

    # Period scores from ScoringEvent when available; fall back to Game period columns.
    period_bits = _period_score_bits(league_session, game)
    if period_bits:
        lines.append(f"**Periods:** {', '.join(period_bits)}")

    home_shots = game.home_shots
    away_shots = game.away_shots
    if home_shots is not None or away_shots is not None:
        lines.append(
            f"**Shots:** {away_name} {int(away_shots or 0)} - {home_name} {int(home_shots or 0)}"
        )

    away_pp = f"{int(game.pp_goals_away or 0)}/{int(game.pp_opp_away or 0)}"
    home_pp = f"{int(game.pp_goals_home or 0)}/{int(game.pp_opp_home or 0)}"
    lines.append(f"**Special Teams:** {away_name} {away_pp} | {home_name} {home_pp}")

    hits_away = game.hits_away
    hits_home = game.hits_home
    blocks_away, blocks_home = _team_block_totals(league_session, game)
    phys_parts: list[str] = []
    if hits_away is not None or hits_home is not None:
        phys_parts.append(
            f"Hits {away_name} {int(hits_away or 0)} - {home_name} {int(hits_home or 0)}"
        )
    if blocks_away or blocks_home:
        phys_parts.append(
            f"Blocks {away_name} {blocks_away} - {home_name} {blocks_home}"
        )
    if phys_parts:
        lines.append(f"**Physical:** {' | '.join(phys_parts)}")

    stars = _star_labels(league_session, game)
    if stars:
        lines.append(f"**Stars:** {', '.join(stars)}")

    lines.append("")
    lines.append("**Goalies**")
    for gl in _goalie_lines(league_session, game, home_id=int(game.home_team_id), away_id=int(game.away_team_id)):
        lines.append(gl)

    lines.append("")
    lines.append(f"**{home_name} Top Performances**")
    home_top = _top_performances(league_session, game, int(game.home_team_id))
    lines.extend(home_top or ["—"])

    lines.append("")
    lines.append(f"**{away_name} Top Performances**")
    away_top = _top_performances(league_session, game, int(game.away_team_id))
    lines.extend(away_top or ["—"])

    scoring = _scoring_summary_lines(league_session, game)
    if scoring:
        lines.append("")
        lines.append("**Scoring Summary**")
        lines.extend(scoring)

    slug = str(league_slug or "").strip() or _league_slug_hint()
    url = build_league_public_url(slug, f"/game/{int(game.id)}") if slug else ""
    if url:
        lines.append("")
        lines.append(f"[Full Box Score]({url})")

    return "\n".join(lines).strip()


def _league_slug_hint() -> str:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    except Exception:
        pass
    return ""


def _period_score_bits(league_session, game: Game) -> list[str]:
    from collections import defaultdict

    away_id = int(game.away_team_id)
    home_id = int(game.home_team_id)
    by_away: dict[int, int] = defaultdict(int)
    by_home: dict[int, int] = defaultdict(int)
    for ev in list(getattr(game, "scoring_events", None) or []):
        try:
            period = int(ev.period or 1)
        except (TypeError, ValueError):
            period = 1
        if int(ev.scoring_team_id or 0) == away_id:
            by_away[period] += 1
        elif int(ev.scoring_team_id or 0) == home_id:
            by_home[period] += 1

    if not by_away and not by_home:
        # Fall back to stored period score columns when present.
        bits: list[str] = []
        for label, a_attr, h_attr in (
            ("1", "score_away_p1", "score_home_p1"),
            ("2", "score_away_p2", "score_home_p2"),
            ("3", "score_away_p3", "score_home_p3"),
        ):
            a = getattr(game, a_attr, None)
            h = getattr(game, h_attr, None)
            if a is None and h is None:
                continue
            bits.append(f"{int(a or 0)}-{int(h or 0)}")
        ot_a = getattr(game, "score_away_ot", None)
        ot_h = getattr(game, "score_home_ot", None)
        if ot_a is not None or ot_h is not None:
            bits.append(f"{int(ot_a or 0)}-{int(ot_h or 0)} OT")
        return bits

    max_p = max(list(by_away.keys()) + list(by_home.keys()) + [3])
    bits = []
    for p in range(1, 4):
        bits.append(f"{by_away[p]}-{by_home[p]}")
    if max_p > 3:
        ota = sum(c for pp, c in by_away.items() if pp > 3)
        oth = sum(c for pp, c in by_home.items() if pp > 3)
        bits.append(f"{ota}-{oth} OT")
    return bits


def _team_block_totals(league_session, game: Game) -> tuple[int, int]:
    away_id = int(game.away_team_id)
    home_id = int(game.home_team_id)
    away_b = 0
    home_b = 0
    for row in list(getattr(game, "skater_lines", None) or []):
        try:
            blocks = int(row.blocked_shots or 0)
        except (TypeError, ValueError):
            blocks = 0
        tid = int(row.team_id or 0)
        if tid == away_id:
            away_b += blocks
        elif tid == home_id:
            home_b += blocks
    return away_b, home_b


def _star_labels(league_session, game: Game) -> list[str]:
    out: list[str] = []
    for fhm_pid in (
        game.fhm_star1_player_id,
        game.fhm_star2_player_id,
        game.fhm_star3_player_id,
    ):
        if fhm_pid is None:
            continue
        player = league_session.scalars(
            select(Player).where(Player.fhm_player_id == str(fhm_pid)).limit(1)
        ).first()
        if player is None:
            continue
        pos = _pos_label(player)
        name = str(player.full_name or "").strip() or "Unknown"
        out.append(f"{pos} {name}".strip() if pos else name)
    return out


def _goalie_lines(league_session, game: Game, *, home_id: int, away_id: int) -> list[str]:
    rows = list(getattr(game, "goalie_lines", None) or [])
    if not rows:
        rows = list(
            league_session.scalars(
                select(GameGoalieStat).where(GameGoalieStat.game_id == int(game.id))
            ).all()
        )
    # Prefer higher TOI per team (starter).
    best: dict[int, GameGoalieStat] = {}
    for row in rows:
        tid = int(row.team_id or 0)
        if tid not in (home_id, away_id):
            continue
        cur = best.get(tid)
        if cur is None or int(row.toi_seconds or 0) > int(cur.toi_seconds or 0):
            best[tid] = row
    lines: list[str] = []
    for tid, label in ((away_id, "Away"), (home_id, "Home")):
        row = best.get(tid)
        if row is None:
            continue
        player = league_session.get(Player, int(row.player_id))
        team = league_session.get(Team, tid)
        name = str(getattr(player, "full_name", "") or "").strip() or "Unknown"
        abbr = str(getattr(team, "abbreviation", "") or "").strip() or label
        sa = int(row.shots_against or 0)
        sv = int(row.saves or 0)
        sv_pct = f"{(100.0 * sv / sa):.1f}%" if sa > 0 else "—"
        lines.append(
            f"{abbr} G {name} — {sv}/{sa} SV ({sv_pct}) · TOI {_fmt_toi(row.toi_seconds)}"
        )
    return lines


def _top_performances(league_session, game: Game, team_id: int) -> list[str]:
    rows = [
        r
        for r in list(getattr(game, "skater_lines", None) or [])
        if int(getattr(r, "team_id", 0) or 0) == int(team_id)
    ]
    if not rows:
        rows = list(
            league_session.scalars(
                select(GameSkaterStat).where(
                    GameSkaterStat.game_id == int(game.id),
                    GameSkaterStat.team_id == int(team_id),
                )
            ).all()
        )
    rows.sort(
        key=lambda r: (
            -float(r.game_rating or 0),
            -int(r.goals or 0),
            -int(r.assists or 0),
            -int(r.toi_seconds or 0),
        )
    )
    out: list[str] = []
    for row in rows[:_TOP_PERF_LIMIT]:
        player = league_session.get(Player, int(row.player_id))
        pos = _pos_label(player)
        name = str(getattr(player, "full_name", "") or "").strip() or "Unknown"
        head = f"{pos} {name}".strip() if pos else name
        g = int(row.goals or 0)
        a = int(row.assists or 0)
        pm = _plus_minus_str(row.plus_minus)
        gr = _gr_str(row.game_rating)
        toi = _fmt_toi(row.toi_seconds)
        out.append(f"{head} — {g}G {a}A {pm} / GR {gr} / TOI {toi}")
    return out


def _scoring_summary_lines(league_session, game: Game) -> list[str]:
    events = list(getattr(game, "scoring_events", None) or [])
    if not events:
        return []
    events = sorted(
        events,
        key=lambda e: (int(e.period or 1), str(e.time_elapsed or "")),
    )
    lines: list[str] = []
    shown = events[:_SCORING_SUMMARY_LIMIT]
    for ev in shown:
        period = int(ev.period or 1)
        team = league_session.get(Team, int(ev.scoring_team_id)) if ev.scoring_team_id else None
        abbr = str(getattr(team, "abbreviation", "") or "").strip() or "???"
        scorer = league_session.get(Player, int(ev.scorer_player_id)) if ev.scorer_player_id else None
        scorer_name = str(getattr(scorer, "full_name", "") or "").strip() or "Unknown"
        assists: list[str] = []
        for aid in (ev.assist1_player_id, ev.assist2_player_id):
            if not aid:
                continue
            ap = league_session.get(Player, int(aid))
            an = str(getattr(ap, "full_name", "") or "").strip()
            if an:
                assists.append(an)
        assist_bit = f" ({', '.join(assists)})" if assists else ""
        strength = _strength_note(ev.strength)
        clock = str(ev.time_elapsed or "").strip() or "—"
        lines.append(
            f"{_period_label(period)} {clock} {abbr} {scorer_name}{assist_bit}{strength}"
        )
    remaining = len(events) - len(shown)
    if remaining > 0:
        lines.append(f"…and {remaining} more")
    return lines


def build_gm_box_score_payload(
    league_session,
    *,
    league_slug: str,
    game: Game,
    recipient_team: Team,
    discord_channel_id: str,
) -> dict[str, Any]:
    body = build_gm_box_score_text(
        league_session,
        game=game,
        recipient_team=recipient_team,
        league_slug=league_slug,
    )
    fields = team_fields_for_discord(recipient_team)
    url = build_league_public_url(league_slug, f"/game/{int(game.id)}")
    payload: dict[str, Any] = {
        "title": f"{_team_display_name(recipient_team)} Box Score",
        "body": body,
        "body_preview": body[:280],
        "has_image": False,
        "game_id": int(game.id),
        "recipient_team_id": int(recipient_team.id),
        "discord_channel_id": str(discord_channel_id or "").strip(),
        "source_type": "gm_box_score",
        "source_id": f"{int(game.id)}:{int(recipient_team.id)}",
    }
    if url:
        payload["url"] = url
    payload.update(fields)
    return payload


def enqueue_gm_box_scores_for_games(
    site_session,
    league_session,
    *,
    league_slug: str,
    game_ids: set[int] | list[int] | tuple[int, ...],
) -> dict[str, int]:
    """Enqueue home/away GM box score events for newly final games."""
    slug = str(league_slug or "").strip()
    stats = {"games": 0, "queued": 0, "skipped_no_channel": 0, "skipped_invalid": 0}
    if not slug:
        return stats
    ids: list[int] = []
    for raw in game_ids or ():
        try:
            gid = int(raw)
        except (TypeError, ValueError):
            continue
        if gid > 0:
            ids.append(gid)
    if not ids:
        return stats

    channel_map = discord_team_channel_map(site_session, slug)
    for gid in sorted(set(ids)):
        game = league_session.get(Game, gid)
        if game is None:
            stats["skipped_invalid"] += 1
            continue
        if str(game.status or "").strip().lower() != "final":
            stats["skipped_invalid"] += 1
            continue
        if game.home_score is None or game.away_score is None:
            stats["skipped_invalid"] += 1
            continue
        stats["games"] += 1
        for tid in (int(game.home_team_id), int(game.away_team_id)):
            cid = str(channel_map.get(tid) or "").strip()
            if not cid:
                stats["skipped_no_channel"] += 1
                continue
            team = league_session.get(Team, tid)
            if team is None:
                stats["skipped_invalid"] += 1
                continue
            try:
                payload = build_gm_box_score_payload(
                    league_session,
                    league_slug=slug,
                    game=game,
                    recipient_team=team,
                    discord_channel_id=cid,
                )
                created = enqueue_discord_event(
                    site_session,
                    league_slug=slug,
                    event_key=GM_BOX_SCORE_EVENT_KEY,
                    payload=payload,
                    created_by_user_id=None,
                    source_type="gm_box_score",
                    source_id=f"{gid}:{tid}",
                )
                if created is not None and str(created.status or "") == "pending":
                    stats["queued"] += 1
            except Exception:
                _log.exception(
                    "Failed to enqueue GM box score for game %s team %s (%s)",
                    gid,
                    tid,
                    slug,
                )
    return stats


def notify_gm_box_scores_after_import(
    site_session,
    league_session,
    *,
    league_slug: str,
    game_ids: set[int] | None = None,
) -> dict[str, int]:
    """Drain stashed finals (plus optional explicit ids) and enqueue Discord box scores."""
    ids = set(game_ids or ())
    ids |= drain_stashed_newly_final_game_ids()
    if not ids:
        return {"games": 0, "queued": 0, "skipped_no_channel": 0, "skipped_invalid": 0}
    return enqueue_gm_box_scores_for_games(
        site_session,
        league_session,
        league_slug=league_slug,
        game_ids=ids,
    )

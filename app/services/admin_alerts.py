from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.models import Game, ImportLog, Season, Team, TeamStanding
from app.site_models import AdminAuditLog, GmApprovalRequest


def _now_utc() -> datetime:
    return datetime.utcnow()


def build_admin_alerts_snapshot(session, league_slug: str) -> dict:
    now = _now_utc()
    alerts: list[dict[str, object]] = []

    current_season = session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current_season is None:
        current_season = session.scalar(select(Season).order_by(Season.id.desc()).limit(1))

    season_id = int(current_season.id) if current_season else None
    teams_total = int(session.scalar(select(func.count(Team.id))) or 0)

    latest_import = session.scalar(select(ImportLog).order_by(ImportLog.id.desc()).limit(1))
    latest_import_status = str(getattr(latest_import, "status", "") or "").strip().lower()
    latest_import_finished = getattr(latest_import, "finished_at", None)
    import_age_hours = None
    if latest_import_finished:
        import_age_hours = int((now - latest_import_finished).total_seconds() // 3600)

    if latest_import is None:
        alerts.append(
            {
                "severity": "warn",
                "title": "No import logs found",
                "detail": "No import history exists yet; data freshness cannot be evaluated.",
            }
        )
    else:
        if latest_import_status not in {"success", "ok"}:
            alerts.append(
                {
                    "severity": "critical",
                    "title": "Latest import did not succeed",
                    "detail": f"Status: {latest_import_status or 'unknown'}",
                }
            )
        if import_age_hours is not None and import_age_hours > 48:
            alerts.append(
                {
                    "severity": "warn",
                    "title": "Import appears stale",
                    "detail": f"Latest import completed about {import_age_hours} hours ago.",
                }
            )

    late_scheduled_games = 0
    finals_total = 0
    standings_rows = 0
    if season_id is not None:
        standings_rows = int(
            session.scalar(select(func.count(TeamStanding.id)).where(TeamStanding.season_id == season_id)) or 0
        )
        finals_total = int(
            session.scalar(
                select(func.count(Game.id)).where(Game.season_id == season_id, Game.status == "final")
            )
            or 0
        )
        cutoff: date = (now - timedelta(days=2)).date()
        late_scheduled_games = int(
            session.scalar(
                select(func.count(Game.id)).where(
                    Game.season_id == season_id,
                    Game.game_date.is_not(None),
                    Game.game_date < cutoff,
                    Game.status != "final",
                )
            )
            or 0
        )
        if standings_rows > 0 and teams_total > 0 and standings_rows != teams_total:
            alerts.append(
                {
                    "severity": "warn",
                    "title": "Standings row mismatch",
                    "detail": f"Current season has {standings_rows} standings rows vs {teams_total} teams.",
                }
            )
        if late_scheduled_games > 0:
            alerts.append(
                {
                    "severity": "warn",
                    "title": "Past-due scheduled games",
                    "detail": f"{late_scheduled_games} games are older than 2 days and still not marked final.",
                }
            )

    pending_ops = int(
        session.scalar(
            select(func.count(GmApprovalRequest.id)).where(
                GmApprovalRequest.league_slug == league_slug,
                GmApprovalRequest.status == "pending",
            )
        )
        or 0
    )
    if pending_ops >= 10:
        sev = "warn" if pending_ops < 25 else "critical"
        alerts.append(
            {
                "severity": sev,
                "title": "Operations queue backlog",
                "detail": f"{pending_ops} requests are pending review.",
            }
        )

    risky_actions = {
        "control_center_restore_backup",
        "control_center_execute_import",
        "control_center_execute_refresh",
        "control_center_season_rollover_execute",
        "admin_roles_update",
        "league_rules_update",
        "undo_apply",
        "story_schedule_live_dispatch",
        "story_schedule_retry_live_dispatch",
    }
    window_start = now - timedelta(hours=24)
    recent_risky = int(
        session.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.league_slug == league_slug,
                AdminAuditLog.created_at >= window_start,
                AdminAuditLog.action.in_(tuple(risky_actions)),
            )
        )
        or 0
    )
    if recent_risky > 0:
        alerts.append(
            {
                "severity": "info",
                "title": "Recent high-impact admin operations",
                "detail": (
                    f"{recent_risky} high-impact actions in last 24 hours "
                    f"(restore/import/rollover, roles, rules, undo, story live dispatch). "
                    f"Review Audit log for detail."
                ),
            }
        )

    severity_rank = {"critical": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_rank.get(str(a.get("severity")), 3), str(a.get("title") or "")))

    cards = {
        "critical_alerts": sum(1 for a in alerts if a.get("severity") == "critical"),
        "warn_alerts": sum(1 for a in alerts if a.get("severity") == "warn"),
        "info_alerts": sum(1 for a in alerts if a.get("severity") == "info"),
        "pending_operations": pending_ops,
        "late_scheduled_games": late_scheduled_games,
        "final_games_current_season": finals_total,
    }
    return {
        "generated_at_utc": now.isoformat(timespec="seconds"),
        "current_season_label": str(getattr(current_season, "label", "") or "—"),
        "cards": cards,
        "alerts": alerts,
        "metrics": {
            "teams_total": teams_total,
            "standings_rows": standings_rows,
            "import_age_hours": import_age_hours,
            "latest_import_status": latest_import_status or "unknown",
            "recent_high_impact_actions_24h": recent_risky,
        },
    }

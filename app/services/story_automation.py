from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import select

from app.site_models import NewsArticle, StoryPublishSchedule

ALLOWED_STORY_CHANNELS = ("site", "discord")
TERMINAL_STATUSES = frozenset({"dispatched", "cancelled"})
ACTIVE_STATUSES = frozenset({"scheduled", "failed"})


def validate_schedule_datetime(scheduled_for_utc: datetime) -> tuple[bool, str]:
    if scheduled_for_utc is None:
        return False, "Scheduled time is required."
    if scheduled_for_utc < datetime.utcnow() - timedelta(days=365 * 5):
        return False, "Scheduled time is unreasonably far in the past."
    if scheduled_for_utc > datetime.utcnow() + timedelta(days=365 * 2):
        return False, "Scheduled time is too far in the future (max ~2 years)."
    return True, ""


def schedule_story_publish(
    session,
    *,
    league_slug: str,
    article_id: int,
    channel: str,
    scheduled_for_utc: datetime,
    dry_run_only: bool,
    created_by_user_id: int | None,
) -> StoryPublishSchedule:
    ch = channel if channel in ALLOWED_STORY_CHANNELS else "site"
    row = StoryPublishSchedule(
        league_slug=league_slug,
        article_id=int(article_id),
        channel=ch,
        status="scheduled",
        scheduled_for_utc=scheduled_for_utc,
        dry_run_only=bool(dry_run_only),
        payload_json=json.dumps({}),
        last_result_json=json.dumps({}),
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        attempt_count=0,
        last_error="",
        last_attempt_at=None,
    )
    session.add(row)
    session.flush()
    return row


def list_story_schedules(session, *, league_slug: str, limit: int = 100) -> list[StoryPublishSchedule]:
    return session.scalars(
        select(StoryPublishSchedule)
        .where(StoryPublishSchedule.league_slug == league_slug)
        .order_by(StoryPublishSchedule.scheduled_for_utc.desc(), StoryPublishSchedule.id.desc())
        .limit(max(1, int(limit)))
    ).all()


def dry_run_dispatch_story(session, *, schedule_row: StoryPublishSchedule) -> dict:
    article = session.get(NewsArticle, int(schedule_row.article_id))
    if not article:
        return {
            "ok": False,
            "message": "Article not found.",
            "channel": schedule_row.channel,
            "article_id": int(schedule_row.article_id),
        }
    payload = {
        "title": str(article.title or "").strip(),
        "body_preview": str(article.body or "").strip()[:240],
        "category": str(article.category or ""),
        "article_status": str(article.status or ""),
        "channel": str(schedule_row.channel or "site"),
        "scheduled_for_utc": schedule_row.scheduled_for_utc.isoformat(timespec="seconds"),
        "dry_run_only": bool(schedule_row.dry_run_only),
        "schedule_status": str(schedule_row.status or ""),
    }
    return {"ok": True, "message": "Dry-run dispatch preview only (no external calls, no DB publish).", "payload": payload}


def _post_discord_webhook(webhook_url: str, content: str) -> dict:
    body = json.dumps({"content": content[:2000]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = int(resp.getcode() or 0)
            if code >= 400:
                return {"ok": False, "message": f"Discord webhook HTTP {code}"}
            return {"ok": True, "message": "Posted to Discord webhook."}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        return {"ok": False, "message": f"Discord HTTPError {exc.code}: {detail or str(exc)}"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:500]}


def execute_story_dispatch(
    session,
    *,
    schedule_row: StoryPublishSchedule,
    league_slug: str,
    discord_webhook_url: str,
    site_public_base_url: str,
    league_display_name: str,
    news_article_ap_points: int,
) -> dict:
    """Live dispatch: publish to site and/or Discord. Updates schedule_row in-session.

    Idempotent: if already ``dispatched``, returns ok without side effects.
    """
    from app.services.ap_service import publish_news_and_maybe_award_ap
    from app.services.gm_notifications import notify_news_approved

    st = str(schedule_row.status or "").strip().lower()
    if st == "dispatched":
        return {
            "ok": True,
            "message": "Already dispatched (idempotent).",
            "idempotent": True,
            "channel": schedule_row.channel,
        }
    if st == "cancelled":
        return {"ok": False, "message": "Schedule was cancelled.", "blocked": True}
    if st not in ACTIVE_STATUSES:
        return {"ok": False, "message": f"Cannot dispatch from status '{st}'.", "blocked": True}
    if bool(schedule_row.dry_run_only):
        return {
            "ok": False,
            "message": "This schedule is dry-run-only; enable live mode on the schedule or create a live-ready schedule.",
            "blocked": True,
        }

    article = session.get(NewsArticle, int(schedule_row.article_id))
    if not article or str(article.league_slug) != str(league_slug):
        schedule_row.status = "failed"
        schedule_row.last_error = "Article not found or wrong league."
        schedule_row.last_result_json = json.dumps({"ok": False, "message": schedule_row.last_error})
        schedule_row.processed_at = datetime.utcnow()
        return {"ok": False, "message": schedule_row.last_error}

    schedule_row.attempt_count = int(schedule_row.attempt_count or 0) + 1
    schedule_row.last_attempt_at = datetime.utcnow()
    schedule_row.last_error = ""

    channel = str(schedule_row.channel or "site").strip().lower()
    if channel not in ALLOWED_STORY_CHANNELS:
        channel = "site"

    headline_link = ""
    base = (site_public_base_url or "").rstrip("/")
    if base:
        headline_link = f"{base}/league-headlines#a{article.id}"

    results: dict = {"channel": channel, "site": None, "discord": None}

    try:
        if article.status == "pending":
            publish_news_and_maybe_award_ap(article, points=int(news_article_ap_points))
            notify_news_approved(league_slug, article)
            results["site"] = {"published": True, "article_id": int(article.id)}
        elif article.status == "published":
            results["site"] = {"published": True, "already_live": True, "article_id": int(article.id)}
        else:
            schedule_row.status = "failed"
            schedule_row.last_error = f"Article status '{article.status}' cannot be published by automation."
            schedule_row.last_result_json = json.dumps({"ok": False, "message": schedule_row.last_error, "results": results})
            schedule_row.processed_at = datetime.utcnow()
            return {"ok": False, "message": schedule_row.last_error, "results": results}

        if channel == "discord":
            wh = (discord_webhook_url or "").strip()
            if not wh:
                schedule_row.status = "failed"
                schedule_row.last_error = "Discord channel selected but DISCORD_STORY_WEBHOOK_URL is not configured."
                schedule_row.last_result_json = json.dumps({"ok": False, "message": schedule_row.last_error, "results": results})
                schedule_row.processed_at = datetime.utcnow()
                return {"ok": False, "message": schedule_row.last_error, "results": results}

            title = str(article.title or "").strip()[:200]
            line = f"**{league_display_name}** — {title}"
            if headline_link:
                msg = f"{line}\n{headline_link}"
            else:
                msg = f"{line}\n(Set SITE_PUBLIC_BASE_URL for article links.)"
            dres = _post_discord_webhook(wh, msg)
            results["discord"] = dres
            if not dres.get("ok"):
                schedule_row.status = "failed"
                schedule_row.last_error = str(dres.get("message") or "Discord post failed")
                schedule_row.last_result_json = json.dumps({"ok": False, "message": schedule_row.last_error, "results": results})
                schedule_row.processed_at = datetime.utcnow()
                return {"ok": False, "message": schedule_row.last_error, "results": results}

        schedule_row.status = "dispatched"
        schedule_row.processed_at = datetime.utcnow()
        out = {"ok": True, "message": "Dispatch complete.", "results": results, "channel": channel}
        schedule_row.last_result_json = json.dumps(out)
        return out
    except Exception as exc:
        schedule_row.status = "failed"
        schedule_row.last_error = str(exc)[:500]
        schedule_row.last_result_json = json.dumps({"ok": False, "message": schedule_row.last_error})
        schedule_row.processed_at = datetime.utcnow()
        return {"ok": False, "message": schedule_row.last_error}

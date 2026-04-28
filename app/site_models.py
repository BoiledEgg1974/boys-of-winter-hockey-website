"""Models stored in the ``site`` SQLAlchemy bind (``site_membership.db``)."""
from __future__ import annotations

import json
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.league_db import db


class User(db.Model, UserMixin):
    __tablename__ = "site_users"
    __bind_key__ = "site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    discord_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_role: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    memberships: Mapped[list["GmLeagueMembership"]] = relationship(back_populates="user")


class GmLeagueMembership(db.Model):
    __tablename__ = "gm_league_memberships"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("user_id", "league_slug", name="uq_gm_user_league"),
        Index("ix_gm_league_team", "league_slug", "team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    terms_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="memberships")


class GmLeagueMessage(db.Model):
    """In-site direct messages between GMs of the same league (no email)."""

    __tablename__ = "gm_league_messages"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_gm_msg_league_to_unread", "league_slug", "to_user_id", "read_at"),
        Index("ix_gm_msg_league_created", "league_slug", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class GmInAppNotification(db.Model):
    """League-scoped notices for GMs (e.g. news moderation), shown in GM Messages — no email."""

    __tablename__ = "gm_in_app_notifications"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_gm_notif_user_league_unread", "user_id", "league_slug", "read_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    article_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class NewsArticle(db.Model):
    __tablename__ = "news_articles"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_news_league_pub", "league_slug", "status", "published_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="general_messages", nullable=False)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ap_awarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    image_rel_path: Mapped[str | None] = mapped_column(String(384), nullable=True)


class ApRedemptionCatalog(db.Model):
    __tablename__ = "ap_redemption_catalog"
    __bind_key__ = "site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_group: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cost_ap: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ApLedgerEntry(db.Model):
    __tablename__ = "ap_ledger_entries"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_ap_ledger_team", "league_slug", "team_id"), Index("ix_ap_ledger_source_ref", "source_ref"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(48), nullable=False)
    meta_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(191), nullable=True)


class ApRedemptionRequest(db.Model):
    __tablename__ = "ap_redemption_requests"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_ap_req_status", "status", "league_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    lines_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    total_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    admin_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"
    __bind_key__ = "site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AdminUndoAction(db.Model):
    __tablename__ = "admin_undo_actions"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_admin_undo_league_created", "league_slug", "created_at"),
        Index("ix_admin_undo_reverted", "league_slug", "is_reverted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    after_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_reverted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reverted_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HomepageModuleSetting(db.Model):
    __tablename__ = "homepage_module_settings"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_slug", "module_key", name="uq_home_mod_league_key"),
        Index("ix_home_mod_league_sort", "league_slug", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SiteAnnouncement(db.Model):
    __tablename__ = "site_announcements"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_site_announce_league_active", "league_slug", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LeagueRuleSetting(db.Model):
    __tablename__ = "league_rule_settings"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_slug", "rule_key", name="uq_league_rule_key"),
        Index("ix_league_rule_league", "league_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class GmApprovalRequest(db.Model):
    __tablename__ = "gm_approval_requests"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_gm_approval_league_status", "league_slug", "status"),
        Index("ix_gm_approval_team", "league_slug", "team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    admin_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    processed_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StoryPublishSchedule(db.Model):
    __tablename__ = "story_publish_schedules"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_story_sched_league_status", "league_slug", "status"),
        Index("ix_story_sched_run_at", "scheduled_for_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="site", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="scheduled", nullable=False)
    scheduled_for_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dry_run_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_result_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    article: Mapped["NewsArticle"] = relationship()


class AwardsVotingCycle(db.Model):
    __tablename__ = "awards_voting_cycles"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_awards_cycle_league_status", "league_slug", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season_label: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AwardsVoteBallot(db.Model):
    __tablename__ = "awards_vote_ballots"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_awards_ballot_cycle_award", "league_slug", "cycle_id", "award_key"),
        Index("ix_awards_ballot_voter", "league_slug", "voter_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("awards_voting_cycles.id"), nullable=False)
    award_key: Mapped[str] = mapped_column(String(64), nullable=False)
    voter_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    candidate_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    rank_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    points_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    cycle: Mapped["AwardsVotingCycle"] = relationship()


class MemberWatchlistItem(db.Model):
    __tablename__ = "member_watchlist_items"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_watchlist_user_league", "user_id", "league_slug"),
        Index("ix_watchlist_league_target", "league_slug", "target_type", "target_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)  # player|team|article|gm
    target_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DiscordChannelRoute(db.Model):
    __tablename__ = "discord_channel_routes"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_slug", "event_key", name="uq_discord_route_league_event"),
        Index("ix_discord_route_league_event", "league_slug", "event_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DiscordOutboundEvent(db.Model):
    __tablename__ = "discord_outbound_events"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_discord_event_status_created", "status", "created_at"),
        Index("ix_discord_event_league_status", "league_slug", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|sent|failed|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DiscordBotHeartbeat(db.Model):
    __tablename__ = "discord_bot_heartbeats"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_discord_hb_league_seen", "league_slug", "last_seen_at"),
        Index("ix_discord_hb_bot", "bot_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bot_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    bot_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    guild_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    extra_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


def meta_dict(entry: ApLedgerEntry) -> dict:
    try:
        return json.loads(entry.meta_json or "{}")
    except json.JSONDecodeError:
        return {}

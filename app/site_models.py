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


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_pwd_reset_lookup", "token_hash", "used_at"),
        Index("ix_pwd_reset_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class SiteBannedIdentity(db.Model):
    """Archived ban list: blocks new registration by email and records why access was removed."""

    __tablename__ = "site_banned_identities"
    __bind_key__ = "site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_norm: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    discord_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("site_users.id"), nullable=True)


class GmLeagueMembership(db.Model):
    __tablename__ = "gm_league_memberships"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("user_id", "league_slug", name="uq_gm_user_league"),
        Index("ix_gm_league_team", "league_slug", "team_id"),
        Index("ix_gm_league_fhm_team", "league_slug", "fhm_team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # Primary key in that league's ``teams`` table (FK target for ORM / roster queries).
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # FHM franchise / export ``team_id`` (e.g. Washington 22). Used with CSVs and messaging by franchise.
    fhm_team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class NewsArticleComment(db.Model):
    """GM / admin comment on a published Around the League article (site DB)."""

    __tablename__ = "news_article_comments"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_news_article_comment_article", "article_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    article: Mapped["NewsArticle"] = relationship()
    user: Mapped["User"] = relationship()


class NewsArticleVote(db.Model):
    """Per-GM thumbs up (+1) or down (-1) on an article; one row per (article, user)."""

    __tablename__ = "news_article_votes"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("article_id", "user_id", name="uq_news_article_vote_article_user"),
        Index("ix_news_article_vote_article", "article_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    article: Mapped["NewsArticle"] = relationship()
    user: Mapped["User"] = relationship()


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


class GmTradeProposal(db.Model):
    """GM-to-GM trade negotiation; no league roster mutation until CSV imports."""

    __tablename__ = "gm_trade_proposals"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_gm_trade_league_status", "league_slug", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    from_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    to_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_partner", nullable=False)
    ledger_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    commissioner_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    commissioner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    partner_acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    commissioner_acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    discord_channel_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DiscordLeagueBotConfig(db.Model):
    __tablename__ = "discord_league_bot_config"
    __bind_key__ = "site"
    __table_args__ = (UniqueConstraint("league_slug", name="uq_discord_bot_cfg_league"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    guild_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DiscordDeliveredSource(db.Model):
    __tablename__ = "discord_delivered_sources"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_slug", "source_type", "source_id", name="uq_discord_delivered_source"),
        Index("ix_discord_delivered_league", "league_slug", "delivered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    outbound_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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


class LeagueDraft(db.Model):
    """League-run draft hub (one row per event; separate from FHM Draft / DraftPick)."""

    __tablename__ = "league_drafts"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_league_draft_slug_status", "league_slug", "status"),
        Index("ix_league_draft_slug_created", "league_slug", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="Draft", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="setup", nullable=False)  # setup|live|completed
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rounds: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    picks_per_round: Mapped[int] = mapped_column(Integer, default=27, nullable=False)
    timer_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    empty_queue_timer_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    min_age_years: Mapped[int] = mapped_column(Integer, nullable=False)
    min_anchor_month: Mapped[int] = mapped_column(Integer, nullable=False)
    min_anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    max_age_years: Mapped[int] = mapped_column(Integer, nullable=False)
    max_anchor_month: Mapped[int] = mapped_column(Integer, nullable=False)
    max_anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    timeline_year: Mapped[int] = mapped_column(Integer, nullable=False)
    current_slot_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pick_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pick_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timer_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timer_paused_remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_extended_for_slot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    awaiting_admin_resolution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    board_ranks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    slots: Mapped[list["LeagueDraftSlot"]] = relationship(
        back_populates="draft", order_by="LeagueDraftSlot.overall_pick"
    )
    picks: Mapped[list["LeagueDraftPick"]] = relationship(
        back_populates="draft", order_by="LeagueDraftPick.overall_pick"
    )
    queue_items: Mapped[list["LeagueDraftQueueItem"]] = relationship(back_populates="draft")
    soundbites: Mapped[list["LeagueDraftSoundbite"]] = relationship(back_populates="draft")


class LeagueDraftSlot(db.Model):
    __tablename__ = "league_draft_slots"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_draft_id", "overall_pick", name="uq_league_draft_slot_overall"),
        Index("ix_league_draft_slot_draft", "league_draft_id", "overall_pick"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_draft_id: Mapped[int] = mapped_column(ForeignKey("league_drafts.id"), nullable=False)
    overall_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    original_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    forfeited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # "", "gold", or "silver" — admin-set after running the boost lottery.
    boost_tier: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    draft: Mapped["LeagueDraft"] = relationship(back_populates="slots")


class LeagueDraftPick(db.Model):
    __tablename__ = "league_draft_picks"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_draft_id", "overall_pick", name="uq_league_draft_pick_overall"),
        Index("ix_league_draft_pick_draft", "league_draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_draft_id: Mapped[int] = mapped_column(ForeignKey("league_drafts.id"), nullable=False)
    overall_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="gm", nullable=False)  # gm|auto_queue|admin
    picked_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    draft: Mapped["LeagueDraft"] = relationship(back_populates="picks")


class LeagueDraftQueueItem(db.Model):
    __tablename__ = "league_draft_queue_items"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_league_draft_queue_draft_user", "league_draft_id", "user_id", "sort_order"),
        UniqueConstraint("league_draft_id", "user_id", "player_id", name="uq_league_draft_queue_user_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_draft_id: Mapped[int] = mapped_column(ForeignKey("league_drafts.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("site_users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    draft: Mapped["LeagueDraft"] = relationship(back_populates="queue_items")


class LeagueDraftSoundbite(db.Model):
    __tablename__ = "league_draft_soundbites"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_league_draft_sound_draft", "league_draft_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_draft_id: Mapped[int] = mapped_column(ForeignKey("league_drafts.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), default="audio/mpeg", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    draft: Mapped["LeagueDraft"] = relationship(back_populates="soundbites")


class LeagueExpansionDraft(db.Model):
    """Commissioner-run expansion draft (separate from LeagueDraft / prospect draft)."""

    __tablename__ = "league_expansion_drafts"
    __bind_key__ = "site"
    __table_args__ = (
        Index("ix_league_expansion_draft_slug_status", "league_slug", "status"),
        Index("ix_league_expansion_draft_slug_created", "league_slug", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="Expansion Draft", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="setup", nullable=False)  # setup|live|completed
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    goalie_rounds: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    skater_rounds: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_goalies_per_team: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_forwards_per_team: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_defense_per_team: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    max_players_lost_per_team: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expansion_team_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    goalie_phase_first_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skater_phase_first_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expansion_team_order_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    exempt_team_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    timer_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    empty_queue_timer_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    current_slot_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pick_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pick_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timer_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timer_paused_remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_extended_for_slot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expansion_pick_cooldown_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    awaiting_admin_resolution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    board_ranks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    slots: Mapped[list["LeagueExpansionDraftSlot"]] = relationship(
        back_populates="draft", order_by="LeagueExpansionDraftSlot.overall_pick"
    )
    picks: Mapped[list["LeagueExpansionDraftPick"]] = relationship(
        back_populates="draft", order_by="LeagueExpansionDraftPick.overall_pick"
    )
    eligible_players: Mapped[list["LeagueExpansionDraftEligiblePlayer"]] = relationship(
        back_populates="draft"
    )


class LeagueExpansionDraftSlot(db.Model):
    __tablename__ = "league_expansion_draft_slots"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_expansion_draft_id", "overall_pick", name="uq_league_exp_slot_overall"),
        Index("ix_league_exp_slot_draft", "league_expansion_draft_id", "overall_pick"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_expansion_draft_id: Mapped[int] = mapped_column(ForeignKey("league_expansion_drafts.id"), nullable=False)
    overall_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)  # goalie | skater
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    forfeited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    draft: Mapped["LeagueExpansionDraft"] = relationship(back_populates="slots")


class LeagueExpansionDraftPick(db.Model):
    __tablename__ = "league_expansion_draft_picks"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_expansion_draft_id", "overall_pick", name="uq_league_exp_pick_overall"),
        Index("ix_league_exp_pick_draft", "league_expansion_draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_expansion_draft_id: Mapped[int] = mapped_column(ForeignKey("league_expansion_drafts.id"), nullable=False)
    overall_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="gm", nullable=False)  # gm|admin
    picked_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    draft: Mapped["LeagueExpansionDraft"] = relationship(back_populates="picks")


class LeagueExpansionDraftEligiblePlayer(db.Model):
    __tablename__ = "league_expansion_draft_eligible_players"
    __bind_key__ = "site"
    __table_args__ = (
        UniqueConstraint("league_expansion_draft_id", "player_id", name="uq_league_exp_elig_draft_player"),
        Index("ix_league_exp_elig_draft", "league_expansion_draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_expansion_draft_id: Mapped[int] = mapped_column(ForeignKey("league_expansion_drafts.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)

    draft: Mapped["LeagueExpansionDraft"] = relationship(back_populates="eligible_players")


class ProspectSystemRankSnapshot(db.Model):
    """League-wide prospect system rank by team; compared on next view to show Δ rank."""

    __tablename__ = "prospect_system_rank_snapshots"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_prospect_sys_snap_league_at", "league_slug", "snapshot_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ranks_json: Mapped[str] = mapped_column(Text, nullable=False)


class PositionalRankSnapshot(db.Model):
    """Standings positional rankings table order by team; Δ vs last snapshot."""

    __tablename__ = "positional_rank_snapshots"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_positional_rank_snap_league_at", "league_slug", "snapshot_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ranks_json: Mapped[str] = mapped_column(Text, nullable=False)


class PowerRankSnapshot(db.Model):
    """Homepage power ranking order by team_id; Change column vs last snapshot."""

    __tablename__ = "power_rank_snapshots"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_power_rank_snap_league_at", "league_slug", "snapshot_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ranks_json: Mapped[str] = mapped_column(Text, nullable=False)


class ProspectLeagueRankSnapshot(db.Model):
    """League-wide prospect board (POT desc); player_id -> rank for /prospects table Δ column."""

    __tablename__ = "prospect_league_rank_snapshots"
    __bind_key__ = "site"
    __table_args__ = (Index("ix_prospect_league_snap_league_at", "league_slug", "snapshot_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ranks_json: Mapped[str] = mapped_column(Text, nullable=False)


def meta_dict(entry: ApLedgerEntry) -> dict:
    try:
        return json.loads(entry.meta_json or "{}")
    except json.JSONDecodeError:
        return {}

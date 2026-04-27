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


def meta_dict(entry: ApLedgerEntry) -> dict:
    try:
        return json.loads(entry.meta_json or "{}")
    except json.JSONDecodeError:
        return {}

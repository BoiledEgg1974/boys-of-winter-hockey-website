"""ORM models for Formula BOWL / Demolition BOWL racing league mounts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import db


class RacingRacer(db.Model):
    """One racer per racing site; links to Cap or Historical GM for AP awards."""

    __tablename__ = "racing_racers"
    __table_args__ = (
        UniqueConstraint("display_name", name="uq_racing_racer_display_name"),
        UniqueConstraint("user_id", name="uq_racing_racer_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Cap/Historical membership used when granting AP.
    ap_league_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ap_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    twitch_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    aliases: Mapped[list["RacingNameAlias"]] = relationship(back_populates="racer")
    results: Mapped[list["RacingEventResult"]] = relationship(back_populates="racer")


class RacingNameAlias(db.Model):
    """Map CSV driver / controller names onto a roster racer."""

    __tablename__ = "racing_name_aliases"
    __table_args__ = (UniqueConstraint("alias_key", name="uq_racing_alias_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racing_racers.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(160), nullable=False)

    racer: Mapped[RacingRacer] = relationship(back_populates="aliases")


class RacingCircuit(db.Model):
    """A circuit / championship run (Formula nights or Derby 10-event circuits)."""

    __tablename__ = "racing_circuits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, default="Circuit")
    external_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    events_planned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    events: Mapped[list["RacingEvent"]] = relationship(back_populates="circuit")
    standings: Mapped[list["RacingCircuitStanding"]] = relationship(back_populates="circuit")


class RacingEvent(db.Model):
    """One Formula feature race or one Derby night (heat ladder)."""

    __tablename__ = "racing_events"
    __table_args__ = (
        UniqueConstraint("circuit_id", "event_number", name="uq_racing_event_circuit_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    circuit_id: Mapped[int] = mapped_column(ForeignKey("racing_circuits.id"), nullable=False, index=True)
    event_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="race")  # race|heat_night
    track_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    export_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    circuit: Mapped[RacingCircuit] = relationship(back_populates="events")
    results: Mapped[list["RacingEventResult"]] = relationship(back_populates="event")
    ap_suggestions: Mapped[list["RacingApSuggestion"]] = relationship(back_populates="event")


class RacingEventResult(db.Model):
    """Finishing order / per-event stats from game CSV exports."""

    __tablename__ = "racing_event_results"
    __table_args__ = (
        UniqueConstraint("event_id", "position", name="uq_racing_result_event_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("racing_events.id"), nullable=False, index=True)
    racer_id: Mapped[int | None] = mapped_column(ForeignKey("racing_racers.id"), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    car_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    driver_name: Mapped[str] = mapped_column(String(160), nullable=False)
    controller: Mapped[str | None] = mapped_column(String(160), nullable=True)
    vehicle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    finished: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    eliminated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    circuit_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounds_survived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    damage_dealt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    gear: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wear: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[RacingEvent] = relationship(back_populates="results")
    racer: Mapped[RacingRacer | None] = relationship(back_populates="results")


class RacingCircuitStanding(db.Model):
    """Circuit leaders snapshot (points + channel points / kills)."""

    __tablename__ = "racing_circuit_standings"
    __table_args__ = (
        UniqueConstraint("circuit_id", "driver_key", name="uq_racing_standing_circuit_driver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    circuit_id: Mapped[int] = mapped_column(ForeignKey("racing_circuits.id"), nullable=False, index=True)
    racer_id: Mapped[int | None] = mapped_column(ForeignKey("racing_racers.id"), nullable=True, index=True)
    driver_key: Mapped[str] = mapped_column(String(160), nullable=False)
    driver_name: Mapped[str] = mapped_column(String(160), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_finish: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_finish: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)

    circuit: Mapped[RacingCircuit] = relationship(back_populates="standings")
    racer: Mapped[RacingRacer | None] = relationship()


class RacingApSuggestion(db.Model):
    """Suggested AP or Twitch channel-point payout from race/circuit results."""

    __tablename__ = "racing_ap_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # race | circuit
    currency: Mapped[str] = mapped_column(String(32), nullable=False, default="ap")  # ap | channel_points
    event_id: Mapped[int | None] = mapped_column(ForeignKey("racing_events.id"), nullable=True, index=True)
    circuit_id: Mapped[int | None] = mapped_column(ForeignKey("racing_circuits.id"), nullable=True, index=True)
    racer_id: Mapped[int | None] = mapped_column(ForeignKey("racing_racers.id"), nullable=True, index=True)
    driver_key: Mapped[str] = mapped_column(String(160), nullable=False)
    driver_name: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending|granted|skipped
    granted_league_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granted_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    event: Mapped[RacingEvent | None] = relationship(back_populates="ap_suggestions")
    racer: Mapped[RacingRacer | None] = relationship()


class RacingRewardTier(db.Model):
    """Admin-configured place→amount tables for race/circuit AP and Twitch CP."""

    __tablename__ = "racing_reward_tiers"
    __table_args__ = (
        UniqueConstraint("schedule_key", "place", name="uq_racing_reward_tier_key_place"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # race_ap | circuit_ap | race_channel_points | circuit_channel_points
    schedule_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    place: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = first
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RacingImportBatch(db.Model):
    """Track imported CSV files for idempotent re-runs."""

    __tablename__ = "racing_import_batches"
    __table_args__ = (UniqueConstraint("filename", name="uq_racing_import_filename"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    export_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    circuit_id: Mapped[int | None] = mapped_column(ForeignKey("racing_circuits.id"), nullable=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("racing_events.id"), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

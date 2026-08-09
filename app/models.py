"""SQLAlchemy models for the Boys of Winter Hockey League site."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.league_db import db


class Team(db.Model):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_team_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    nickname: Mapped[str | None] = mapped_column(String(120))
    abbreviation: Mapped[str] = mapped_column(String(8), nullable=False)
    logo_path: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str | None] = mapped_column(String(16))
    secondary_color: Mapped[str | None] = mapped_column(String(16))
    text_color: Mapped[str | None] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    fhm_league_id: Mapped[int | None] = mapped_column(Integer)
    fhm_conference_id: Mapped[int | None] = mapped_column(Integer)
    fhm_division_id: Mapped[int | None] = mapped_column(Integer)

    players: Mapped[list["Player"]] = relationship(back_populates="current_team")
    home_games: Mapped[list["Game"]] = relationship(
        foreign_keys="Game.home_team_id", back_populates="home_team"
    )
    away_games: Mapped[list["Game"]] = relationship(
        foreign_keys="Game.away_team_id", back_populates="away_team"
    )

    def full_display_name(self) -> str:
        """FHM-style label: ``name`` (city/region) plus ``nickname`` when set (e.g. Toronto Maple Leafs)."""
        base = (self.name or "").strip()
        nick = (self.nickname or "").strip()
        if not nick:
            return base or "—"
        if nick.lower() in base.lower():
            return base
        return f"{base} {nick}".strip()


class Season(db.Model):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_season_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    games: Mapped[list["Game"]] = relationship(back_populates="season")
    standings: Mapped[list["TeamStanding"]] = relationship(back_populates="season")


class Player(db.Model):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_player_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    full_name: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    nick_name: Mapped[str | None] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(8))
    shoots_catches: Mapped[str | None] = mapped_column(String(8))
    nationality: Mapped[str | None] = mapped_column(String(80))
    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_city: Mapped[str | None] = mapped_column(String(120))
    birth_state: Mapped[str | None] = mapped_column(String(80))
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    franchise_fhm_id: Mapped[int | None] = mapped_column(Integer)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str | None] = mapped_column(String(40))
    headshot_path: Mapped[str | None] = mapped_column(String(500))
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    overall_ability: Mapped[float | None] = mapped_column(Float)
    overall_potential: Mapped[float | None] = mapped_column(Float)
    boost_tier: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    current_team: Mapped["Team | None"] = relationship(back_populates="players")
    skater_season_stats: Mapped[list["PlayerSkaterStat"]] = relationship(back_populates="player")
    goalie_season_stats: Mapped[list["PlayerGoalieStat"]] = relationship(back_populates="player")
    career_skater_lines: Mapped[list["PlayerSkaterCareerLine"]] = relationship(back_populates="player")
    career_goalie_lines: Mapped[list["PlayerGoalieCareerLine"]] = relationship(back_populates="player")
    contract: Mapped["PlayerContract | None"] = relationship(back_populates="player", uselist=False)
    hall_of_fame_entry: Mapped["HallOfFameMember | None"] = relationship(
        back_populates="player", uselist=False
    )


class LeagueMeta(db.Model):
    """One row per league id from league_data.csv (FHM)."""

    __tablename__ = "league_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_league_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(16))


class TeamSeasonAggregate(db.Model):
    """Team-level season totals from team_stats.csv / team_stats_playoffs.csv (FHM)."""

    __tablename__ = "team_season_aggregates"
    __table_args__ = (
        UniqueConstraint("season_id", "team_id", "stat_segment", name="uq_team_season_agg_seg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    stat_segment: Mapped[str] = mapped_column(String(8), default="rs", nullable=False)
    shots_for: Mapped[int | None] = mapped_column(Integer)
    shots_against: Mapped[int | None] = mapped_column(Integer)
    faceoff_pct: Mapped[float | None] = mapped_column(Float)
    blocked_shots: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    takeaways: Mapped[int | None] = mapped_column(Integer)
    giveaways: Mapped[int | None] = mapped_column(Integer)
    pp_chances: Mapped[int | None] = mapped_column(Integer)
    pp_goals: Mapped[int | None] = mapped_column(Integer)
    pk_goals_against: Mapped[int | None] = mapped_column(Integer)
    sh_chances: Mapped[int | None] = mapped_column(Integer)
    sh_goals: Mapped[int | None] = mapped_column(Integer)
    pim_per_game: Mapped[float | None] = mapped_column(Float)
    attendance_home: Mapped[int | None] = mapped_column(Integer)
    attendance_away: Mapped[int | None] = mapped_column(Integer)
    sellouts_home: Mapped[int | None] = mapped_column(Integer)
    sellouts_away: Mapped[int | None] = mapped_column(Integer)
    capacity_use_pct: Mapped[float | None] = mapped_column(Float)

    season: Mapped["Season"] = relationship()
    team: Mapped["Team"] = relationship()


class PlayerSkaterCareerLine(db.Model):
    """Per-year career line from player_skater_career_stats_* and retired_* CSVs."""

    __tablename__ = "player_skater_career_lines"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "season_year",
            "team_fhm_id",
            "league_fhm_id",
            "career_source",
            name="uq_career_skater_line",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    team_fhm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    league_fhm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    career_source: Mapped[str] = mapped_column(String(24), default="rs", nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    gp: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    pim: Mapped[int] = mapped_column(Integer, default=0)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
    pp_goals: Mapped[int | None] = mapped_column(Integer)
    pp_assists: Mapped[int | None] = mapped_column(Integer)
    sh_goals: Mapped[int | None] = mapped_column(Integer)
    sh_assists: Mapped[int | None] = mapped_column(Integer)
    gwg: Mapped[int | None] = mapped_column(Integer)
    shots: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    gva: Mapped[int | None] = mapped_column(Integer)
    tka: Mapped[int | None] = mapped_column(Integer)
    sb: Mapped[int | None] = mapped_column(Integer)
    fights: Mapped[int | None] = mapped_column(Integer)
    fights_won: Mapped[int | None] = mapped_column(Integer)
    game_rating: Mapped[float | None] = mapped_column(Float)

    player: Mapped["Player"] = relationship(back_populates="career_skater_lines")
    team: Mapped["Team | None"] = relationship()


class PlayerGoalieCareerLine(db.Model):
    """Per-year goalie career from player_goalie_career_stats_* and retired_* CSVs."""

    __tablename__ = "player_goalie_career_lines"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "season_year",
            "team_fhm_id",
            "league_fhm_id",
            "career_source",
            name="uq_career_goalie_line",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    team_fhm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    league_fhm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    career_source: Mapped[str] = mapped_column(String(24), default="rs", nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    gp: Mapped[int] = mapped_column(Integer, default=0)
    games_started: Mapped[int | None] = mapped_column(Integer)
    minutes_played: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties_otl: Mapped[int | None] = mapped_column(Integer)
    empty_net_goals: Mapped[int | None] = mapped_column(Integer)
    shutouts: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    shots_against: Mapped[int] = mapped_column(Integer, default=0)
    game_rating: Mapped[float | None] = mapped_column(Float)

    player: Mapped["Player"] = relationship(back_populates="career_goalie_lines")
    team: Mapped["Team | None"] = relationship()


class PlayerContract(db.Model):
    __tablename__ = "player_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), unique=True, nullable=False)
    fhm_team_id: Mapped[int | None] = mapped_column(Integer)
    average_salary: Mapped[int | None] = mapped_column(Integer)
    has_ntc: Mapped[bool] = mapped_column(Boolean, default=False)
    has_nmc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_elc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ufa: Mapped[bool] = mapped_column(Boolean, default=False)

    player: Mapped["Player"] = relationship(back_populates="contract")


class Game(db.Model):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_game_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    game_date: Mapped[date | None] = mapped_column(Date, index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    went_to_overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    went_to_shootout: Mapped[bool] = mapped_column(Boolean, default=False)
    home_shots: Mapped[int | None] = mapped_column(Integer)
    away_shots: Mapped[int | None] = mapped_column(Integer)
    arena: Mapped[str | None] = mapped_column(String(200))
    attendance: Mapped[int | None] = mapped_column(Integer)
    game_type: Mapped[str | None] = mapped_column(String(40))
    fhm_league_id: Mapped[int | None] = mapped_column(Integer)
    fhm_star1_player_id: Mapped[int | None] = mapped_column(Integer)
    fhm_star2_player_id: Mapped[int | None] = mapped_column(Integer)
    fhm_star3_player_id: Mapped[int | None] = mapped_column(Integer)
    pp_goals_home: Mapped[int | None] = mapped_column(Integer)
    pp_opp_home: Mapped[int | None] = mapped_column(Integer)
    pp_goals_away: Mapped[int | None] = mapped_column(Integer)
    pp_opp_away: Mapped[int | None] = mapped_column(Integer)
    pim_home: Mapped[int | None] = mapped_column(Integer)
    pim_away: Mapped[int | None] = mapped_column(Integer)
    hits_home: Mapped[int | None] = mapped_column(Integer)
    hits_away: Mapped[int | None] = mapped_column(Integer)
    sq0_home: Mapped[int | None] = mapped_column(Integer)
    sq1_home: Mapped[int | None] = mapped_column(Integer)
    sq2_home: Mapped[int | None] = mapped_column(Integer)
    sq3_home: Mapped[int | None] = mapped_column(Integer)
    sq4_home: Mapped[int | None] = mapped_column(Integer)
    sq0_away: Mapped[int | None] = mapped_column(Integer)
    sq1_away: Mapped[int | None] = mapped_column(Integer)
    sq2_away: Mapped[int | None] = mapped_column(Integer)
    sq3_away: Mapped[int | None] = mapped_column(Integer)
    sq4_away: Mapped[int | None] = mapped_column(Integer)
    sog_home_p1: Mapped[int | None] = mapped_column(Integer)
    sog_home_p2: Mapped[int | None] = mapped_column(Integer)
    sog_home_p3: Mapped[int | None] = mapped_column(Integer)
    sog_home_ot: Mapped[int | None] = mapped_column(Integer)
    sog_away_p1: Mapped[int | None] = mapped_column(Integer)
    sog_away_p2: Mapped[int | None] = mapped_column(Integer)
    sog_away_p3: Mapped[int | None] = mapped_column(Integer)
    sog_away_ot: Mapped[int | None] = mapped_column(Integer)
    score_home_p1: Mapped[int | None] = mapped_column(Integer)
    score_home_p2: Mapped[int | None] = mapped_column(Integer)
    score_home_p3: Mapped[int | None] = mapped_column(Integer)
    score_home_ot: Mapped[int | None] = mapped_column(Integer)
    score_away_p1: Mapped[int | None] = mapped_column(Integer)
    score_away_p2: Mapped[int | None] = mapped_column(Integer)
    score_away_p3: Mapped[int | None] = mapped_column(Integer)
    score_away_ot: Mapped[int | None] = mapped_column(Integer)

    season: Mapped["Season"] = relationship(back_populates="games")
    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id], back_populates="home_games")
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id], back_populates="away_games")
    scoring_events: Mapped[list["ScoringEvent"]] = relationship(
        back_populates="game", order_by="ScoringEvent.period, ScoringEvent.time_elapsed"
    )
    penalties: Mapped[list["PenaltyEvent"]] = relationship(back_populates="game")
    skater_lines: Mapped[list["GameSkaterStat"]] = relationship(back_populates="game")
    goalie_lines: Mapped[list["GameGoalieStat"]] = relationship(back_populates="game")


class TeamStanding(db.Model):
    __tablename__ = "team_standings"
    __table_args__ = (UniqueConstraint("season_id", "team_id", name="uq_standing_season_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    conference: Mapped[str | None] = mapped_column(String(80))
    division: Mapped[str | None] = mapped_column(String(80))
    gp: Mapped[int] = mapped_column(Integer, default=0)
    w: Mapped[int] = mapped_column(Integer, default=0)
    l: Mapped[int] = mapped_column(Integer, default=0)
    otl: Mapped[int] = mapped_column(Integer, default=0)
    pts: Mapped[int] = mapped_column(Integer, default=0)
    gf: Mapped[int] = mapped_column(Integer, default=0)
    ga: Mapped[int] = mapped_column(Integer, default=0)
    streak: Mapped[str | None] = mapped_column(String(16))
    ties: Mapped[int] = mapped_column(Integer, default=0)
    shootout_wins: Mapped[int] = mapped_column(Integer, default=0)
    shootout_losses: Mapped[int] = mapped_column(Integer, default=0)
    win_pct: Mapped[float | None] = mapped_column(Float)

    season: Mapped["Season"] = relationship(back_populates="standings")
    team: Mapped["Team"] = relationship()

    def standing_gp_display(self) -> int:
        """Games played for standings tables: W + L + T + SOW + SOL; OTL is its own column."""
        return (
            int(self.w or 0)
            + int(self.l or 0)
            + int(self.ties or 0)
            + int(self.shootout_wins or 0)
            + int(self.shootout_losses or 0)
        )


class PlayerSkaterStat(db.Model):
    __tablename__ = "player_skater_stats"
    __table_args__ = (
        UniqueConstraint("season_id", "player_id", "stat_segment", name="uq_skater_season_player_segment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    stat_segment: Mapped[str] = mapped_column(String(8), default="rs")
    gp: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)
    pim: Mapped[int] = mapped_column(Integer, default=0)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
    shots: Mapped[int | None] = mapped_column(Integer)
    ppg: Mapped[int | None] = mapped_column(Integer)
    shg: Mapped[int | None] = mapped_column(Integer)
    gwg: Mapped[int | None] = mapped_column(Integer)
    pp_assists: Mapped[int | None] = mapped_column(Integer)
    sh_assists: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    blocked_shots: Mapped[int | None] = mapped_column(Integer)
    takeaways: Mapped[int | None] = mapped_column(Integer)
    giveaways: Mapped[int | None] = mapped_column(Integer)
    faceoffs: Mapped[int | None] = mapped_column(Integer)
    faceoff_wins: Mapped[int | None] = mapped_column(Integer)
    fights: Mapped[int | None] = mapped_column(Integer)
    fights_won: Mapped[int | None] = mapped_column(Integer)
    toi_seconds: Mapped[int | None] = mapped_column(Integer)
    ppto_seconds: Mapped[int | None] = mapped_column(Integer)
    shto_seconds: Mapped[int | None] = mapped_column(Integer)
    game_rating: Mapped[float | None] = mapped_column(Float)
    game_rating_off: Mapped[float | None] = mapped_column(Float)
    game_rating_def: Mapped[float | None] = mapped_column(Float)
    pdo: Mapped[float | None] = mapped_column(Float)
    cf: Mapped[int | None] = mapped_column(Integer)
    ca: Mapped[int | None] = mapped_column(Integer)
    cf_pct: Mapped[float | None] = mapped_column(Float)
    cf_pct_rel: Mapped[float | None] = mapped_column(Float)
    ff: Mapped[int | None] = mapped_column(Integer)
    fa: Mapped[int | None] = mapped_column(Integer)
    ff_pct: Mapped[float | None] = mapped_column(Float)
    ff_pct_rel: Mapped[float | None] = mapped_column(Float)
    gf_per_60: Mapped[float | None] = mapped_column(Float)
    ga_per_60: Mapped[float | None] = mapped_column(Float)
    sf_per_60: Mapped[float | None] = mapped_column(Float)
    sa_per_60: Mapped[float | None] = mapped_column(Float)

    season: Mapped["Season"] = relationship()
    player: Mapped["Player"] = relationship(back_populates="skater_season_stats")
    team: Mapped["Team | None"] = relationship()


class PlayerGoalieStat(db.Model):
    __tablename__ = "player_goalie_stats"
    __table_args__ = (
        UniqueConstraint("season_id", "player_id", "stat_segment", name="uq_goalie_season_player_segment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    stat_segment: Mapped[str] = mapped_column(String(8), default="rs")
    gp: Mapped[int] = mapped_column(Integer, default=0)
    games_started: Mapped[int | None] = mapped_column(Integer)
    minutes_played: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    otl: Mapped[int] = mapped_column(Integer, default=0)
    ga: Mapped[int] = mapped_column(Integer, default=0)
    sa: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    gaa: Mapped[float | None] = mapped_column(Float)
    sv_pct: Mapped[float | None] = mapped_column(Float)
    game_rating: Mapped[float | None] = mapped_column(Float)
    gsaa: Mapped[float | None] = mapped_column(Float)

    season: Mapped["Season"] = relationship()
    player: Mapped["Player"] = relationship(back_populates="goalie_season_stats")
    team: Mapped["Team | None"] = relationship()


class GameSkaterStat(db.Model):
    __tablename__ = "game_skater_stats"
    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_game_skater"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    shots: Mapped[int] = mapped_column(Integer, default=0)
    pim: Mapped[int] = mapped_column(Integer, default=0)
    toi_seconds: Mapped[int | None] = mapped_column(Integer)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
    game_rating: Mapped[float | None] = mapped_column(Float)
    hits: Mapped[int | None] = mapped_column(Integer)
    blocked_shots: Mapped[int | None] = mapped_column(Integer)
    missed_shots: Mapped[int | None] = mapped_column(Integer)
    takeaways: Mapped[int | None] = mapped_column(Integer)
    giveaways: Mapped[int | None] = mapped_column(Integer)
    faceoffs_won: Mapped[int | None] = mapped_column(Integer)
    faceoffs_lost: Mapped[int | None] = mapped_column(Integer)
    oz_starts: Mapped[int | None] = mapped_column(Integer)
    nz_starts: Mapped[int | None] = mapped_column(Integer)
    dz_starts: Mapped[int | None] = mapped_column(Integer)
    sq0: Mapped[int | None] = mapped_column(Integer)
    sq1: Mapped[int | None] = mapped_column(Integer)
    sq2: Mapped[int | None] = mapped_column(Integer)
    sq3: Mapped[int | None] = mapped_column(Integer)
    sq4: Mapped[int | None] = mapped_column(Integer)
    team_shots_off: Mapped[int | None] = mapped_column(Integer)
    team_shots_against_off: Mapped[int | None] = mapped_column(Integer)
    team_goals_off: Mapped[int | None] = mapped_column(Integer)
    team_goal_against_off: Mapped[int | None] = mapped_column(Integer)

    game: Mapped["Game"] = relationship(back_populates="skater_lines")
    player: Mapped["Player"] = relationship()
    team: Mapped["Team"] = relationship()


class GameGoalieStat(db.Model):
    __tablename__ = "game_goalie_stats"
    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_game_goalie"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    shots_against: Mapped[int] = mapped_column(Integer, default=0)
    goals_allowed: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str | None] = mapped_column(String(8))
    toi_seconds: Mapped[int | None] = mapped_column(Integer)
    game_rating: Mapped[float | None] = mapped_column(Float)

    game: Mapped["Game"] = relationship(back_populates="goalie_lines")
    player: Mapped["Player"] = relationship()
    team: Mapped["Team"] = relationship()


class ScoringEvent(db.Model):
    __tablename__ = "scoring_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    time_elapsed: Mapped[str | None] = mapped_column(String(16))
    scorer_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    assist1_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    assist2_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    scoring_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    strength: Mapped[str | None] = mapped_column(String(24))

    game: Mapped["Game"] = relationship(back_populates="scoring_events")


class PenaltyEvent(db.Model):
    __tablename__ = "penalty_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    time_elapsed: Mapped[str | None] = mapped_column(String(16))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    minutes: Mapped[int | None] = mapped_column(Integer)
    infraction: Mapped[str | None] = mapped_column(String(120))

    game: Mapped["Game"] = relationship(back_populates="penalties")


class Prospect(db.Model):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_prospect_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    rank: Mapped[int | None] = mapped_column(Integer)
    tier: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)

    player: Mapped["Player | None"] = relationship()
    team: Mapped["Team | None"] = relationship()


class Draft(db.Model):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fhm_draft_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.id"))
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)

    picks: Mapped[list["DraftPick"]] = relationship(back_populates="draft", order_by="DraftPick.overall_pick")


class DraftPick(db.Model):
    __tablename__ = "draft_picks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), nullable=False)
    overall_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int | None] = mapped_column(Integer)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    original_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    draft_year: Mapped[int | None] = mapped_column(Integer)
    fhm_picked_from_team_id: Mapped[int | None] = mapped_column(Integer)

    draft: Mapped["Draft"] = relationship(back_populates="picks")
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])
    player: Mapped["Player | None"] = relationship()


class HistoryAward(db.Model):
    __tablename__ = "history_awards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    award_name: Mapped[str] = mapped_column(String(160), nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    #: FHM ``StaffId`` from ``staff_master.csv`` (Jack Adams / Jim Gregory history rows).
    staff_fhm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    #: ``csv`` (import) or ``admin`` (protected from replace-all imports).
    source: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    season: Mapped["Season"] = relationship()
    player: Mapped["Player | None"] = relationship()
    team: Mapped["Team | None"] = relationship()


class HistoryChampion(db.Model):
    __tablename__ = "history_champions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    trophy: Mapped[str | None] = mapped_column(String(120))

    season: Mapped["Season"] = relationship()
    team: Mapped["Team"] = relationship()


class HistoryAllStar(db.Model):
    """First / second all-star team lines from ``history_all_stars.csv`` (additive upsert import)."""

    __tablename__ = "history_all_stars"
    __table_args__ = (
        UniqueConstraint(
            "season_label",
            "team_rank",
            "slot",
            name="uq_history_all_star_label_team_slot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    #: CSV / display season key (e.g. ``1989-90``). Distinct from :attr:`season_id` when the DB has
    #: a single placeholder season row and ``notes`` carries ``sheet_season=``.
    season_label: Mapped[str] = mapped_column(String(16), nullable=False, default="", index=True)
    #: ``1`` = First Team, ``2`` = Second Team.
    team_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Row order within the team (1 = goalie … 6 = right wing).
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[str] = mapped_column(String(32), nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    season: Mapped["Season"] = relationship()
    player: Mapped["Player | None"] = relationship()
    team: Mapped["Team | None"] = relationship()


class HallOfFameMember(db.Model):
    """Inductee list sourced from CSV imports or protected admin entries."""

    __tablename__ = "hall_of_fame_members"
    __table_args__ = (UniqueConstraint("player_id", name="uq_hof_player"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    #: ``skater`` or ``goalie`` — controls which career stat block the HoF page shows.
    member_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    inducted_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: ``csv`` (upserted from import; never deleted by import) or ``admin`` (never overwritten by CSV).
    source: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    player: Mapped["Player"] = relationship(back_populates="hall_of_fame_entry")


class TeamSeasonRecord(db.Model):
    """One row per team per historical season, sourced from ``team_season_records_template.csv``.

    Columns mirror the CSV. Blank cells become ``NULL`` and are filtered out of leaderboards.
    The literal token ``"null"`` (case-insensitive) also becomes ``NULL`` but its origin is
    tracked in :attr:`null_columns_csv` so detail tables can render ``-`` for those cells
    while leaderboards still skip them.
    """

    __tablename__ = "team_season_records"
    __table_args__ = (
        UniqueConstraint(
            "season_year_label",
            "team_id",
            "team_name_override",
            name="uq_team_season_record_year_team",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_year_label: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    start_year: Mapped[int | None] = mapped_column(Integer, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team_fhm_id_csv: Mapped[str | None] = mapped_column(String(64))
    team_name_override: Mapped[str | None] = mapped_column(String(200))
    conference_id: Mapped[int | None] = mapped_column(Integer)
    conference_override: Mapped[str | None] = mapped_column(String(120))
    division_id: Mapped[int | None] = mapped_column(Integer)
    division_override: Mapped[str | None] = mapped_column(String(120))
    logo_file_override: Mapped[str | None] = mapped_column(String(500))
    gp: Mapped[int | None] = mapped_column(Integer)
    w: Mapped[int | None] = mapped_column(Integer)
    l: Mapped[int | None] = mapped_column(Integer)
    t_otl: Mapped[int | None] = mapped_column(Integer)
    pts: Mapped[int | None] = mapped_column(Integer)
    gf: Mapped[int | None] = mapped_column(Integer)
    ga: Mapped[int | None] = mapped_column(Integer)
    goal_diff: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(String(80))
    pim_per_game: Mapped[float | None] = mapped_column(Float)
    ppg: Mapped[int | None] = mapped_column(Integer)
    ppg_against: Mapped[int | None] = mapped_column(Integer)
    pp_chances: Mapped[int | None] = mapped_column(Integer)
    shg: Mapped[int | None] = mapped_column(Integer)
    shg_against: Mapped[int | None] = mapped_column(Integer)
    sh_chances: Mapped[int | None] = mapped_column(Integer)
    pp_pct: Mapped[float | None] = mapped_column(Float)
    pk_pct: Mapped[float | None] = mapped_column(Float)
    shots_for: Mapped[int | None] = mapped_column(Integer)
    shots_against: Mapped[int | None] = mapped_column(Integer)
    null_columns_csv: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    team: Mapped["Team | None"] = relationship()


class FranchiseTeamIdentity(db.Model):
    """Era-specific display identity for relocated/renamed franchises.

    Rows are keyed to the current franchise ``teams.id`` when possible, with
    ``team_fhm_id`` as a fallback for historical career/draft rows that only
    carry FHM source IDs.
    """

    __tablename__ = "franchise_team_identities"
    __table_args__ = (
        Index("ix_franchise_identity_team_year", "team_id", "start_year", "end_year"),
        Index("ix_franchise_identity_fhm_year", "team_fhm_id", "start_year", "end_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team_fhm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    logo_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="historical", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    team: Mapped["Team | None"] = relationship()


class TeamHonorsMeta(db.Model):
    """Per-team display toggles for the team page honors section."""

    __tablename__ = "team_honors_meta"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    retired_section_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    team: Mapped["Team"] = relationship()


class TeamRetiredNumber(db.Model):
    """Franchise retired jersey numbers shown on team pages."""

    __tablename__ = "team_retired_numbers"
    __table_args__ = (
        UniqueConstraint("team_id", "jersey_number", name="uq_team_retired_jersey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    jersey_number: Mapped[int] = mapped_column(Integer, nullable=False)
    jersey_image_rel_path: Mapped[str | None] = mapped_column(String(500))
    number_color: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    team: Mapped["Team"] = relationship()


class TeamVictoryBanner(db.Model):
    """Team championship / victory banner images (TXX-BannerY assets)."""

    __tablename__ = "team_victory_banners"
    __table_args__ = (
        UniqueConstraint("team_id", "victory_number", name="uq_team_victory_banner"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    victory_number: Mapped[int] = mapped_column(Integer, nullable=False)
    banner_image_rel_path: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    team: Mapped["Team"] = relationship()


class PlayerOverallBaseline(db.Model):
    """1–100 overall composite baseline for depth-chart ↑/↓ (snapshotted at import start; optional CLI reset)."""

    __tablename__ = "player_overall_baselines"

    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), primary_key=True)
    baseline_score: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PlayerRatingSnapshot(db.Model):
    """Point-in-time copy of ``player_ratings.csv`` values for development trend charts."""

    __tablename__ = "player_rating_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    ratings_json: Mapped[str] = mapped_column(Text, nullable=False)
    ability: Mapped[float | None] = mapped_column(Float)
    potential: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    timeline_season_start_year: Mapped[int | None] = mapped_column(Integer)
    timeline_calendar_year: Mapped[int | None] = mapped_column(Integer)
    timeline_calendar_month: Mapped[int | None] = mapped_column(Integer)

    player: Mapped["Player"] = relationship()


class PlayerAnalyticsSnapshot(db.Model):
    """Point-in-time player analytics (WAR %, process metrics) across FHM imports."""

    __tablename__ = "player_analytics_snapshots"
    __table_args__ = (
        Index("ix_player_analytics_snap_player_at", "player_id", "snapshot_at"),
        Index("ix_player_analytics_snap_year_seg", "season_year", "stat_segment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stat_segment: Mapped[str] = mapped_column(String(8), nullable=False, default="rs")
    is_goalie: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_rollover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    war_pct: Mapped[int | None] = mapped_column(Integer)
    gp: Mapped[int | None] = mapped_column(Integer)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    percentiles_json: Mapped[str | None] = mapped_column(Text)

    player: Mapped["Player"] = relationship()


class TeamAnalyticsSnapshot(db.Model):
    """Point-in-time team process metrics across FHM imports / season years."""

    __tablename__ = "team_analytics_snapshots"
    __table_args__ = (
        Index("ix_team_analytics_snap_team_at", "team_id", "snapshot_at"),
        Index("ix_team_analytics_snap_year_seg", "season_year", "stat_segment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stat_segment: Mapped[str] = mapped_column(String(8), nullable=False, default="rs")
    is_rollover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)

    team: Mapped["Team"] = relationship()


class AdvancedStatsHubSnapshot(db.Model):
    """Archived Advanced Stats hub payload (leaderboards + lines + shot quality) for a season year."""

    __tablename__ = "advanced_stats_hub_snapshots"
    __table_args__ = (
        Index("ix_adv_stats_hub_snap_year_seg", "season_year", "stat_segment"),
        Index("ix_adv_stats_hub_snap_at", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stat_segment: Mapped[str] = mapped_column(String(8), nullable=False, default="rs")
    is_rollover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    hub_json: Mapped[str] = mapped_column(Text, nullable=False)


class OrgDevelopmentReportArchive(db.Model):
    """Persisted monthly org development report (survives snapshot trimming)."""

    __tablename__ = "org_development_report_archives"
    __table_args__ = (
        UniqueConstraint("team_id", "timeline_key", name="uq_org_dev_report_team_timeline"),
        Index("ix_org_dev_report_team_sort", "team_id", "timeline_season_start_year", "timeline_calendar_year", "timeline_calendar_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    league_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeline_key: Mapped[str] = mapped_column(String(16), nullable=False)
    timeline_season_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    timeline_calendar_year: Mapped[int] = mapped_column(Integer, nullable=False)
    timeline_calendar_month: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TradeLogEntry(db.Model):
    """League trade history from optional ``trades.csv`` in the raw import folder (replace-all import)."""

    __tablename__ = "trade_log_entries"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_trade_log_external_id"),
        Index("ix_trade_log_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    team_b_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)

    team_a: Mapped["Team"] = relationship(foreign_keys=[team_a_id])
    team_b: Mapped["Team"] = relationship(foreign_keys=[team_b_id])


class ImportLog(db.Model):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="started")
    message: Mapped[str | None] = mapped_column(Text)


class RecordLeaderSnapshot(db.Model):
    """Persisted #1 holders for season / all-time / team boards (Discord break detection)."""

    __tablename__ = "record_leader_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_key", name="uq_record_leader_snapshot_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_key: Mapped[str] = mapped_column(String(255), nullable=False)
    holder_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class GameRecordBaseline(db.Model):
    """Admin-seeded single-game record baselines; boxscore imports may surpass these values."""

    __tablename__ = "game_record_baselines"
    __table_args__ = (
        UniqueConstraint(
            "metric_key",
            "segment",
            "scope",
            "player_kind",
            name="uq_game_record_baseline_metric",
        ),
        Index("ix_game_record_baseline_segment", "segment", "scope", "player_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str] = mapped_column(String(8), nullable=False, default="rs")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    player_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="skater")
    value: Mapped[float] = mapped_column(Float, nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    opponent_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"))
    game_date: Mapped[date | None] = mapped_column(Date)
    season_label: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])
    opponent_team: Mapped["Team | None"] = relationship(foreign_keys=[opponent_team_id])
    game: Mapped["Game | None"] = relationship()


class RecordStatAdjustment(db.Model):
    """Exclude or override a career/team row used when building records leaderboards."""

    __tablename__ = "record_stat_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adj_type: Mapped[str] = mapped_column(String(16), nullable=False)
    line_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="skater_career")
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    season_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    team_fhm_id: Mapped[str | None] = mapped_column(String(64))
    career_source: Mapped[str | None] = mapped_column(String(24))
    overrides_json: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    player: Mapped["Player | None"] = relationship()


Index("ix_games_season_status", Game.season_id, Game.status)
Index("ix_player_skater_points", PlayerSkaterStat.season_id, PlayerSkaterStat.stat_segment, PlayerSkaterStat.points)
Index("ix_player_goalie_wins", PlayerGoalieStat.season_id, PlayerGoalieStat.stat_segment, PlayerGoalieStat.wins)

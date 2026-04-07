"""SQLAlchemy models for the Boys of Winter Hockey League site."""
from __future__ import annotations

from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
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

db = SQLAlchemy()


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

    current_team: Mapped["Team | None"] = relationship(back_populates="players")
    skater_season_stats: Mapped[list["PlayerSkaterStat"]] = relationship(back_populates="player")
    goalie_season_stats: Mapped[list["PlayerGoalieStat"]] = relationship(back_populates="player")
    career_skater_lines: Mapped[list["PlayerSkaterCareerLine"]] = relationship(back_populates="player")
    career_goalie_lines: Mapped[list["PlayerGoalieCareerLine"]] = relationship(back_populates="player")
    contract: Mapped["PlayerContract | None"] = relationship(back_populates="player", uselist=False)


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
    notes: Mapped[str | None] = mapped_column(Text)

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


class ImportLog(db.Model):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="started")
    message: Mapped[str | None] = mapped_column(Text)


Index("ix_games_season_status", Game.season_id, Game.status)
Index("ix_player_skater_points", PlayerSkaterStat.season_id, PlayerSkaterStat.stat_segment, PlayerSkaterStat.points)
Index("ix_player_goalie_wins", PlayerGoalieStat.season_id, PlayerGoalieStat.stat_segment, PlayerGoalieStat.wins)

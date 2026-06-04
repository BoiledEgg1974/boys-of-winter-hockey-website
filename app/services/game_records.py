"""Single-game league records from boxscores + admin baselines."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.models import (
    FranchiseTeamIdentity,
    Game,
    GameGoalieStat,
    GameRecordBaseline,
    GameSkaterStat,
    Player,
    ScoringEvent,
    Season,
    Team,
    db,
)
from app.services.rookie_eligibility import player_was_rookie_in_season, rookie_player_ids_for_season
from app.services.seasons import season_display_label


@dataclass(frozen=True)
class GameRecordMetric:
    key: str
    title: str
    player_kind: str
    higher_is_better: bool = True
    defense_only: bool = False
    value_kind: str = "int"
    source: str = "skater"


@dataclass(frozen=True)
class GameRecordTeamChoice:
    value: int
    label: str
    sort_label: str
    identity_id: int | None = None


def game_record_metrics(*, player_kind: str) -> list[GameRecordMetric]:
    if player_kind == "goalie":
        return [
            GameRecordMetric("saves", "Saves", "goalie"),
            GameRecordMetric("shots_against", "Shots Against", "goalie"),
            GameRecordMetric("goals_allowed", "Goals Allowed", "goalie"),
        ]
    return [
        GameRecordMetric("goals", "Goals", "skater"),
        GameRecordMetric("assists", "Assists", "skater"),
        GameRecordMetric("points", "Points", "skater"),
        GameRecordMetric("pp_goals", "PP Goals", "skater", source="scoring_pp"),
        GameRecordMetric("sh_goals", "SH Goals", "skater", source="scoring_sh"),
        GameRecordMetric("goals_def", "Goals Def", "skater", defense_only=True),
        GameRecordMetric("assists_def", "Assists Def", "skater", defense_only=True),
        GameRecordMetric("points_def", "Points Def", "skater", defense_only=True),
        GameRecordMetric("pim", "PIM", "skater"),
        GameRecordMetric("shots", "Shots", "skater"),
        GameRecordMetric("missed_shots", "Missed Shots", "skater"),
        GameRecordMetric("blocked_shots", "Blocked Shots", "skater"),
        GameRecordMetric("hits", "Hits", "skater"),
        GameRecordMetric("takeaways", "Takeaways", "skater"),
        GameRecordMetric("giveaways", "Giveaways", "skater"),
        GameRecordMetric("toi_seconds", "Min Played", "skater", value_kind="time"),
        GameRecordMetric("faceoffs_won", "FO Won", "skater"),
        GameRecordMetric("faceoffs_lost", "FO Lost", "skater"),
        GameRecordMetric("plus_minus_high", "Highest Plus Minus", "skater", value_kind="plus_minus"),
        GameRecordMetric(
            "plus_minus_low",
            "Lowest Plus Minus",
            "skater",
            higher_is_better=False,
            value_kind="plus_minus",
        ),
    ]


def _strength_is_pp(strength: str | None) -> bool:
    s = (strength or "").strip().lower()
    return "pp" in s or "power" in s


def _strength_is_sh(strength: str | None) -> bool:
    s = (strength or "").strip().lower()
    if _strength_is_pp(strength):
        return False
    return "sh" in s or "short" in s or s in {"pk", "penalty kill"}


def _fmt_toi(seconds: int | None) -> str:
    if seconds is None or seconds <= 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _season_start_year_from_label(label: str | None) -> int | None:
    m = re.search(r"(\d{4})", str(label or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def game_record_season_year(game_date: date | None, season_label: str | None) -> int | None:
    """Best-effort season start year for era-correct baseline identities."""
    from_label = _season_start_year_from_label(season_label)
    if from_label is not None:
        return from_label
    if game_date is None:
        return None
    return int(game_date.year) if int(game_date.month) >= 7 else int(game_date.year) - 1


def format_game_record_value(value: float | None, metric: GameRecordMetric) -> str:
    if value is None:
        return "—"
    if metric.value_kind == "time":
        return _fmt_toi(int(round(value)))
    if metric.value_kind == "pct":
        v = float(value)
        if v > 1.5:
            v = v / 100.0
        s = f"{v:.3f}"
        return s[1:] if s.startswith("0") else s
    if metric.value_kind == "plus_minus":
        iv = int(round(value))
        return f"+{iv}" if iv > 0 else str(iv)
    if metric.value_kind == "rating":
        return f"{float(value):.1f}"
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.2f}"


def _compare_values(a: float, b: float, *, higher_is_better: bool) -> float:
    """Return the better of two numeric values."""
    if higher_is_better:
        return a if a >= b else b
    return a if a <= b else b


def _is_better(a: float, b: float, *, higher_is_better: bool) -> bool:
    if higher_is_better:
        return a > b
    return a < b


@dataclass
class GameRecordHolder:
    metric: GameRecordMetric
    value: float | None
    display_value: str
    player: Player | None
    team: Team | None
    opponent_team: Team | None
    game_date: date | None
    season_label: str | None
    game_id: int | None
    source: str

    def to_template_dict(self) -> dict[str, Any]:
        season_year = game_record_season_year(self.game_date, self.season_label)
        return {
            "metric": self.metric,
            "title": self.metric.title,
            "value": self.value,
            "display_value": self.display_value,
            "player": self.player,
            "team": self.team,
            "opponent_team": self.opponent_team,
            "season_year": season_year,
            "season_year_label": self.season_label,
            "game_date": self.game_date,
            "season_label": self.season_label,
            "game_id": self.game_id,
            "source": self.source,
        }


def _opponent_for_game(game: Game, team_id: int | None) -> Team | None:
    if team_id is None:
        return None
    if int(game.home_team_id) == int(team_id):
        return db.session.get(Team, game.away_team_id)
    if int(game.away_team_id) == int(team_id):
        return db.session.get(Team, game.home_team_id)
    return None


def _season_label_for_game(session: Session, game: Game) -> str | None:
    sn = session.get(Season, game.season_id) if game.season_id else None
    return season_display_label(sn) if sn else None


def _baseline_row(
    session: Session,
    metric: GameRecordMetric,
    segment: str,
    scope: str,
) -> GameRecordHolder | None:
    row = session.scalars(
        select(GameRecordBaseline).where(
            GameRecordBaseline.metric_key == metric.key,
            GameRecordBaseline.segment == segment,
            GameRecordBaseline.scope == scope,
            GameRecordBaseline.player_kind == metric.player_kind,
        ).limit(1)
    ).first()
    if row is None:
        return None
    pl = session.get(Player, row.player_id) if row.player_id else None
    tm = session.get(Team, row.team_id) if row.team_id else None
    opp = session.get(Team, row.opponent_team_id) if row.opponent_team_id else None
    val = float(row.value) if row.value is not None else None
    return GameRecordHolder(
        metric=metric,
        value=val,
        display_value=format_game_record_value(val, metric),
        player=pl,
        team=tm,
        opponent_team=opp,
        game_date=row.game_date,
        season_label=row.season_label,
        game_id=row.game_id,
        source="baseline",
    )


def _skater_metric_column(metric: GameRecordMetric):
    if metric.key == "points":
        return (GameSkaterStat.goals + GameSkaterStat.assists).label("metric_val")
    if metric.key == "points_def":
        return (GameSkaterStat.goals + GameSkaterStat.assists).label("metric_val")
    if metric.key == "goals_def":
        return GameSkaterStat.goals.label("metric_val")
    if metric.key == "assists_def":
        return GameSkaterStat.assists.label("metric_val")
    if metric.key == "plus_minus_high" or metric.key == "plus_minus_low":
        return GameSkaterStat.plus_minus.label("metric_val")
    col = getattr(GameSkaterStat, metric.key, None)
    if col is None:
        return None
    return col.label("metric_val")


def _goalie_metric_column(metric: GameRecordMetric):
    if metric.key == "save_pct":
        return case(
            (GameGoalieStat.shots_against > 0, GameGoalieStat.saves * 1.0 / GameGoalieStat.shots_against),
            else_=None,
        ).label("metric_val")
    if metric.key == "minutes_played":
        return GameGoalieStat.toi_seconds.label("metric_val")
    col = getattr(GameGoalieStat, metric.key, None)
    if col is None:
        return None
    return col.label("metric_val")


def _game_segment_filter(segment: str):
    if segment == "po":
        return or_(
            Game.game_type.ilike("%playoff%"),
            Game.game_type.ilike("%post%"),
            Game.game_type.ilike("%stanley%"),
        )
    return or_(
        Game.game_type.is_(None),
        Game.game_type.ilike("%regular%"),
        and_(
            ~Game.game_type.ilike("%playoff%"),
            ~Game.game_type.ilike("%post%"),
            ~Game.game_type.ilike("%preseason%"),
            ~Game.game_type.ilike("%pre-season%"),
            ~Game.game_type.ilike("%exhibition%"),
        ),
    )


def _best_from_skater_query(
    session: Session,
    metric: GameRecordMetric,
    segment: str,
    scope: str,
) -> GameRecordHolder | None:
    metric_col = _skater_metric_column(metric)
    if metric_col is None:
        return None
    rookie_cache: dict[int, set[int]] = {}
    q = (
        select(GameSkaterStat, Game, Player, Team, metric_col)
        .join(Game, GameSkaterStat.game_id == Game.id)
        .join(Player, GameSkaterStat.player_id == Player.id)
        .join(Team, GameSkaterStat.team_id == Team.id)
        .where(
            Game.status == "final",
            _game_segment_filter(segment),
            metric_col.isnot(None),
        )
    )
    if metric.defense_only or metric.key.endswith("_def"):
        q = q.where(
            or_(
                Player.position.ilike("D%"),
                Player.position.ilike("% D%"),
                Player.position.ilike("LD%"),
                Player.position.ilike("RD%"),
            )
        )
    order = metric_col.desc() if metric.higher_is_better else metric_col.asc()
    rows = session.execute(q.order_by(order, Game.game_date.desc()).limit(400)).all()
    for gs, game, pl, tm, raw_val in rows:
        if raw_val is None:
            continue
        val = float(raw_val)
        if scope == "rookie":
            sid = int(game.season_id)
            if sid not in rookie_cache:
                sn = session.get(Season, sid)
                rookie_cache[sid] = (
                    rookie_player_ids_for_season(session, sn, player_kind="skater") if sn else set()
                )
            if int(pl.id) not in rookie_cache[sid]:
                continue
        opp = _opponent_for_game(game, tm.id)
        return GameRecordHolder(
            metric=metric,
            value=val,
            display_value=format_game_record_value(val, metric),
            player=pl,
            team=tm,
            opponent_team=opp,
            game_date=game.game_date,
            season_label=_season_label_for_game(session, game),
            game_id=int(game.id),
            source="boxscore",
        )
    return None


def _best_from_goalie_query(
    session: Session,
    metric: GameRecordMetric,
    segment: str,
    scope: str,
) -> GameRecordHolder | None:
    metric_col = _goalie_metric_column(metric)
    if metric_col is None:
        return None
    rookie_cache: dict[int, set[int]] = {}
    min_sa = 1 if metric.key == "save_pct" else 0
    q = (
        select(GameGoalieStat, Game, Player, Team, metric_col)
        .join(Game, GameGoalieStat.game_id == Game.id)
        .join(Player, GameGoalieStat.player_id == Player.id)
        .join(Team, GameGoalieStat.team_id == Team.id)
        .where(
            Game.status == "final",
            _game_segment_filter(segment),
            metric_col.isnot(None),
        )
    )
    if min_sa:
        q = q.where(GameGoalieStat.shots_against >= min_sa)
    order = metric_col.desc() if metric.higher_is_better else metric_col.asc()
    rows = session.execute(q.order_by(order, Game.game_date.desc()).limit(400)).all()
    for gg, game, pl, tm, raw_val in rows:
        if raw_val is None:
            continue
        val = float(raw_val)
        if scope == "rookie":
            sid = int(game.season_id)
            if sid not in rookie_cache:
                sn = session.get(Season, sid)
                rookie_cache[sid] = (
                    rookie_player_ids_for_season(session, sn, player_kind="goalie") if sn else set()
                )
            if int(pl.id) not in rookie_cache[sid]:
                continue
        opp = _opponent_for_game(game, tm.id)
        return GameRecordHolder(
            metric=metric,
            value=val,
            display_value=format_game_record_value(val, metric),
            player=pl,
            team=tm,
            opponent_team=opp,
            game_date=game.game_date,
            season_label=_season_label_for_game(session, game),
            game_id=int(game.id),
            source="boxscore",
        )
    return None


def _best_from_scoring(
    session: Session,
    metric: GameRecordMetric,
    segment: str,
    scope: str,
    *,
    pp: bool,
) -> GameRecordHolder | None:
    strength_fn = _strength_is_pp if pp else _strength_is_sh
    rookie_cache: dict[int, set[int]] = {}
    games = session.scalars(
        select(Game).where(Game.status == "final", _game_segment_filter(segment))
    ).all()
    game_by_id = {int(g.id): g for g in games}
    if not game_by_id:
        return None
    events = session.scalars(
        select(ScoringEvent).where(
            ScoringEvent.game_id.in_(game_by_id.keys()),
            ScoringEvent.scorer_player_id.isnot(None),
        )
    ).all()
    counts: dict[tuple[int, int], int] = {}
    for ev in events:
        if not strength_fn(ev.strength):
            continue
        key = (int(ev.game_id), int(ev.scorer_player_id))
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    best_key = max(counts.keys(), key=lambda k: counts[k])
    best_val = float(counts[best_key])
    game = game_by_id[best_key[0]]
    pl = session.get(Player, best_key[1])
    if pl is None:
        return None
    if scope == "rookie":
        sn = session.get(Season, game.season_id)
        if sn and not player_was_rookie_in_season(session, int(pl.id), sn, player_kind="skater"):
            sorted_keys = sorted(counts.keys(), key=lambda k: counts[k], reverse=True)
            for gk in sorted_keys:
                g2 = game_by_id[gk[0]]
                sn2 = session.get(Season, g2.season_id)
                pl2 = session.get(Player, gk[1])
                if pl2 and sn2 and player_was_rookie_in_season(session, int(pl2.id), sn2, player_kind="skater"):
                    best_key = gk
                    best_val = float(counts[gk])
                    game = g2
                    pl = pl2
                    break
            else:
                return None
    gs = session.scalars(
        select(GameSkaterStat).where(
            GameSkaterStat.game_id == game.id,
            GameSkaterStat.player_id == pl.id,
        ).limit(1)
    ).first()
    tm = session.get(Team, gs.team_id) if gs and gs.team_id else None
    opp = _opponent_for_game(game, tm.id if tm else None)
    return GameRecordHolder(
        metric=metric,
        value=best_val,
        display_value=format_game_record_value(best_val, metric),
        player=pl,
        team=tm,
        opponent_team=opp,
        game_date=game.game_date,
        season_label=_season_label_for_game(session, game),
        game_id=int(game.id),
        source="boxscore",
    )


def _merge_holders(
    baseline: GameRecordHolder | None,
    computed: GameRecordHolder | None,
    metric: GameRecordMetric,
) -> GameRecordHolder | None:
    if baseline is None and computed is None:
        return None
    if baseline is None:
        return computed
    if computed is None or computed.value is None:
        return baseline
    if baseline.value is None:
        return computed
    if _is_better(computed.value, baseline.value, higher_is_better=metric.higher_is_better):
        return computed
    return baseline


def resolve_game_record(
    session: Session,
    metric: GameRecordMetric,
    segment: str,
    scope: str,
) -> GameRecordHolder | None:
    baseline = _baseline_row(session, metric, segment, scope)
    computed: GameRecordHolder | None = None
    if metric.source == "scoring_pp":
        computed = _best_from_scoring(session, metric, segment, scope, pp=True)
    elif metric.source == "scoring_sh":
        computed = _best_from_scoring(session, metric, segment, scope, pp=False)
    elif metric.player_kind == "goalie":
        computed = _best_from_goalie_query(session, metric, segment, scope)
    else:
        computed = _best_from_skater_query(session, metric, segment, scope)
    merged = _merge_holders(baseline, computed, metric)
    if merged is None:
        return GameRecordHolder(
            metric=metric,
            value=None,
            display_value="—",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label=None,
            game_id=None,
            source="empty",
        )
    return merged


def build_game_records_page(
    session: Session,
    *,
    segment: str = "rs",
    scope: str = "all",
    player_kind: str = "skater",
) -> dict[str, Any]:
    segment = segment if segment in ("rs", "po") else "rs"
    scope = scope if scope in ("all", "rookie") else "all"
    player_kind = player_kind if player_kind in ("skater", "goalie") else "skater"
    metrics = game_record_metrics(player_kind=player_kind)
    cards = [
        resolve_game_record(session, m, segment, scope).to_template_dict()
        for m in metrics
    ]
    return {
        "segment": segment,
        "scope": scope,
        "player_kind": player_kind,
        "cards": cards,
        "segment_label": "Playoffs" if segment == "po" else "Regular Season",
        "scope_label": "Rookies" if scope == "rookie" else "All Players",
        "kind_label": "Goalies" if player_kind == "goalie" else "Skaters",
    }


def list_baselines(session: Session) -> list[GameRecordBaseline]:
    return list(
        session.scalars(
            select(GameRecordBaseline).order_by(
                GameRecordBaseline.segment,
                GameRecordBaseline.scope,
                GameRecordBaseline.player_kind,
                GameRecordBaseline.metric_key,
            )
        ).all()
    )


def _identity_range_label(identity: FranchiseTeamIdentity) -> str:
    start = int(identity.start_year)
    end = identity.end_year
    if end is None or int(end) >= 2100:
        return f"{start}-present"
    if int(end) == start:
        return str(start)
    return f"{start}-{int(end)}"


def baseline_team_choices_for_admin(session: Session) -> list[GameRecordTeamChoice]:
    """Current teams plus era identities, all saving back to the canonical franchise team id."""
    teams = list(session.scalars(select(Team).order_by(Team.name, Team.nickname)).all())
    by_id = {int(t.id): t for t in teams}
    by_fhm = {
        str(t.fhm_team_id).strip(): t
        for t in teams
        if str(t.fhm_team_id or "").strip()
    }
    choices: list[GameRecordTeamChoice] = [
        GameRecordTeamChoice(
            value=int(t.id),
            label=t.full_display_name(),
            sort_label=t.full_display_name(),
        )
        for t in teams
    ]
    seen_identity_choices: set[tuple[int, str]] = set()
    identities = session.scalars(
        select(FranchiseTeamIdentity).order_by(
            FranchiseTeamIdentity.display_name,
            FranchiseTeamIdentity.start_year,
            FranchiseTeamIdentity.id,
        )
    ).all()
    for identity in identities:
        team = by_id.get(int(identity.team_id)) if identity.team_id else None
        if team is None and identity.team_fhm_id:
            team = by_fhm.get(str(identity.team_fhm_id).strip())
        if team is None:
            continue
        name = (identity.display_name or "").strip()
        if not name:
            continue
        label = f"{name} ({_identity_range_label(identity)})"
        key = (int(team.id), label)
        if key in seen_identity_choices:
            continue
        seen_identity_choices.add(key)
        choices.append(
            GameRecordTeamChoice(
                value=int(team.id),
                label=label,
                sort_label=name,
                identity_id=int(identity.id),
            )
        )
    return sorted(choices, key=lambda c: (c.sort_label.casefold(), c.label.casefold(), c.value))


def upsert_baseline(
    session: Session,
    *,
    metric_key: str,
    segment: str,
    scope: str,
    player_kind: str,
    value: float,
    player_id: int | None = None,
    team_id: int | None = None,
    opponent_team_id: int | None = None,
    game_id: int | None = None,
    game_date: date | None = None,
    season_label: str | None = None,
    notes: str | None = None,
) -> GameRecordBaseline:
    row = session.scalars(
        select(GameRecordBaseline).where(
            GameRecordBaseline.metric_key == metric_key,
            GameRecordBaseline.segment == segment,
            GameRecordBaseline.scope == scope,
            GameRecordBaseline.player_kind == player_kind,
        ).limit(1)
    ).first()
    if row is None:
        row = GameRecordBaseline(
            metric_key=metric_key,
            segment=segment,
            scope=scope,
            player_kind=player_kind,
            value=float(value),
        )
        session.add(row)
    row.value = float(value)
    row.player_id = player_id
    row.team_id = team_id
    row.opponent_team_id = opponent_team_id
    row.game_id = game_id
    row.game_date = game_date
    row.season_label = season_label
    row.notes = notes
    return row


def delete_baseline(session: Session, baseline_id: int) -> bool:
    row = session.get(GameRecordBaseline, int(baseline_id))
    if row is None:
        return False
    session.delete(row)
    return True


def metric_choices_for_admin() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for kind in ("skater", "goalie"):
        for m in game_record_metrics(player_kind=kind):
            out.append((m.key, m.title, kind))
    return out

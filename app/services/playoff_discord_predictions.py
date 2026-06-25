"""Build Discord payloads for admin /predict playoff series posts."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from app.models import Game, Season, Team, TeamSeasonAggregate, TeamStanding
from app.services.game_preview import _pp_pk_ranks_for_rs
from app.services.playoff_bracket import playoff_bracket_payload
from app.services.playoff_series_prediction import (
    PREDICTION_METHOD_NOTE,
    _is_regular_season_game,
    load_rs_head_to_head,
)
from app.services.seasons import get_current_season, season_with_imported_data_fallback


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _rank_paren(rank: int | None) -> str:
    if rank is None:
        return "—"
    return f"({_ordinal(rank)})"


def _dense_rank_map(pairs: list[tuple[int, float]], *, high_good: bool) -> dict[int, int]:
    if not pairs:
        return {}
    ordered = sorted(pairs, key=lambda x: x[1], reverse=high_good)
    out: dict[int, int] = {}
    prev_val: float | None = None
    rank = 0
    for idx, (tid, val) in enumerate(ordered, start=1):
        if prev_val is None or abs(val - prev_val) > 1e-9:
            rank = idx
            prev_val = val
        out[int(tid)] = rank
    return out


def _gf_ga_rank_maps(session, season_id: int) -> tuple[dict[int, int], dict[int, int]]:
    rows = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    rates_gf: list[tuple[int, float]] = []
    rates_ga: list[tuple[int, float]] = []
    for st in rows:
        gpd = st.standing_gp_display()
        if gpd > 0 and st.team_id is not None:
            rates_gf.append((int(st.team_id), float(st.gf or 0) / float(gpd)))
            rates_ga.append((int(st.team_id), float(st.ga or 0) / float(gpd)))
    return (
        _dense_rank_map(rates_gf, high_good=True),
        _dense_rank_map(rates_ga, high_good=False),
    )


def _team_agg(session, season_id: int, team_id: int) -> TeamSeasonAggregate | None:
    return session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season_id,
            TeamSeasonAggregate.team_id == team_id,
            TeamSeasonAggregate.stat_segment == "rs",
        )
    ).first()


def _pp_pk_pct(agg: TeamSeasonAggregate | None) -> tuple[float | None, float | None]:
    if agg is None:
        return None, None
    pp_pct = None
    pk_pct = None
    if agg.pp_chances and agg.pp_goals is not None and agg.pp_chances > 0:
        pp_pct = round(100.0 * float(agg.pp_goals) / float(agg.pp_chances), 1)
    if agg.sh_chances and agg.sh_chances > 0 and agg.pk_goals_against is not None:
        pk_pct = round(100.0 - (100.0 * float(agg.pk_goals_against) / float(agg.sh_chances)), 1)
    return pp_pct, pk_pct


def _team_stats_line(
    abbrev: str,
    *,
    gf_g: float | None,
    gf_rank: int | None,
    ga_g: float | None,
    ga_rank: int | None,
    pp_pct: float | None,
    pp_rank: int | None,
    pk_pct: float | None,
    pk_rank: int | None,
) -> str:
    parts: list[str] = []
    if gf_g is not None:
        parts.append(f"GF/G {gf_g:.1f} {_rank_paren(gf_rank)}")
    if ga_g is not None:
        parts.append(f"GA/G {ga_g:.1f} {_rank_paren(ga_rank)}")
    if pp_pct is not None:
        parts.append(f"PP% {pp_pct:.1f}% {_rank_paren(pp_rank)}")
    if pk_pct is not None:
        parts.append(f"PK% {pk_pct:.1f}% {_rank_paren(pk_rank)}")
    if not parts:
        return f"{abbrev}: —"
    return f"{abbrev}: " + ", ".join(parts)


def _wl_otl_for_team(game: Game, team_id: int) -> str:
    hs = game.home_score
    aws = game.away_score
    if hs is None or aws is None:
        return "?"
    if int(game.home_team_id) == team_id:
        tf, ta = int(hs), int(aws)
    else:
        tf, ta = int(aws), int(hs)
    if tf > ta:
        return "W"
    if tf < ta:
        if game.went_to_overtime or game.went_to_shootout:
            return "OTL"
        return "L"
    return "T"


def _season_h2h_record(
    session,
    season_id: int,
    team_a_id: int,
    team_b_id: int,
) -> dict[str, Any]:
    games = session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status == "final",
            (
                ((Game.home_team_id == team_a_id) & (Game.away_team_id == team_b_id))
                | ((Game.home_team_id == team_b_id) & (Game.away_team_id == team_a_id))
            ),
        )
    ).all()
    rs_games = [g for g in games if _is_regular_season_game(g.game_type)]
    w = l = otl = t = 0
    for g in rs_games:
        r = _wl_otl_for_team(g, team_a_id)
        if r == "W":
            w += 1
        elif r == "L":
            l += 1
        elif r == "OTL":
            otl += 1
        elif r == "T":
            t += 1
    return {
        "gp": len(rs_games),
        "w": w,
        "l": l,
        "otl": otl,
        "ties": t,
        "str": f"{w}-{l}-{otl}" + (f"-{t}" if t else "") if rs_games else None,
    }


def _h2h_goals_for_pair(
    h2h: dict[tuple[int, int], tuple[int, int, int]],
    team_a_id: int,
    team_b_id: int,
) -> tuple[int | None, int | None]:
    lo, hi = (team_a_id, team_b_id) if team_a_id < team_b_id else (team_b_id, team_a_id)
    row = h2h.get((lo, hi))
    if not row or row[2] <= 0:
        return None, None
    gf_lo, gf_hi, _n = row
    if team_a_id == lo:
        return int(gf_lo), int(gf_hi)
    return int(gf_hi), int(gf_lo)


def _series_pair_key(series: dict[str, Any]) -> tuple[int, int] | None:
    ta = series.get("team_a") or {}
    tb = series.get("team_b") or {}
    if not ta.get("id") or not tb.get("id"):
        return None
    a, b = int(ta["id"]), int(tb["id"])
    return (min(a, b), max(a, b))


def collect_bracket_series(bracket: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return unique (round_label, series_json) pairs from a bracket payload."""
    seen: set[tuple[int, int]] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    rounds = (
        ("First round", bracket.get("first_round") or []),
        ("Second round", bracket.get("second_round") or []),
        ("Conference finals", bracket.get("conference_finals") or []),
        ("Championship", [bracket.get("championship")] if bracket.get("championship") else []),
    )
    for label, slots in rounds:
        for slot in slots:
            if not slot:
                continue
            key = _series_pair_key(slot)
            if key is None or key in seen:
                continue
            seen.add(key)
            out.append((label, slot))
    return out


_PLAYOFF_ROUND_LABELS: tuple[str, ...] = (
    "First round",
    "Second round",
    "Conference finals",
    "Championship",
)

_ROUND_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "First round": (
        "first",
        "1",
        "1st",
        "opening",
        "round 1",
        "r1",
        "first round",
        "division quarterfinals",
        "quarterfinal",
        "quarterfinals",
    ),
    "Second round": (
        "second",
        "2",
        "2nd",
        "round 2",
        "r2",
        "second round",
        "semifinal",
        "semifinals",
        "division semifinals",
        "semis",
    ),
    "Conference finals": (
        "conference",
        "conference finals",
        "conference final",
        "conf finals",
        "conf final",
        "cf",
        "final four",
    ),
    "Championship": (
        "championship",
        "final",
        "finals",
        "cup",
        "stanley cup",
        "bowl",
    ),
}


def normalize_predict_round_filter(raw: str | None) -> str | None:
    """Map free-text round input to a canonical bracket round label."""
    key = " ".join(str(raw or "").strip().lower().split())
    if not key:
        return None
    if key in {"all", "every", "every round", "all rounds", "open", "remaining"}:
        return "__all__"
    for label in _PLAYOFF_ROUND_LABELS:
        if key == label.lower():
            return label
    for label, aliases in _ROUND_FILTER_ALIASES.items():
        if key in aliases:
            return label
    for label in _PLAYOFF_ROUND_LABELS:
        short = label.lower()
        if key in short or short.startswith(key):
            return label
    for label, aliases in _ROUND_FILTER_ALIASES.items():
        for alias in aliases:
            if key.startswith(alias) or alias.startswith(key):
                return label
    return None


def series_needs_prediction(series: dict[str, Any]) -> bool:
    """True when a bracket series still needs a prediction post."""
    if bool(series.get("series_complete")):
        return False
    if bool(series.get("preview_only")):
        return False
    ta = series.get("team_a") or {}
    tb = series.get("team_b") or {}
    return bool(ta.get("id") and tb.get("id"))


def open_prediction_series(
    bracket: dict[str, Any],
    *,
    round_filter: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Bracket series that still need predictions, optionally limited to one round."""
    out: list[tuple[str, dict[str, Any]]] = []
    for label, series in collect_bracket_series(bracket):
        if not series_needs_prediction(series):
            continue
        if round_filter is not None and label != round_filter:
            continue
        out.append((label, series))
    return out


def list_prediction_rounds(bracket: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize each round that still has open series."""
    counts: dict[str, int] = {}
    for label, series in collect_bracket_series(bracket):
        if series_needs_prediction(series):
            counts[label] = counts.get(label, 0) + 1
    return [
        {"label": label, "series_count": counts[label]}
        for label in _PLAYOFF_ROUND_LABELS
        if label in counts
    ]


def format_predict_round_help(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return (
            "No playoff series need predictions right now. "
            "Completed and projected-only matchups are skipped."
        )
    lines = [
        "Multiple rounds still need predictions. Choose **round** on `/predict`:",
    ]
    for row in rounds:
        label = str(row.get("label") or "").strip()
        count = int(row.get("series_count") or 0)
        hint = _ROUND_FILTER_ALIASES.get(label, (label.lower(),))[0]
        lines.append(f"• **{label}** — {count} series → round: `{hint}`")
    lines.append("")
    lines.append("Or pick **All open rounds** to queue every series above.")
    lines.append(
        "If you do not see a **round** field on `/predict`, re-register slash commands: "
        "`python -m scripts.league_discord_bot.register_slash_commands`"
    )
    return "\n".join(lines)


def _team_meta(team_json: dict[str, Any] | None, team_row: Team | None) -> dict[str, Any]:
    if not team_json:
        return {}
    fhm = getattr(team_row, "fhm_team_id", None) if team_row else None
    return {
        "id": int(team_json.get("id") or 0),
        "abbrev": str(team_json.get("abbreviation") or team_json.get("name") or "").strip(),
        "name": str(team_json.get("name") or "").strip(),
        "fhm_team_id": int(fhm) if fhm is not None else None,
    }


def build_playoff_predictions_discord_payload(
    session,
    *,
    league_slug: str,
    round_filter: str | None = None,
    bracket: dict[str, Any] | None = None,
    season: Season | None = None,
) -> dict[str, Any]:
    if season is None:
        canonical = get_current_season()
        season = season_with_imported_data_fallback(session, canonical) if canonical else None
    if season is None:
        return {"error": "No imported season data is available yet."}
    if bracket is None:
        bracket = playoff_bracket_payload(int(season.id), include_team_logos=False)
    if bracket.get("empty"):
        return {"error": str(bracket.get("message") or "No playoff bracket is available yet.")}
    series_rows = open_prediction_series(bracket, round_filter=round_filter)
    if not series_rows:
        if round_filter:
            open_rounds = list_prediction_rounds(bracket)
            if open_rounds:
                return {
                    "error": (
                        f"No open series found for **{round_filter}**. "
                        f"{format_predict_round_help(open_rounds)}"
                    )
                }
        return {"error": "No playoff series need predictions right now."}

    gf_rank_map, ga_rank_map = _gf_ga_rank_maps(session, int(season.id))
    pp_rank_map, pk_rank_map = _pp_pk_ranks_for_rs(session, int(season.id))
    h2h = load_rs_head_to_head(session, int(season.id))
    standings = {
        int(st.team_id): st
        for st in session.scalars(select(TeamStanding).where(TeamStanding.season_id == season.id)).all()
    }
    team_ids: set[int] = set()
    for _label, s in series_rows:
        for side in (s.get("team_a") or {}, s.get("team_b") or {}):
            tid = side.get("id")
            if tid:
                team_ids.add(int(tid))
    teams_by_id = (
        {int(t.id): t for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()}
        if team_ids
        else {}
    )

    formatted_series: list[dict[str, Any]] = []
    for round_label, series in series_rows:
        ta_json = series.get("team_a") or {}
        tb_json = series.get("team_b") or {}
        ta_id = int(ta_json.get("id") or 0)
        tb_id = int(tb_json.get("id") or 0)
        ta_row = teams_by_id.get(ta_id)
        tb_row = teams_by_id.get(tb_id)
        ta_meta = _team_meta(ta_json, ta_row)
        tb_meta = _team_meta(tb_json, tb_row)

        def stats_for(team_id: int, abbrev: str) -> str:
            st = standings.get(team_id)
            agg = _team_agg(session, int(season.id), team_id)
            pp_pct, pk_pct = _pp_pk_pct(agg)
            gf_g = ga_g = None
            if st is not None:
                gpd = st.standing_gp_display()
                if gpd > 0:
                    gf_g = round(float(st.gf or 0) / float(gpd), 1)
                    ga_g = round(float(st.ga or 0) / float(gpd), 1)
            return _team_stats_line(
                abbrev,
                gf_g=gf_g,
                gf_rank=gf_rank_map.get(team_id),
                ga_g=ga_g,
                ga_rank=ga_rank_map.get(team_id),
                pp_pct=pp_pct,
                pp_rank=pp_rank_map.get(team_id),
                pk_pct=pk_pct,
                pk_rank=pk_rank_map.get(team_id),
            )

        pred = series.get("prediction") or {}
        fav = pred.get("favorite") or {}
        fav_pct = pred.get("favorite_win_series")
        if fav_pct is not None and fav.get("abbreviation"):
            prediction_line = f"{fav.get('abbreviation')} {float(fav_pct) * 100:.1f}% to win series"
        elif pred.get("team_a_win_series") is not None:
            ta_abbr = ta_meta.get("abbrev") or "A"
            tb_abbr = tb_meta.get("abbrev") or "B"
            pa = float(pred["team_a_win_series"]) * 100.0
            pb = 100.0 - pa
            prediction_line = f"{ta_abbr} {pa:.1f}% · {tb_abbr} {pb:.1f}%"
        else:
            prediction_line = "Prediction unavailable"

        h2h_rec = _season_h2h_record(session, int(season.id), ta_id, tb_id)
        goals_a, goals_b = _h2h_goals_for_pair(h2h, ta_id, tb_id)
        if h2h_rec.get("gp"):
            h2h_line = (
                f"{ta_meta.get('abbrev') or 'A'} {h2h_rec.get('str')} vs "
                f"{tb_meta.get('abbrev') or 'B'}"
            )
            if goals_a is not None and goals_b is not None:
                h2h_line += f" · {goals_a}-{goals_b} goals"
        else:
            h2h_line = "No regular-season meetings"

        formatted_series.append(
            {
                "round_label": round_label,
                "team_a": ta_meta,
                "team_b": tb_meta,
                "team_a_stats": stats_for(ta_id, ta_meta.get("abbrev") or "A"),
                "team_b_stats": stats_for(tb_id, tb_meta.get("abbrev") or "B"),
                "prediction_line": prediction_line,
                "h2h_line": h2h_line,
                "series_score": f"{series.get('wins_a', 0)}-{series.get('wins_b', 0)}",
            }
        )

    source_id = f"{int(season.id)}-{int(time.time())}"
    title = f"Playoff predictions — {season.label}"
    if round_filter:
        title = f"{title} · {round_filter}"
    return {
        "payload": {
            "title": title,
            "season_id": int(season.id),
            "season_label": season.label,
            "round_filter": round_filter or "",
            "series": formatted_series,
            "series_count": len(formatted_series),
            "prediction_method_note": PREDICTION_METHOD_NOTE,
            "source_id": source_id,
        }
    }

from __future__ import annotations

from sqlalchemy import select

from app.models import Season, Team, TeamStanding


def _rank_rows(rows: list[dict]) -> list[dict]:
    ranked = sorted(
        rows,
        key=lambda r: (
            -int(r.get("pts", 0)),
            -int(r.get("w", 0)),
            -int(r.get("gf", 0) - r.get("ga", 0)),
            str(r.get("team_name") or ""),
        ),
    )
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return ranked


def build_prediction_snapshot(
    session,
    *,
    selected_team_id: int | None = None,
    add_wins: int = 0,
    add_otl: int = 0,
    add_losses: int = 0,
) -> dict:
    current = session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current is None:
        current = session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    if current is None:
        return {
            "season_label": "—",
            "base_rows": [],
            "projected_rows": [],
            "selected_team_id": None,
            "inputs": {"add_wins": add_wins, "add_otl": add_otl, "add_losses": add_losses},
            "disclaimer": "No current season loaded.",
        }
    standings = session.scalars(
        select(TeamStanding).where(TeamStanding.season_id == int(current.id))
    ).all()
    team_ids = {int(s.team_id) for s in standings}
    teams = (
        {int(t.id): t for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()}
        if team_ids
        else {}
    )
    base_rows: list[dict] = []
    for s in standings:
        tm = teams.get(int(s.team_id))
        base_rows.append(
            {
                "team_id": int(s.team_id),
                "team_name": tm.full_display_name() if tm else f"team_id={s.team_id}",
                "pts": int(s.pts or 0),
                "w": int(s.w or 0),
                "l": int(s.l or 0),
                "otl": int(s.otl or 0),
                "gf": int(s.gf or 0),
                "ga": int(s.ga or 0),
            }
        )
    base_rows = _rank_rows(base_rows)
    projected_rows = [dict(r) for r in base_rows]
    selected_team_id = int(selected_team_id) if selected_team_id else None
    if selected_team_id is not None:
        for r in projected_rows:
            if int(r["team_id"]) == selected_team_id:
                r["w"] = int(r["w"]) + max(0, int(add_wins or 0))
                r["otl"] = int(r["otl"]) + max(0, int(add_otl or 0))
                r["l"] = int(r["l"]) + max(0, int(add_losses or 0))
                r["pts"] = int(r["pts"]) + (2 * max(0, int(add_wins or 0))) + max(0, int(add_otl or 0))
                break
    projected_rows = _rank_rows(projected_rows)
    base_rank = {int(r["team_id"]): int(r["rank"]) for r in base_rows}
    for r in projected_rows:
        old_rank = base_rank.get(int(r["team_id"]), int(r["rank"]))
        r["base_rank"] = old_rank
        r["rank_delta"] = int(old_rank) - int(r["rank"])
    return {
        "season_label": str(current.label or "—"),
        "base_rows": base_rows,
        "projected_rows": projected_rows,
        "selected_team_id": selected_team_id,
        "inputs": {
            "add_wins": max(0, int(add_wins or 0)),
            "add_otl": max(0, int(add_otl or 0)),
            "add_losses": max(0, int(add_losses or 0)),
        },
        "disclaimer": (
            "Simplified model: adds W/OTL/L points only; does not adjust GP, ROW, or head-to-head. "
            "Rank sort matches site standings tie order (PTS, W, GF−GA)."
        ),
    }

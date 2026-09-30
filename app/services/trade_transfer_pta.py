"""BOWL-Relegation: PTA / transfer-fee validation on Trade Tool ledgers (dry-run)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.transfer_rules import (
    bowl_team_budget_snapshot,
    is_transfer_tool_league,
    load_transfer_rules_config,
    rules_snapshot_for_players,
)


def relegation_trade_pta_dry_run_only(league_slug: str | None) -> bool:
    """When True, Trade Tool publish is blocked; use PTA dry-run instead."""
    import os

    slug = (league_slug or "").strip()
    if not is_transfer_tool_league(slug):
        return False
    raw = os.environ.get("RELEGATION_TRADE_PTA_DRY_RUN", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def gm_trade_proposals_enabled(league_slug: str | None) -> bool:
    """GM Trade Tool → partner → commissioner on BOWL-Relegation."""
    import os

    slug = (league_slug or "").strip()
    if not is_transfer_tool_league(slug):
        return False
    raw = os.environ.get("RELEGATION_GM_TRADE_PROPOSALS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def format_transfer_compensation_summary(comp: dict[str, Any]) -> str:
    lines: list[str] = ["Transfer compensation:"]
    left = comp.get("left_acquiring") or {}
    right = comp.get("right_acquiring") or {}
    l_pta = int(left.get("pta_transfer_fee") or 0)
    l_cash = int(left.get("cash_sweetener") or 0)
    r_pta = int(right.get("pta_transfer_fee") or 0)
    r_cash = int(right.get("cash_sweetener") or 0)
    if not any((l_pta, l_cash, r_pta, r_cash)):
        return ""
    lines.append(f"  • Team A acquiring: PTA ${l_pta:,}" + (f", cash ${l_cash:,}" if l_cash else ""))
    lines.append(f"  • Team B acquiring: PTA ${r_pta:,}" + (f", cash ${r_cash:,}" if r_cash else ""))
    return "\n".join(lines)


def default_transfer_compensation() -> dict[str, Any]:
    return {
        "left_acquiring": {"pta_transfer_fee": 0, "cash_sweetener": 0},
        "right_acquiring": {"pta_transfer_fee": 0, "cash_sweetener": 0},
    }


def parse_trade_ledger_object(raw: str | None) -> dict[str, Any]:
    from app.services.trade_tool import parse_ledger_payload

    left_out, right_out = parse_ledger_payload(raw)
    comp = default_transfer_compensation()
    if raw and str(raw).strip():
        try:
            import json

            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            tc = data.get("transfer_compensation")
            if isinstance(tc, dict):
                for side in ("left_acquiring", "right_acquiring"):
                    block = tc.get(side)
                    if isinstance(block, dict):
                        comp[side] = {
                            "pta_transfer_fee": int(block.get("pta_transfer_fee") or 0),
                            "cash_sweetener": int(block.get("cash_sweetener") or 0),
                        }
    return {
        "from_left_to_right": left_out,
        "from_right_to_left": right_out,
        "transfer_compensation": comp,
    }


def player_ids_from_drag_keys(keys: list[str]) -> list[int]:
    out: list[int] = []
    for key in keys:
        k = str(key or "").strip()
        if not k.startswith("player:"):
            continue
        try:
            out.append(int(k.split(":", 1)[1]))
        except (ValueError, IndexError):
            continue
    return out


def _side_preview(
    session: Session,
    *,
    league_slug: str,
    acquiring_team_id: int,
    seller_team: Team | None,
    player_ids: list[int],
    compensation: dict[str, Any],
    raw_dir: Path | None,
) -> dict[str, Any]:
    cfg = load_transfer_rules_config(league_slug)
    errors: list[str] = []
    players: list[dict[str, Any]] = []
    if not player_ids:
        return {
            "incoming_player_count": 0,
            "required_pta_fee_usd": 0,
            "offered_pta_usd": int(compensation.get("pta_transfer_fee") or 0),
            "offered_cash_usd": int(compensation.get("cash_sweetener") or 0),
            "team_budget": bowl_team_budget_snapshot(
                session, bowl_team_id=int(acquiring_team_id), league_slug=league_slug
            ),
            "players": players,
            "errors": errors,
            "ok": True,
        }
    if seller_team is None:
        return {
            "incoming_player_count": len(player_ids),
            "required_pta_fee_usd": 0,
            "errors": ["Seller team not found."],
            "ok": False,
        }
    roster = [session.get(Player, int(pid)) for pid in player_ids]
    roster = [p for p in roster if p]
    if len(roster) != len(player_ids):
        errors.append("One or more incoming players are missing from the import.")
    snap = rules_snapshot_for_players(
        session,
        players=roster,
        external_team=seller_team,
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    required = int(snap.get("required_pta_fee_usd") or 0)
    for pl in roster:
        ctx_notes: list[str] = []
        blocked = False
        block_reason = ""
        fee = required
        if snap.get("any_blocked"):
            blocked = True
            reasons = snap.get("block_reasons") or []
            block_reason = str(reasons[0]) if reasons else "Blocked by transfer rules."
        players.append(
            {
                "id": int(pl.id),
                "name": pl.full_name,
                "pta_fee_usd": fee,
                "blocked": blocked,
                "block_reason": block_reason,
            }
        )
    pta = int(compensation.get("pta_transfer_fee") or 0)
    cash = int(compensation.get("cash_sweetener") or 0)
    if pta < required:
        errors.append(f"PTA fee must be at least ${required:,}.")
    if snap.get("any_blocked"):
        for r in snap.get("block_reasons") or []:
            errors.append(str(r))
    budget = bowl_team_budget_snapshot(
        session, bowl_team_id=int(acquiring_team_id), league_slug=league_slug
    )
    remaining = budget.get("remaining_budget_usd")
    if remaining is not None and (pta + cash) > int(remaining):
        errors.append(
            f"PTA plus cash (${pta + cash:,}) exceeds remaining budget "
            f"(${int(remaining):,})."
        )
    if cash and not cfg.allow_cash_sweetener:
        errors.append("Cash sweeteners are not allowed.")
    return {
        "incoming_player_count": len(player_ids),
        "required_pta_fee_usd": required,
        "offered_pta_usd": pta,
        "offered_cash_usd": cash,
        "team_budget": budget,
        "players": players,
        "rules_snapshot": snap,
        "errors": errors,
        "ok": not errors,
    }


def run_trade_pta_dry_run(
    session: Session,
    *,
    league_slug: str,
    left_team_id: int,
    right_team_id: int,
    ledger_raw: str | None,
    raw_dir: Path | None,
    ledger_error: str | None = None,
) -> dict[str, Any]:
    if not is_transfer_tool_league(league_slug):
        return {"valid": False, "errors": ["PTA dry-run is only available on BOWL-Relegation."], "dry_run": True}
    if ledger_error:
        return {"valid": False, "errors": [ledger_error], "dry_run": True}
    obj = parse_trade_ledger_object(ledger_raw)
    left_out = obj["from_left_to_right"]
    right_out = obj["from_right_to_left"]
    comp = obj["transfer_compensation"]
    left_team = session.get(Team, int(left_team_id))
    right_team = session.get(Team, int(right_team_id))
    incoming_to_left = player_ids_from_drag_keys(right_out)
    incoming_to_right = player_ids_from_drag_keys(left_out)
    left_side = _side_preview(
        session,
        league_slug=league_slug,
        acquiring_team_id=int(left_team_id),
        seller_team=right_team,
        player_ids=incoming_to_left,
        compensation=comp.get("left_acquiring") or {},
        raw_dir=raw_dir,
    )
    right_side = _side_preview(
        session,
        league_slug=league_slug,
        acquiring_team_id=int(right_team_id),
        seller_team=left_team,
        player_ids=incoming_to_right,
        compensation=comp.get("right_acquiring") or {},
        raw_dir=raw_dir,
    )
    errors: list[str] = []
    errors.extend(left_side.get("errors") or [])
    errors.extend(right_side.get("errors") or [])
    summary_lines = [
        "DRY RUN — no trade published, no roster changes.",
        "",
        f"Team A incoming players: {left_side.get('incoming_player_count', 0)} · "
        f"required PTA ${int(left_side.get('required_pta_fee_usd') or 0):,} · "
        f"offered PTA ${int(left_side.get('offered_pta_usd') or 0):,} · "
        f"cash ${int(left_side.get('offered_cash_usd') or 0):,}",
        f"Team B incoming players: {right_side.get('incoming_player_count', 0)} · "
        f"required PTA ${int(right_side.get('required_pta_fee_usd') or 0):,} · "
        f"offered PTA ${int(right_side.get('offered_pta_usd') or 0):,} · "
        f"cash ${int(right_side.get('offered_cash_usd') or 0):,}",
    ]
    if errors:
        summary_lines.extend(["", "Issues:"])
        summary_lines.extend(f"  • {e}" for e in errors)
    else:
        summary_lines.extend(["", "PTA / budget checks passed for this ledger."])
    return {
        "valid": not errors,
        "dry_run": True,
        "errors": errors,
        "left_team_id": int(left_team_id),
        "right_team_id": int(right_team_id),
        "left": left_side,
        "right": right_side,
        "summary": "\n".join(summary_lines),
        "transfer_compensation": comp,
    }

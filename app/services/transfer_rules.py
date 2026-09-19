"""Cross-league transfer rules (PTA fees, KHL blocks, eligibility) for BOWL-Relegation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LeagueMeta, Player, Team
from app.services.draft_hub_eligibility import age_as_of
from app.services.player_contract_csv import contract_export_row, contract_years_remaining_major
from app.services.rfa_offers import EUROPEAN_NATIONALITIES, _is_european
from app.services.seasons import get_current_season, season_age_reference_date

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "transfer_rules_bowl_fantasy.json"

STATUS_PENDING_AI = "pending_ai"
STATUS_AI_DECLINED = "ai_declined"
STATUS_AI_COUNTER = "ai_counter"
STATUS_PENDING_COMMISSIONER = "pending_commissioner"
STATUS_COMMISSIONER_DECLINED = "commissioner_declined"
STATUS_PUBLISHED = "published"


@dataclass
class TransferRulesConfig:
    league_slug: str = "bowl-fantasy"
    eligible_external_league_fhm_ids: tuple[int, ...] = ()
    excluded_external_league_fhm_ids: tuple[int, ...] = ()
    allow_draft_pick_sweeteners: bool = True
    allow_player_sweeteners: bool = True
    allow_cash_sweetener: bool = True
    max_players_acquired: int = 1
    max_sweetener_picks: int = 2
    max_sweetener_players: int = 2
    european_rights_window_years: int = 4
    north_american_rights_window_years: int = 2
    ufa_min_age: int = 22
    khl_league_fhm_id: int = 6
    khl_requires_contract_expiry: bool = True
    european_pta_fees_usd: dict[str, Any] = field(default_factory=dict)
    pick_value_to_usd_multiplier: float = 50000.0
    ai_partner: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerTransferContext:
    player: Player
    external_team: Team
    external_league_fhm_id: int
    age: int | None
    is_european: bool
    is_russian: bool
    is_khl: bool
    is_ufa_path: bool
    contract_years_remaining: int | None
    average_salary: int | None
    pta_fee_usd: int
    blocked: bool
    block_reason: str
    rule_notes: list[str]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=4)
def _load_config_cached(path_str: str, mtime: float) -> TransferRulesConfig:
    del mtime
    path = Path(path_str)
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
    fees = raw.get("european_pta_fees_usd") or {}
    return TransferRulesConfig(
        league_slug=str(raw.get("league_slug") or "bowl-fantasy"),
        eligible_external_league_fhm_ids=tuple(int(x) for x in (raw.get("eligible_external_league_fhm_ids") or [])),
        excluded_external_league_fhm_ids=tuple(int(x) for x in (raw.get("excluded_external_league_fhm_ids") or [])),
        allow_draft_pick_sweeteners=bool(raw.get("allow_draft_pick_sweeteners", True)),
        allow_player_sweeteners=bool(raw.get("allow_player_sweeteners", True)),
        allow_cash_sweetener=bool(raw.get("allow_cash_sweetener", True)),
        max_players_acquired=max(1, int(raw.get("max_players_acquired") or 1)),
        max_sweetener_picks=max(0, int(raw.get("max_sweetener_picks") or 2)),
        max_sweetener_players=max(0, int(raw.get("max_sweetener_players") or 2)),
        european_rights_window_years=int(raw.get("european_rights_window_years") or 4),
        north_american_rights_window_years=int(raw.get("north_american_rights_window_years") or 2),
        ufa_min_age=int(raw.get("ufa_min_age") or 22),
        khl_league_fhm_id=int(raw.get("khl_league_fhm_id") or 6),
        khl_requires_contract_expiry=bool(raw.get("khl_requires_contract_expiry", True)),
        european_pta_fees_usd=fees if isinstance(fees, dict) else {},
        pick_value_to_usd_multiplier=float(raw.get("pick_value_to_usd_multiplier") or 50000),
        ai_partner={k: float(v) for k, v in (raw.get("ai_partner") or {}).items() if isinstance(v, (int, float))},
        raw=raw,
    )


def load_transfer_rules_config(league_slug: str = "bowl-fantasy") -> TransferRulesConfig:
    """Load JSON rules for the league mount (v1: bowl-fantasy only)."""
    del league_slug
    path = _CONFIG_PATH
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    return _load_config_cached(str(path.resolve()), mtime)


def is_transfer_tool_league(league_slug: str | None) -> bool:
    return (league_slug or "").strip() == "bowl-fantasy"


def bowl_main_league_fhm_ids(session: Session) -> frozenset[int]:
    from app.services.all_time_records import bowl_nhl_league_ids

    return frozenset(bowl_nhl_league_ids(session) or (0, 1))


def external_leagues_for_transfer(session: Session, league_slug: str) -> list[dict[str, Any]]:
    """External FHM leagues eligible for cross-league acquisition."""
    cfg = load_transfer_rules_config(league_slug)
    main_ids = bowl_main_league_fhm_ids(session)
    rows = session.scalars(select(LeagueMeta).order_by(LeagueMeta.name)).all()
    out: list[dict[str, Any]] = []
    for lm in rows:
        lid = int(lm.fhm_league_id)
        if lid in main_ids:
            continue
        if cfg.eligible_external_league_fhm_ids and lid not in cfg.eligible_external_league_fhm_ids:
            continue
        if lid in cfg.excluded_external_league_fhm_ids:
            continue
        out.append(
            {
                "fhm_league_id": lid,
                "name": lm.name,
                "abbreviation": lm.abbreviation or "",
            }
        )
    return out


def external_teams_for_league(session: Session, fhm_league_id: int) -> list[Team]:
    return list(
        session.scalars(
            select(Team)
            .where(Team.fhm_league_id == int(fhm_league_id))
            .order_by(Team.name)
        ).all()
    )


def _is_russian(player: Player) -> bool:
    nat = str(player.nationality or "").strip().upper()
    return nat in {"RUS", "RUSSIA", "BLR", "BELARUS", "KAZ", "KAZAKHSTAN"}


def _player_age(session: Session, player: Player) -> int | None:
    if not player.birth_date:
        return None
    season = get_current_season(session)
    ref = season_age_reference_date(season)
    return age_as_of(player.birth_date, ref)


def _pta_fee_for_player(cfg: TransferRulesConfig, *, league_fhm_id: int, age: int | None) -> int:
    fees = cfg.european_pta_fees_usd or {}
    by_league = fees.get("by_league_fhm_id") or {}
    base = int(by_league.get(str(league_fhm_id)) or fees.get("default") or 350000)
    brackets = fees.get("by_age_bracket") or {}
    if age is None:
        return base
    if age < 22:
        return int(brackets.get("under_22") or base)
    if age <= 25:
        return int(brackets.get("22_25") or base)
    return int(brackets.get("26_plus") or base)


def build_player_transfer_context(
    session: Session,
    *,
    player: Player,
    external_team: Team,
    league_slug: str,
    raw_dir: Path | None = None,
) -> PlayerTransferContext:
    cfg = load_transfer_rules_config(league_slug)
    league_fhm_id = int(external_team.fhm_league_id or 0)
    age = _player_age(session, player)
    european = _is_european(player)
    russian = _is_russian(player)
    is_khl = league_fhm_id == cfg.khl_league_fhm_id
    contract = player.contract
    season = get_current_season(session)
    season_start = int(season.start_year) if season and season.start_year else None
    years_left = contract_years_remaining_major(
        getattr(player, "fhm_player_id", None),
        season_start,
        raw_import_dir=raw_dir,
    )
    avg_salary = int(contract.average_salary) if contract and contract.average_salary else None
    export_row = contract_export_row(getattr(player, "fhm_player_id", None), raw_dir)
    is_ufa = bool(contract.is_ufa) if contract else False
    if export_row is not None:
        from app.services.player_contract_csv import contract_export_is_ufa

        ufa_flag = contract_export_is_ufa(getattr(player, "fhm_player_id", None), raw_dir)
        if ufa_flag is not None:
            is_ufa = bool(ufa_flag)

    is_ufa_path = bool(age is not None and age >= cfg.ufa_min_age and is_ufa)
    notes: list[str] = []
    blocked = False
    block_reason = ""
    pta_fee = 0

    if is_khl and cfg.khl_requires_contract_expiry:
        if years_left and years_left > 0:
            blocked = True
            block_reason = (
                "KHL has no NHL transfer agreement — player must have an expired contract "
                "or mutual consent before BOWL can sign them."
            )
        notes.append("KHL: no PTA fee; contract must be clear.")
    elif is_ufa_path and european:
        pta_fee = 0
        notes.append(f"European UFA path (age {age}+, undrafted/free agent).")
    elif european:
        pta_fee = _pta_fee_for_player(cfg, league_fhm_id=league_fhm_id, age=age)
        notes.append(f"European PTA transfer fee: ${pta_fee:,}.")
        notes.append("Standard European pro contract includes NHL Out clause.")
    elif russian and not is_khl:
        pta_fee = _pta_fee_for_player(cfg, league_fhm_id=league_fhm_id, age=age)
        notes.append("Russian player outside KHL — PTA-style fee applies.")
    else:
        pta_fee = int((cfg.european_pta_fees_usd or {}).get("default") or 200000)
        notes.append("Non-European external signing — baseline transfer fee applies.")

    if years_left and years_left > 0 and not is_khl:
        notes.append(f"Contract: {years_left} year(s) remaining on current deal.")

    return PlayerTransferContext(
        player=player,
        external_team=external_team,
        external_league_fhm_id=league_fhm_id,
        age=age,
        is_european=european,
        is_russian=russian,
        is_khl=is_khl,
        is_ufa_path=is_ufa_path,
        contract_years_remaining=years_left,
        average_salary=avg_salary,
        pta_fee_usd=int(pta_fee),
        blocked=blocked,
        block_reason=block_reason,
        rule_notes=notes,
    )


def rules_snapshot_for_players(
    session: Session,
    *,
    players: list[Player],
    external_team: Team,
    league_slug: str,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    cfg = load_transfer_rules_config(league_slug)
    contexts = [
        build_player_transfer_context(
            session,
            player=pl,
            external_team=external_team,
            league_slug=league_slug,
            raw_dir=raw_dir,
        )
        for pl in players
    ]
    total_pta = sum(c.pta_fee_usd for c in contexts)
    blocked = [c for c in contexts if c.blocked]
    return {
        "config_version": cfg.raw,
        "external_team_id": int(external_team.id),
        "external_league_fhm_id": int(external_team.fhm_league_id or 0),
        "required_pta_fee_usd": total_pta,
        "players": [
            {
                "player_id": int(c.player.id),
                "player_name": c.player.full_name,
                "pta_fee_usd": c.pta_fee_usd,
                "blocked": c.blocked,
                "block_reason": c.block_reason,
                "rule_notes": c.rule_notes,
                "age": c.age,
                "contract_years_remaining": c.contract_years_remaining,
            }
            for c in contexts
        ],
        "any_blocked": bool(blocked),
        "block_reasons": [c.block_reason for c in blocked if c.block_reason],
    }


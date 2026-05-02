from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.site_models import LeagueRuleSetting

DEFAULT_LEAGUE_RULES: tuple[dict[str, str], ...] = (
    {"rule_key": "roster_max_size", "rule_value": "23"},
    {"rule_key": "waiver_window_open", "rule_value": "true"},
    {"rule_key": "schedule_frozen", "rule_value": "false"},
    {"rule_key": "trade_deadline_utc", "rule_value": ""},
    {"rule_key": "salary_cap_enabled", "rule_value": "false"},
    {"rule_key": "salary_cap_amount", "rule_value": ""},
    {"rule_key": "playoff_roster_lock", "rule_value": "true"},
    # Trade Tool: max draft round number shown for manual (non-CSV) pick chips (1–this value).
    {"rule_key": "trade_tool_draft_rounds", "rule_value": "8"},
)


def ensure_league_rules(session, league_slug: str, updated_by_user_id: int | None = None) -> None:
    rows = session.scalars(
        select(LeagueRuleSetting).where(LeagueRuleSetting.league_slug == league_slug)
    ).all()
    by_key = {r.rule_key: r for r in rows}
    now = datetime.utcnow()
    changed = False
    for item in DEFAULT_LEAGUE_RULES:
        key = str(item["rule_key"])
        if key in by_key:
            continue
        session.add(
            LeagueRuleSetting(
                league_slug=league_slug,
                rule_key=key,
                rule_value=str(item["rule_value"]),
                updated_by_user_id=updated_by_user_id,
                updated_at=now,
            )
        )
        changed = True
    if changed:
        session.commit()


def get_league_rules(session, league_slug: str) -> list[LeagueRuleSetting]:
    ensure_league_rules(session, league_slug)
    return session.scalars(
        select(LeagueRuleSetting)
        .where(LeagueRuleSetting.league_slug == league_slug)
        .order_by(LeagueRuleSetting.rule_key.asc(), LeagueRuleSetting.id.asc())
    ).all()


def get_rule_value(session, league_slug: str, rule_key: str, default: str = "") -> str:
    ensure_league_rules(session, league_slug)
    row = session.scalar(
        select(LeagueRuleSetting)
        .where(
            LeagueRuleSetting.league_slug == league_slug,
            LeagueRuleSetting.rule_key == rule_key,
        )
        .limit(1)
    )
    if row is None:
        return default
    return str(row.rule_value or default)


def rule_bool(session, league_slug: str, rule_key: str, default: bool = False) -> bool:
    raw = get_rule_value(session, league_slug, rule_key, "true" if default else "false")
    s = str(raw or "").strip().lower()
    return s in {"1", "true", "yes", "on"}


def rule_int(session, league_slug: str, rule_key: str, default: int = 0) -> int:
    raw = get_rule_value(session, league_slug, rule_key, str(default))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)


def rule_datetime_utc(session, league_slug: str, rule_key: str) -> datetime | None:
    raw = get_rule_value(session, league_slug, rule_key, "")
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def rule_deadline_passed(session, league_slug: str, rule_key: str) -> bool:
    dt = rule_datetime_utc(session, league_slug, rule_key)
    if dt is None:
        return False
    return datetime.utcnow() > dt


@dataclass(frozen=True)
class PointsEconomyRuleResult:
    """Uniform result for AP ledger / redemption approval guards."""

    allowed: bool
    code: str
    message: str


def evaluate_points_economy_mutations_allowed(session, league_slug: str) -> PointsEconomyRuleResult:
    """Block AP economy writes when schedule is frozen or trade deadline passed (matches GM redeem rules)."""
    if rule_bool(session, league_slug, "schedule_frozen", default=False):
        return PointsEconomyRuleResult(
            False,
            "schedule_frozen",
            "AP economy changes are blocked while the schedule is frozen by league rule.",
        )
    if rule_deadline_passed(session, league_slug, "trade_deadline_utc"):
        return PointsEconomyRuleResult(
            False,
            "trade_deadline",
            "AP economy changes are blocked after the configured trade deadline.",
        )
    return PointsEconomyRuleResult(True, "", "")


@dataclass(frozen=True)
class ContractMutationRuleResult:
    allowed: bool
    code: str
    message: str


def evaluate_contract_mutation_allowed(session, league_slug: str) -> ContractMutationRuleResult:
    """Shared contract-edit guards (schedule freeze + trade deadline)."""
    if rule_bool(session, league_slug, "schedule_frozen", default=False):
        return ContractMutationRuleResult(
            False,
            "schedule_frozen",
            "Contract edits are blocked while the schedule is frozen by league rule.",
        )
    if rule_deadline_passed(session, league_slug, "trade_deadline_utc"):
        return ContractMutationRuleResult(
            False,
            "trade_deadline",
            "Contract edits are blocked after the configured trade deadline.",
        )
    return ContractMutationRuleResult(True, "", "")

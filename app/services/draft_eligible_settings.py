"""Per-league Draft Eligible page rules (stored in site ``league_rule_settings``)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select

from app.services.draft_hub_eligibility import (
    DRAFT_POOL_AGE_RULES,
    DRAFT_POOL_BIRTH_WINDOW,
    DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
    DraftEligibilityParams,
    HISTORICAL_AMATEUR_BIRTH_END,
    HISTORICAL_AMATEUR_BIRTH_START,
    default_eligibility_for_league,
)
from app.services.league_rules import ensure_league_rules, get_rule_value
from app.site_models import LeagueRuleSetting
from app.sqlite_retry import commit_with_sqlite_retry

DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW = "birth_window"
DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES = "age_rules"

_RULE_BIRTH_START = "draft_eligible_birth_start"
_RULE_BIRTH_END = "draft_eligible_birth_end"
_RULE_POOL_MODE = "draft_eligible_pool_mode"
_RULE_EXCLUDE_EASTERN_BLOC = "draft_eligible_exclude_eastern_bloc"
_RULE_MIN_AGE = "draft_eligible_min_age_years"
_RULE_MIN_ANCHOR_MONTH = "draft_eligible_min_anchor_month"
_RULE_MIN_ANCHOR_DAY = "draft_eligible_min_anchor_day"
_RULE_MAX_AGE = "draft_eligible_max_age_years"
_RULE_MAX_ANCHOR_MONTH = "draft_eligible_max_anchor_month"
_RULE_MAX_ANCHOR_DAY = "draft_eligible_max_anchor_day"

_DRAFT_ELIGIBLE_RULE_DEFAULTS: tuple[dict[str, str], ...] = (
    {"rule_key": _RULE_POOL_MODE, "rule_value": ""},
    {"rule_key": _RULE_BIRTH_START, "rule_value": ""},
    {"rule_key": _RULE_BIRTH_END, "rule_value": ""},
    {"rule_key": _RULE_EXCLUDE_EASTERN_BLOC, "rule_value": ""},
    {"rule_key": _RULE_MIN_AGE, "rule_value": ""},
    {"rule_key": _RULE_MIN_ANCHOR_MONTH, "rule_value": ""},
    {"rule_key": _RULE_MIN_ANCHOR_DAY, "rule_value": ""},
    {"rule_key": _RULE_MAX_AGE, "rule_value": ""},
    {"rule_key": _RULE_MAX_ANCHOR_MONTH, "rule_value": ""},
    {"rule_key": _RULE_MAX_ANCHOR_DAY, "rule_value": ""},
)


@dataclass(frozen=True)
class DraftEligiblePageConfig:
    pool_mode: str
    birth_start: date
    birth_end: date
    exclude_eastern_bloc: bool
    min_age_years: int
    min_anchor_month: int
    min_anchor_day: int
    max_age_years: int
    max_anchor_month: int
    max_anchor_day: int


def _default_pool_mode(league_slug: str) -> str:
    if league_slug == "bowl-historical":
        return DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW
    return DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES


def default_draft_eligible_page_config(league_slug: str) -> DraftEligiblePageConfig:
    ddef = default_eligibility_for_league(league_slug)
    if league_slug == "bowl-historical":
        return DraftEligiblePageConfig(
            pool_mode=DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
            birth_start=HISTORICAL_AMATEUR_BIRTH_START,
            birth_end=HISTORICAL_AMATEUR_BIRTH_END,
            exclude_eastern_bloc=True,
            min_age_years=ddef.min_age_years,
            min_anchor_month=ddef.min_anchor_month,
            min_anchor_day=ddef.min_anchor_day,
            max_age_years=ddef.max_age_years,
            max_anchor_month=ddef.max_anchor_month,
            max_anchor_day=ddef.max_anchor_day,
        )
    return DraftEligiblePageConfig(
        pool_mode=DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES,
        birth_start=HISTORICAL_AMATEUR_BIRTH_START,
        birth_end=HISTORICAL_AMATEUR_BIRTH_END,
        exclude_eastern_bloc=False,
        min_age_years=ddef.min_age_years,
        min_anchor_month=ddef.min_anchor_month,
        min_anchor_day=ddef.min_anchor_day,
        max_age_years=ddef.max_age_years,
        max_anchor_month=ddef.max_anchor_month,
        max_anchor_day=ddef.max_anchor_day,
    )


def ensure_draft_eligible_rule_rows(session, league_slug: str, updated_by_user_id: int | None = None) -> None:
    ensure_league_rules(session, league_slug, updated_by_user_id=updated_by_user_id)
    rows = session.scalars(
        select(LeagueRuleSetting).where(LeagueRuleSetting.league_slug == league_slug)
    ).all()
    by_key = {str(r.rule_key): r for r in rows}
    now = datetime.utcnow()
    changed = False
    for item in _DRAFT_ELIGIBLE_RULE_DEFAULTS:
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
        commit_with_sqlite_retry(session)


def _parse_date(raw: str, fallback: date) -> date:
    text = str(raw or "").strip()
    if not text:
        return fallback
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return fallback


def _parse_bool(raw: str, fallback: bool) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return fallback
    return text in {"1", "true", "yes", "on"}


def _parse_int(raw: str, fallback: int) -> int:
    text = str(raw or "").strip()
    if not text:
        return fallback
    try:
        return int(text)
    except (TypeError, ValueError):
        return fallback


def load_draft_eligible_page_config(session, league_slug: str) -> DraftEligiblePageConfig:
    ensure_draft_eligible_rule_rows(session, league_slug)
    defaults = default_draft_eligible_page_config(league_slug)
    mode_raw = get_rule_value(session, league_slug, _RULE_POOL_MODE, "")
    pool_mode = mode_raw.strip() if mode_raw.strip() in {
        DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
        DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES,
    } else defaults.pool_mode
    return DraftEligiblePageConfig(
        pool_mode=pool_mode,
        birth_start=_parse_date(
            get_rule_value(session, league_slug, _RULE_BIRTH_START, ""),
            defaults.birth_start,
        ),
        birth_end=_parse_date(
            get_rule_value(session, league_slug, _RULE_BIRTH_END, ""),
            defaults.birth_end,
        ),
        exclude_eastern_bloc=_parse_bool(
            get_rule_value(session, league_slug, _RULE_EXCLUDE_EASTERN_BLOC, ""),
            defaults.exclude_eastern_bloc,
        ),
        min_age_years=_parse_int(
            get_rule_value(session, league_slug, _RULE_MIN_AGE, ""),
            defaults.min_age_years,
        ),
        min_anchor_month=_parse_int(
            get_rule_value(session, league_slug, _RULE_MIN_ANCHOR_MONTH, ""),
            defaults.min_anchor_month,
        ),
        min_anchor_day=_parse_int(
            get_rule_value(session, league_slug, _RULE_MIN_ANCHOR_DAY, ""),
            defaults.min_anchor_day,
        ),
        max_age_years=_parse_int(
            get_rule_value(session, league_slug, _RULE_MAX_AGE, ""),
            defaults.max_age_years,
        ),
        max_anchor_month=_parse_int(
            get_rule_value(session, league_slug, _RULE_MAX_ANCHOR_MONTH, ""),
            defaults.max_anchor_month,
        ),
        max_anchor_day=_parse_int(
            get_rule_value(session, league_slug, _RULE_MAX_ANCHOR_DAY, ""),
            defaults.max_anchor_day,
        ),
    )


def _set_rule_value(
    session,
    league_slug: str,
    rule_key: str,
    value: str,
    *,
    updated_by_user_id: int | None,
) -> None:
    row = session.scalar(
        select(LeagueRuleSetting)
        .where(
            LeagueRuleSetting.league_slug == league_slug,
            LeagueRuleSetting.rule_key == rule_key,
        )
        .limit(1)
    )
    now = datetime.utcnow()
    if row is None:
        session.add(
            LeagueRuleSetting(
                league_slug=league_slug,
                rule_key=rule_key,
                rule_value=value,
                updated_by_user_id=updated_by_user_id,
                updated_at=now,
            )
        )
        return
    row.rule_value = value
    row.updated_by_user_id = updated_by_user_id
    row.updated_at = now


def save_draft_eligible_page_config(
    session,
    league_slug: str,
    config: DraftEligiblePageConfig,
    *,
    updated_by_user_id: int | None = None,
) -> None:
    ensure_draft_eligible_rule_rows(session, league_slug, updated_by_user_id=updated_by_user_id)
    _set_rule_value(
        session,
        league_slug,
        _RULE_POOL_MODE,
        config.pool_mode,
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_BIRTH_START,
        config.birth_start.isoformat(),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_BIRTH_END,
        config.birth_end.isoformat(),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_EXCLUDE_EASTERN_BLOC,
        "true" if config.exclude_eastern_bloc else "false",
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MIN_AGE,
        str(int(config.min_age_years)),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MIN_ANCHOR_MONTH,
        str(int(config.min_anchor_month)),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MIN_ANCHOR_DAY,
        str(int(config.min_anchor_day)),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MAX_AGE,
        str(int(config.max_age_years)),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MAX_ANCHOR_MONTH,
        str(int(config.max_anchor_month)),
        updated_by_user_id=updated_by_user_id,
    )
    _set_rule_value(
        session,
        league_slug,
        _RULE_MAX_ANCHOR_DAY,
        str(int(config.max_anchor_day)),
        updated_by_user_id=updated_by_user_id,
    )
    commit_with_sqlite_retry(session)


def config_to_eligibility_params(
    config: DraftEligiblePageConfig,
    *,
    timeline_year: int,
) -> DraftEligibilityParams:
    if config.pool_mode == DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW:
        pool_source = DRAFT_POOL_BIRTH_WINDOW
    else:
        pool_source = DRAFT_POOL_AGE_RULES
    return DraftEligibilityParams(
        timeline_year=int(timeline_year),
        min_age_years=int(config.min_age_years),
        min_anchor_month=int(config.min_anchor_month),
        min_anchor_day=int(config.min_anchor_day),
        max_age_years=int(config.max_age_years),
        max_anchor_month=int(config.max_anchor_month),
        max_anchor_day=int(config.max_anchor_day),
        pool_source=pool_source,
        born_before_date=None,
        birth_window_start=config.birth_start,
        birth_window_end=config.birth_end,
        exclude_eastern_bloc=bool(config.exclude_eastern_bloc),
    )


def _fmt_long_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def format_draft_eligible_summary(
    config: DraftEligiblePageConfig,
    *,
    league_slug: str,
    timeline_year: int,
) -> str:
    if config.pool_mode == DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW:
        start = _fmt_long_date(config.birth_start).replace("  ", " ")
        end = _fmt_long_date(config.birth_end).replace("  ", " ")
        if league_slug == "bowl-historical":
            base = f"Historical amateur pool shows players born from {start} through {end}"
        else:
            base = f"Draft eligible pool shows players born from {start} through {end}"
        if config.exclude_eastern_bloc:
            return f"{base}, excluding Iron Curtain nationalities."
        return f"{base}."
    if league_slug == "bowl-cap":
        prefix = f"Cap draft eligible pool for the {timeline_year} in-game draft"
    elif league_slug == "bowl-fantasy":
        prefix = f"Relegation draft eligible pool for the {timeline_year} in-game draft"
    else:
        prefix = f"Draft eligible pool for the {timeline_year} in-game draft"
    return (
        f"{prefix}: players must be at least {config.min_age_years} by "
        f"{config.min_anchor_month:02d}/{config.min_anchor_day:02d}, {timeline_year}, and not older "
        f"than {config.max_age_years} by {config.max_anchor_month:02d}/{config.max_anchor_day:02d}, "
        f"{timeline_year}."
    )

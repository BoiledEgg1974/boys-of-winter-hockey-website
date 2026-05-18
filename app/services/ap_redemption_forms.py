"""Per-catalog-item GM input fields for AP redemptions (cap / historical group)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team


@dataclass(frozen=True)
class RedemptionFormField:
    name: str
    label: str
    field_type: str  # text, select, checkbox_group
    required: bool = True
    options: tuple[tuple[str, str], ...] = ()
    help_text: str = ""


STAFF_POSITION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("head_coach", "Head Coach"),
    ("assistant_coach", "Assistant Coach"),
    ("scout", "Scout"),
    ("trainer", "Trainer"),
)

POSITION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("goalie", "Goalie"),
    ("left_defense", "Left Defense"),
    ("right_defense", "Right Defense"),
    ("left_wing", "Left Wing"),
    ("center", "Center"),
    ("right_wing", "Right Wing"),
)

MARKET_FAN_MEDIA_OPTIONS: tuple[tuple[str, str], ...] = (
    ("market", "Market"),
    ("fan", "Fan"),
    ("media", "Media"),
)

COACH_ROLE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("gm", "GM"),
    ("coach", "Coach"),
)

# Longest / most specific title matches first.
_TITLE_FORM_KEY_RULES: tuple[tuple[str, str], ...] = (
    ("purchase a gold boost", "gold_draft_boost"),
    ("purchase a silver boost", "silver_draft_boost"),
    ("re-allocate 1 point", "reallocate_attribute"),
    ("add 2 points to a position", "add_position_points"),
    ("add 2 points to coach", "coach_attribute_points"),
    ("change injury proneness", "injury_proneness"),
    ("market / fan / media", "market_fan_media"),
    ("supplemental staff", "supplemental_staff"),
    ("retire a number", "retire_number"),
    ("change a rival", "change_rival"),
)

_FORM_FIELDS: dict[str, tuple[RedemptionFormField, ...]] = {
    "change_rival": (
        RedemptionFormField("rival_team_id", "Rival team", "select", True),
    ),
    "retire_number": (
        RedemptionFormField("player_name", "Player name", "text", True),
        RedemptionFormField("jersey_number", "Jersey number to retire", "text", True),
    ),
    "supplemental_staff": (
        RedemptionFormField("staff_name", "Staff name", "text", True),
        RedemptionFormField(
            "staff_position",
            "Staff position",
            "select",
            True,
            options=STAFF_POSITION_OPTIONS,
        ),
    ),
    "market_fan_media": (
        RedemptionFormField(
            "choices",
            "Choose one or more",
            "checkbox_group",
            True,
            options=MARKET_FAN_MEDIA_OPTIONS,
            help_text="Select Market, Fan, and/or Media.",
        ),
    ),
    "injury_proneness": (
        RedemptionFormField(
            "body_part",
            "Body part (if specific)",
            "text",
            False,
            help_text="Leave blank if using General below.",
        ),
        RedemptionFormField(
            "general",
            "General (all-over proneness)",
            "checkbox_group",
            False,
            options=(("general", "General"),),
        ),
    ),
    "reallocate_attribute": (
        RedemptionFormField("from_attribute", "FROM (attribute)", "text", True),
        RedemptionFormField("to_attribute", "TO (attribute)", "text", True),
    ),
    "add_position_points": (
        RedemptionFormField(
            "position",
            "Position",
            "select",
            True,
            options=POSITION_OPTIONS,
        ),
    ),
    "coach_attribute_points": (
        RedemptionFormField(
            "coach_roles",
            "Apply to",
            "checkbox_group",
            True,
            options=COACH_ROLE_OPTIONS,
            help_text="Select GM and/or Coach.",
        ),
        RedemptionFormField("attribute", "Attribute", "text", True),
    ),
    "silver_draft_boost": (
        RedemptionFormField("player_name", "Draftee (player name)", "text", True),
    ),
    "gold_draft_boost": (
        RedemptionFormField("player_name", "Draftee (player name)", "text", True),
    ),
}


def catalog_item_form_key(title: str) -> str | None:
    t = str(title or "").strip().lower()
    if not t:
        return None
    for needle, key in _TITLE_FORM_KEY_RULES:
        if needle in t:
            return key
    return None


def form_fields_for_key(form_key: str | None) -> tuple[RedemptionFormField, ...]:
    if not form_key:
        return ()
    return _FORM_FIELDS.get(form_key, ())


def team_select_options(session: Session) -> list[tuple[str, str]]:
    teams = list(session.scalars(select(Team).order_by(Team.name)).all())
    return [(str(t.id), t.full_display_name()) for t in teams]


def _clean_text(val: object, *, max_len: int = 200) -> str:
    return str(val or "").strip()[:max_len]


def _selected_checkbox_values(raw: dict[str, Any], field_name: str) -> list[str]:
    key = field_name
    vals = raw.get(key)
    if isinstance(vals, list):
        return [str(v).strip() for v in vals if str(v).strip()]
    if vals is not None and str(vals).strip():
        return [str(vals).strip()]
    # Also accept detail_<id>_choices_market style flattened keys
    prefix = f"{field_name}_"
    out: list[str] = []
    for k, v in raw.items():
        if str(k).startswith(prefix) and v:
            out.append(str(k)[len(prefix) :])
    return out


def parse_catalog_item_details(
    form_key: str,
    raw: dict[str, Any],
    *,
    session: Session,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate POST fields for one catalog line; return (details dict, error message)."""
    fields = form_fields_for_key(form_key)
    if not fields:
        return {}, None

    details: dict[str, Any] = {"form_key": form_key}
    if form_key == "change_rival":
        tid_raw = raw.get("rival_team_id")
        try:
            tid = int(tid_raw)
        except (TypeError, ValueError):
            return None, "Choose a rival team."
        team = session.get(Team, tid)
        if team is None:
            return None, "Choose a valid rival team."
        details["rival_team_id"] = tid
        details["rival_team_name"] = team.full_display_name()
        return details, None

    if form_key == "retire_number":
        player = _clean_text(raw.get("player_name"))
        number = _clean_text(raw.get("jersey_number"), max_len=8)
        if not player:
            return None, "Enter the player name."
        if not number:
            return None, "Enter the jersey number to retire."
        details["player_name"] = player
        details["jersey_number"] = number
        return details, None

    if form_key == "supplemental_staff":
        name = _clean_text(raw.get("staff_name"))
        pos = _clean_text(raw.get("staff_position"), max_len=40)
        valid_pos = {k for k, _ in STAFF_POSITION_OPTIONS}
        if not name:
            return None, "Enter the staff name."
        if pos not in valid_pos:
            return None, "Choose a staff position."
        details["staff_name"] = name
        details["staff_position"] = pos
        details["staff_position_label"] = dict(STAFF_POSITION_OPTIONS).get(pos, pos)
        return details, None

    if form_key == "market_fan_media":
        picked = _selected_checkbox_values(raw, "choices")
        valid = {k for k, _ in MARKET_FAN_MEDIA_OPTIONS}
        picked = [p for p in picked if p in valid]
        if not picked:
            return None, "Select at least one of Market, Fan, or Media."
        labels = [dict(MARKET_FAN_MEDIA_OPTIONS)[p] for p in picked]
        details["choices"] = picked
        details["choice_labels"] = labels
        return details, None

    if form_key == "injury_proneness":
        body_part = _clean_text(raw.get("body_part"))
        general = "general" in _selected_checkbox_values(raw, "general")
        if general and body_part:
            return None, "Choose either a body part or General, not both."
        if not general and not body_part:
            return None, "Enter a body part or select General."
        if general:
            details["scope"] = "general"
        else:
            details["scope"] = "body_part"
            details["body_part"] = body_part
        return details, None

    if form_key == "reallocate_attribute":
        frm = _clean_text(raw.get("from_attribute"))
        to = _clean_text(raw.get("to_attribute"))
        if not frm or not to:
            return None, "Enter both FROM and TO attributes."
        details["from_attribute"] = frm
        details["to_attribute"] = to
        return details, None

    if form_key == "add_position_points":
        pos = _clean_text(raw.get("position"), max_len=40)
        valid = {k for k, _ in POSITION_OPTIONS}
        if pos not in valid:
            return None, "Choose a position."
        details["position"] = pos
        details["position_label"] = dict(POSITION_OPTIONS)[pos]
        return details, None

    if form_key == "coach_attribute_points":
        roles = _selected_checkbox_values(raw, "coach_roles")
        valid_roles = {k for k, _ in COACH_ROLE_OPTIONS}
        roles = [r for r in roles if r in valid_roles]
        attr = _clean_text(raw.get("attribute"))
        if not roles:
            return None, "Select GM and/or Coach."
        if not attr:
            return None, "Enter the attribute name."
        details["coach_roles"] = roles
        details["coach_role_labels"] = [dict(COACH_ROLE_OPTIONS)[r] for r in roles]
        details["attribute"] = attr
        return details, None

    if form_key in ("silver_draft_boost", "gold_draft_boost"):
        player = _clean_text(raw.get("player_name"))
        if not player:
            return None, "Enter the draftee player name."
        details["player_name"] = player
        return details, None

    return {}, None


def format_details_summary(details: dict[str, Any] | None) -> str:
    if not details:
        return ""
    fk = str(details.get("form_key") or "")
    if fk == "change_rival":
        return f"Rival: {details.get('rival_team_name', '')}"
    if fk == "retire_number":
        return f"#{details.get('jersey_number', '')} for {details.get('player_name', '')}"
    if fk == "supplemental_staff":
        return f"{details.get('staff_name', '')} ({details.get('staff_position_label', '')})"
    if fk == "market_fan_media":
        labels = details.get("choice_labels") or details.get("choices") or []
        return "Market/Fan/Media: " + ", ".join(str(x) for x in labels)
    if fk == "injury_proneness":
        if details.get("scope") == "general":
            return "Injury proneness: General"
        return f"Injury proneness: {details.get('body_part', '')}"
    if fk == "reallocate_attribute":
        return f"Reallocate: {details.get('from_attribute', '')} → {details.get('to_attribute', '')}"
    if fk == "add_position_points":
        return f"+2 position: {details.get('position_label', details.get('position', ''))}"
    if fk == "coach_attribute_points":
        roles = ", ".join(str(x) for x in (details.get("coach_role_labels") or []))
        return f"+2 coach attr ({roles}): {details.get('attribute', '')}"
    if fk in ("silver_draft_boost", "gold_draft_boost"):
        return f"Draftee: {details.get('player_name', '')}"
    return ""


def extract_raw_details_for_catalog_id(form, catalog_id: int) -> dict[str, Any]:
    """Read ``detail_<catalog_id>_<field>`` keys from a Flask request form."""
    prefix = f"detail_{int(catalog_id)}_"
    raw: dict[str, Any] = {}
    for key in form:
        if not str(key).startswith(prefix):
            continue
        field = str(key)[len(prefix) :]
        if not field:
            continue
        values = form.getlist(key)
        if field in ("choices", "general", "coach_roles"):
            raw[field] = [v for v in values if str(v).strip()]
        elif len(values) > 1:
            raw[field] = values
        else:
            raw[field] = form.get(key)
    return raw


def line_item_display_title(title: str, details: dict[str, Any] | None) -> str:
    summary = format_details_summary(details)
    if summary:
        return f"{title} — {summary}"
    return title

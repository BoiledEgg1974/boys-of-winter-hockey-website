"""Repair FHM Windows-1252 names already stored with the wrong code page."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value

from app.models import Player, Team
from scripts.import_pipeline.encoding_utils import clean_fhm_text

_PLAYER_TEXT_FIELDS = (
    "first_name",
    "last_name",
    "full_name",
    "nick_name",
    "birth_city",
    "birth_state",
    "nationality",
)
_TEAM_TEXT_FIELDS = ("name", "city", "nickname")


def _repair_loaded_fields(target: object, fields: tuple[str, ...]) -> None:
    for attr in fields:
        val = getattr(target, attr, None)
        if not val:
            continue
        fixed = clean_fhm_text(val)
        if fixed is not None and fixed != val:
            set_committed_value(target, attr, fixed)


@event.listens_for(Player, "load")
def _repair_player_fhm_text(target: Player, _context) -> None:
    _repair_loaded_fields(target, _PLAYER_TEXT_FIELDS)


@event.listens_for(Team, "load")
def _repair_team_fhm_text(target: Team, _context) -> None:
    _repair_loaded_fields(target, _TEAM_TEXT_FIELDS)

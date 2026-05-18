"""Link team, player, and staff names in news article bodies to site pages."""
from __future__ import annotations

from dataclasses import dataclass
from flask import url_for
from markupsafe import Markup, escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.staff_catalog import _load_catalog


@dataclass(frozen=True)
class _LinkPhrase:
    phrase: str
    href: str
    priority: int


_phrase_cache: dict[tuple[object, ...], tuple[_LinkPhrase, ...]] = {}


def _phrase_cache_token(session: Session) -> tuple[object, ...]:
    from flask import current_app

    slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    team_n = int(session.scalar(select(func.count(Team.id))) or 0)
    player_n = int(session.scalar(select(func.count(Player.id))) or 0)
    cat = _load_catalog()
    return (slug, team_n, player_n, len(cat))


def _add_phrase(
    bucket: dict[str, _LinkPhrase],
    phrase: str,
    href: str,
    *,
    priority: int,
) -> None:
    text = str(phrase or "").strip()
    if len(text) < 3:
        return
    key = text.lower()
    existing = bucket.get(key)
    if existing is None or (len(text), priority) > (len(existing.phrase), existing.priority):
        bucket[key] = _LinkPhrase(phrase=text, href=href, priority=priority)


def _team_phrases(team: Team, href: str, bucket: dict[str, _LinkPhrase]) -> None:
    _add_phrase(bucket, team.full_display_name(), href, priority=30)
    name = str(team.name or "").strip()
    nick = str(team.nickname or "").strip()
    if name:
        _add_phrase(bucket, name, href, priority=20)
    if nick and nick.lower() not in name.lower():
        _add_phrase(bucket, nick, href, priority=15)
    abbr = str(team.abbreviation or "").strip()
    if len(abbr) >= 3:
        _add_phrase(bucket, abbr, href, priority=5)


def _player_phrases(player: Player, href: str, bucket: dict[str, _LinkPhrase]) -> None:
    full = str(player.full_name or "").strip()
    if len(full) >= 4:
        _add_phrase(bucket, full, href, priority=25)
    nick = str(player.nick_name or "").strip()
    if nick and len(nick) >= 3 and nick.lower() not in full.lower():
        _add_phrase(bucket, nick, href, priority=12)
    first = str(player.first_name or "").strip()
    last = str(player.last_name or "").strip()
    if first and last:
        plain = f"{first} {last}".strip()
        if plain.lower() != full.lower() and len(plain) >= 5:
            _add_phrase(bucket, plain, href, priority=18)


def _staff_phrases(staff: dict, href: str, bucket: dict[str, _LinkPhrase]) -> None:
    full = str(staff.get("full_name") or "").strip()
    if len(full) >= 4:
        _add_phrase(bucket, full, href, priority=22)
    if '"' in full:
        plain = full.replace('"', "").strip()
        if plain.lower() != full.lower():
            _add_phrase(bucket, plain, href, priority=18)


def build_news_entity_link_phrases(session: Session) -> tuple[_LinkPhrase, ...]:
    """Sorted longest-first phrases for the active league mount."""
    bucket: dict[str, _LinkPhrase] = {}
    for team in session.scalars(select(Team)).all():
        href = url_for("main.team_page", slug=team.slug)
        _team_phrases(team, href, bucket)
    for player in session.scalars(select(Player).where(Player.retired.is_(False))).all():
        href = url_for("main.player_page", player_id=int(player.id))
        _player_phrases(player, href, bucket)
    for staff in _load_catalog().values():
        sid = str(staff.get("staff_fhm_id") or "").strip()
        if not sid:
            continue
        href = url_for("site_gm.staff_profile_page", staff_fhm_id=sid)
        _staff_phrases(staff, href, bucket)
    phrases = list(bucket.values())
    phrases.sort(key=lambda p: (-len(p.phrase), -p.priority, p.phrase.lower()))
    return tuple(phrases)


def _get_phrases(session: Session) -> tuple[_LinkPhrase, ...]:
    key = _phrase_cache_token(session)
    cached = _phrase_cache.get(key)
    if cached is None:
        cached = build_news_entity_link_phrases(session)
        _phrase_cache[key] = cached
    return cached


def _has_word_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        return False
    if end < len(text) and (text[end].isalnum() or text[end] == "_"):
        return False
    return True


def linkify_plain_text(text: str, phrases: tuple[_LinkPhrase, ...]) -> str:
    """Return HTML with ``<a>`` tags; input is plain text (newlines preserved)."""
    if not text or not phrases:
        return str(escape(text))
    original = str(text)
    n = len(original)
    i = 0
    parts: list[str] = []
    while i < n:
        matched: _LinkPhrase | None = None
        for lp in phrases:
            plen = len(lp.phrase)
            if plen < 3 or i + plen > n:
                continue
            if original[i : i + plen].lower() != lp.phrase.lower():
                continue
            if not _has_word_boundary(original, i, i + plen):
                continue
            matched = lp
            break
        if matched is not None:
            plen = len(matched.phrase)
            label = escape(original[i : i + plen])
            parts.append(f'<a href="{escape(matched.href)}">{label}</a>')
            i += plen
        else:
            parts.append(escape(original[i]))
            i += 1
    return "".join(parts)


def linkify_news_body(session: Session, body: str | None) -> Markup:
    """Link mentions of teams, players, and staff in article body text."""
    text = str(body or "")
    if not text.strip():
        return Markup("")
    phrases = _get_phrases(session)
    return Markup(linkify_plain_text(text, phrases))


def clear_news_entity_link_cache() -> None:
    _phrase_cache.clear()

"""Site DB schema tweaks and default commissioner account."""
from __future__ import annotations

import os

from sqlalchemy import func, or_, select, text
from werkzeug.security import generate_password_hash

from app.league_db import db
from app.site_models import GmLeagueMembership, User

_COMMISH_USERNAME = "Commish"
_COMMISH_EMAIL = "keenovdecimanus@gmail.com"
_COMMISH_EMAIL_LEGACY = "commish@bowl-league.site"
_COMMISH_DISCORD = "BoiledEgg"


def ensure_news_articles_category_column(app) -> None:
    """Add ``category`` to ``news_articles`` when upgrading an existing site DB."""
    engine = db.get_engine(app, bind="site")
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(news_articles)")).fetchall()
        colnames = {row[1] for row in rows}
        if "category" not in colnames:
            conn.execute(
                text(
                    "ALTER TABLE news_articles ADD COLUMN category VARCHAR(32) "
                    "NOT NULL DEFAULT 'general_messages'"
                )
            )


def ensure_news_articles_image_rel_path_column(app) -> None:
    """Add ``image_rel_path`` for optional headline images."""
    engine = db.get_engine(app, bind="site")
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(news_articles)")).fetchall()
        colnames = {row[1] for row in rows}
        if "image_rel_path" not in colnames:
            conn.execute(text("ALTER TABLE news_articles ADD COLUMN image_rel_path VARCHAR(384)"))


def ensure_site_users_username_column(app) -> None:
    """Add ``username`` to ``site_users`` when upgrading an existing SQLite file."""
    engine = db.get_engine(app, bind="site")
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(site_users)")).fetchall()
        colnames = {row[1] for row in rows}
        if "username" not in colnames:
            conn.execute(text("ALTER TABLE site_users ADD COLUMN username VARCHAR(64)"))
        idx_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_site_users_username'")
        ).fetchall()
        if not idx_rows:
            conn.execute(text("CREATE UNIQUE INDEX ix_site_users_username ON site_users(username)"))


def ensure_gm_league_memberships_fhm_team_id_column(app) -> None:
    """Add ``fhm_team_id`` + index on ``gm_league_memberships`` for franchise-stable routing."""
    engine = db.get_engine(app, bind="site")
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(gm_league_memberships)")).fetchall()
        colnames = {row[1] for row in rows}
        if "fhm_team_id" not in colnames:
            conn.execute(text("ALTER TABLE gm_league_memberships ADD COLUMN fhm_team_id VARCHAR(64)"))
        idx_rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_gm_league_fhm_team'"
            )
        ).fetchall()
        if not idx_rows:
            conn.execute(
                text(
                    "CREATE INDEX ix_gm_league_fhm_team ON gm_league_memberships(league_slug, fhm_team_id)"
                )
            )


def backfill_gm_membership_fhm_team_ids(app) -> None:
    """Copy ``teams.fhm_team_id`` from each league DB into site membership rows (NULL/blank only)."""
    from app.services.register_team_options import fhm_team_id_for_league_team

    mems = db.session.scalars(
        select(GmLeagueMembership).where(
            or_(GmLeagueMembership.fhm_team_id.is_(None), GmLeagueMembership.fhm_team_id == "")
        )
    ).all()
    changed = False
    for m in mems:
        fhm = fhm_team_id_for_league_team(m.league_slug, int(m.team_id))
        if fhm:
            m.fhm_team_id = fhm
            changed = True
    if changed:
        db.session.commit()


def ensure_commish_admin(app) -> None:
    """Create or update commissioner admin. Login: Commish (username) or keenovdecimanus@gmail.com."""
    ensure_site_users_username_column(app)
    ensure_news_articles_category_column(app)
    ensure_news_articles_image_rel_path_column(app)
    ensure_gm_league_memberships_fhm_team_id_column(app)
    try:
        backfill_gm_membership_fhm_team_ids(app)
    except Exception as exc:
        app.logger.warning("GM membership FHM id backfill skipped: %s", exc)
    pw = str(
        app.config.get("COMMISH_ADMIN_PASSWORD")
        or os.environ.get("COMMISH_ADMIN_PASSWORD", "Claudette81!")
    )

    u = db.session.scalar(
        select(User).where(func.lower(User.username) == func.lower(_COMMISH_USERNAME)).limit(1)
    )
    if u is None:
        u = db.session.scalar(
            select(User).where(func.lower(User.email) == func.lower(_COMMISH_EMAIL_LEGACY)).limit(1)
        )

    if u is None:
        taken = db.session.scalar(
            select(User.id).where(func.lower(User.email) == func.lower(_COMMISH_EMAIL)).limit(1)
        )
        if taken is not None:
            return
        db.session.add(
            User(
                email=_COMMISH_EMAIL,
                username=_COMMISH_USERNAME,
                password_hash=generate_password_hash(pw),
                discord_name=_COMMISH_DISCORD,
                is_admin=True,
            )
        )
        db.session.commit()
        return

    changed = False
    if not u.is_admin:
        u.is_admin = True
        changed = True
    if getattr(u, "username", None) in (None, ""):
        u.username = _COMMISH_USERNAME
        changed = True
    if (u.username or "").lower() == _COMMISH_USERNAME.lower() or (
        u.email or ""
    ).lower() == _COMMISH_EMAIL_LEGACY.lower():
        if u.discord_name != _COMMISH_DISCORD:
            u.discord_name = _COMMISH_DISCORD
            changed = True
        if (u.email or "").lower() != _COMMISH_EMAIL.lower():
            other = db.session.scalar(
                select(User.id).where(
                    func.lower(User.email) == func.lower(_COMMISH_EMAIL),
                    User.id != u.id,
                ).limit(1)
            )
            if other is None:
                u.email = _COMMISH_EMAIL
                changed = True
    if changed:
        db.session.commit()

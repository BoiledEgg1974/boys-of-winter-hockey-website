"""Site DB schema tweaks and default commissioner account."""
from __future__ import annotations

import os

from sqlalchemy import func, select, text
from werkzeug.security import generate_password_hash

from app.league_db import db
from app.site_models import User

_COMMISH_USERNAME = "Commish"
_COMMISH_EMAIL = "keenovdecimanus@gmail.com"
_COMMISH_EMAIL_LEGACY = "commish@bowl-league.site"
_COMMISH_DISCORD = "BoiledEgg"


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


def ensure_commish_admin(app) -> None:
    """Create or update commissioner admin. Login: Commish (username) or keenovdecimanus@gmail.com."""
    ensure_site_users_username_column(app)
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

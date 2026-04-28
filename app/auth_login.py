"""Flask-Login: one LoginManager factory per Flask app instance (hub vs league mounts)."""
from __future__ import annotations

from urllib.parse import quote

from flask import redirect, request
from flask_login import LoginManager, current_user

from app.league_db import db
from app.site_models import User

ADMIN_ROLE_SUPER = "super_admin"
ADMIN_ROLE_LEAGUE = "league_admin"
ADMIN_ROLE_CONTENT = "content_admin"
ADMIN_ROLE_STATS = "stats_admin"
ADMIN_ROLE_READONLY = "staff_readonly"

ADMIN_ROLE_VALUES = {
    ADMIN_ROLE_SUPER,
    ADMIN_ROLE_LEAGUE,
    ADMIN_ROLE_CONTENT,
    ADMIN_ROLE_STATS,
    ADMIN_ROLE_READONLY,
}


def load_site_user(user_id: str) -> User | None:
    if not user_id or not str(user_id).isdigit():
        return None
    u = db.session.get(User, int(user_id))
    if u is None or u.revoked_at is not None:
        return None
    return u


def create_login_manager() -> LoginManager:
    lm = LoginManager()
    lm.user_loader(load_site_user)
    lm.login_message = "Please log in to continue."

    @lm.unauthorized_handler
    def _unauth():
        # Hub mounts at / ; league at /<slug>/ — redirect to hub login with return URL
        next_url = request.url
        return redirect("/login?next=" + quote(next_url, safe=""))

    return lm


def active_membership_for_league(user, league_slug: str):
    from sqlalchemy import select

    from app.site_models import GmLeagueMembership

    if not user or not getattr(user, "is_authenticated", False):
        return None
    return db.session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.user_id == user.id,
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
        .limit(1)
    )


def require_admin():
    from flask import abort

    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)


def user_admin_role(user) -> str | None:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    raw = (getattr(user, "admin_role", None) or "").strip().lower()
    if raw in ADMIN_ROLE_VALUES:
        return raw
    if getattr(user, "is_admin", False):
        # Backward-compatible fallback for legacy boolean-only admins.
        return ADMIN_ROLE_SUPER
    return None


def has_admin_role(user, *roles: str) -> bool:
    role = user_admin_role(user)
    if role is None:
        return False
    if not roles:
        return True
    wanted = {str(r or "").strip().lower() for r in roles if str(r or "").strip()}
    if role == ADMIN_ROLE_SUPER:
        return True
    return role in wanted


def require_admin_role(*roles: str):
    from flask import abort

    if not has_admin_role(current_user, *roles):
        abort(403)

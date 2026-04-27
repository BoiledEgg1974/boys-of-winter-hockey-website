"""Registration / login on hub only (path ``/``)."""
from __future__ import annotations

from urllib.parse import unquote

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import LEAGUES
from app.league_db import db
from app.site_models import GmLeagueMembership, User

hub_auth_bp = Blueprint("hub_auth", __name__)


@hub_auth_bp.get("/register")
def register_get():
    from app.services.register_team_options import all_league_team_options

    if current_user.is_authenticated:
        return redirect(url_for("hub_auth.account"))
    return render_template(
        "register.html",
        leagues=LEAGUES,
        errors=[],
        team_options=all_league_team_options(),
        form=None,
    )


@hub_auth_bp.post("/register")
def register_post():
    errors: list[str] = []
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    password2 = request.form.get("password_confirm") or ""
    discord = (request.form.get("discord_name") or "").strip()
    terms = request.form.get("terms") == "1"
    league_slugs = [s for s in request.form.getlist("leagues") if s.strip()]

    if not email or "@" not in email:
        errors.append("A valid email is required.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != password2:
        errors.append("Passwords do not match.")
    if not discord:
        errors.append("Discord name is required.")
    if not terms:
        errors.append("You must accept the membership terms.")
    if not league_slugs:
        errors.append("Select at least one league you GM in.")

    memberships_data: list[tuple[str, int]] = []
    if not errors:
        for slug in league_slugs:
            raw = (request.form.get(f"team_id_{slug}") or "").strip()
            if not raw.isdigit():
                errors.append(f"Select a team for {slug}.")
                break
            tid = int(raw)
            memberships_data.append((slug, tid))

    if not errors:
        existing = db.session.scalar(select(User.id).where(User.email == email).limit(1))
        if existing is not None:
            errors.append("That email is already registered.")

    if errors:
        from app.services.register_team_options import all_league_team_options

        return render_template(
            "register.html",
            leagues=LEAGUES,
            errors=errors,
            form=request.form,
            team_options=all_league_team_options(),
        )

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        discord_name=discord,
        is_admin=False,
    )
    db.session.add(user)
    db.session.flush()
    for slug, tid in memberships_data:
        db.session.add(
            GmLeagueMembership(
                user_id=user.id,
                league_slug=slug,
                team_id=tid,
                status="pending",
                terms_version="v1",
            )
        )
    db.session.commit()
    flash("Account created. Memberships are pending until an administrator approves them.", "ok")
    return redirect(url_for("hub_auth.login"))


@hub_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        nxt = (request.args.get("next") or "").strip()
        if nxt:
            return redirect(unquote(nxt))
        return redirect(url_for("hub_auth.account"))
    if request.method == "GET":
        return render_template("login.html", error=None)
    login_id = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    if "@" in login_id:
        user = db.session.scalar(select(User).where(func.lower(User.email) == login_id.lower()).limit(1))
    else:
        user = db.session.scalar(select(User).where(func.lower(User.username) == login_id.lower()).limit(1))
    if user is None or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="Invalid email/username or password.")
    if user.revoked_at is not None:
        return render_template("login.html", error="This account has been revoked.")
    login_user(user, remember=bool(request.form.get("remember")))
    nxt = (request.args.get("next") or request.form.get("next") or "").strip()
    if nxt:
        return redirect(unquote(nxt))
    return redirect(url_for("hub_auth.account"))


@hub_auth_bp.post("/logout")
def logout():
    logout_user()
    return redirect("/")


@hub_auth_bp.get("/account")
@login_required
def account():
    rows = db.session.scalars(
        select(GmLeagueMembership).where(GmLeagueMembership.user_id == current_user.id)
    ).all()
    active_slugs = {m.league_slug for m in rows if (m.status or "").strip() == "active"}
    league_news_links = [e for e in LEAGUES if e.slug in active_slugs]
    return render_template(
        "account.html",
        memberships=rows,
        leagues=LEAGUES,
        league_news_links=league_news_links,
    )


@hub_auth_bp.get("/admin/memberships")
@login_required
def admin_memberships():
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    rows = db.session.execute(
        select(GmLeagueMembership, User)
        .join(User, User.id == GmLeagueMembership.user_id)
        .order_by(GmLeagueMembership.created_at.desc())
    ).all()
    pairs = [(r[0], r[1]) for r in rows]
    return render_template("admin_memberships.html", rows=pairs)


@hub_auth_bp.post("/admin/memberships/<int:mid>/approve")
@login_required
def admin_approve_membership(mid: int):
    from datetime import datetime

    if not current_user.is_admin:
        from flask import abort

        abort(403)
    m = db.session.get(GmLeagueMembership, mid)
    if m:
        m.status = "active"
        m.approved_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("hub_auth.admin_memberships"))


@hub_auth_bp.post("/admin/memberships/<int:mid>/revoke")
@login_required
def admin_revoke_membership(mid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    m = db.session.get(GmLeagueMembership, mid)
    if m:
        m.status = "revoked"
        db.session.commit()
    return redirect(url_for("hub_auth.admin_memberships"))


@hub_auth_bp.post("/admin/users/<int:uid>/set-admin")
@login_required
def admin_set_user_admin(uid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    u = db.session.get(User, uid)
    if u and u.id != current_user.id:
        u.is_admin = request.form.get("is_admin") == "1"
        db.session.commit()
    return redirect(url_for("hub_auth.admin_memberships"))

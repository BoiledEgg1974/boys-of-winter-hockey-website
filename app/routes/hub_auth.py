"""Registration / login on hub only (path ``/``)."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import unquote

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import LEAGUES
from app.league_db import db
from app.site_models import GmLeagueMembership, SiteBannedIdentity, User

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

    memberships_data: list[tuple[str, int, str | None]] = []
    if not errors:
        from app.services.register_team_options import fhm_team_id_for_league_team

        for slug in league_slugs:
            raw = (request.form.get(f"team_id_{slug}") or "").strip()
            if not raw.isdigit():
                errors.append(f"Select a team for {slug}.")
                break
            tid = int(raw)
            memberships_data.append((slug, tid, fhm_team_id_for_league_team(slug, tid)))

    if not errors:
        banned_id = db.session.scalar(
            select(SiteBannedIdentity.id).where(SiteBannedIdentity.email_norm == email).limit(1)
        )
        if banned_id is not None:
            errors.append("Registration is not allowed for this email address.")
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
    for slug, tid, fhm_tid in memberships_data:
        db.session.add(
            GmLeagueMembership(
                user_id=user.id,
                league_slug=slug,
                team_id=tid,
                fhm_team_id=fhm_tid,
                status="pending",
                terms_version="v1",
            )
        )
    db.session.commit()
    try:
        from app.services.admin_review_notify import notify_membership_registration_pending

        notify_membership_registration_pending(
            user_email=user.email,
            discord_name=user.discord_name or "",
            membership_rows=memberships_data,
        )
    except Exception as exc:
        current_app.logger.warning("Admin notify (membership registration): %s", exc)
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


@hub_auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("hub_auth.account"))
    if request.method == "GET":
        return render_template("forgot_password.html", error=None)

    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return render_template("forgot_password.html", error="Enter a valid email address.")

    user = db.session.scalar(select(User).where(func.lower(User.email) == email).limit(1))
    # Return a generic success message either way so account presence is not exposed.
    if user is not None and user.revoked_at is None:
        try:
            from app.services.password_reset import issue_password_reset_token, send_password_reset_email

            raw_token = issue_password_reset_token(user)
            send_password_reset_email(user=user, raw_token=raw_token)
        except Exception as exc:
            current_app.logger.warning("Password reset email failed for %s: %s", email, exc)

    flash(
        "If that email is registered, a reset link has been sent.",
        "ok",
    )
    return redirect(url_for("hub_auth.login"))


@hub_auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    from app.services.password_reset import consume_reset_token_and_update_password, find_user_for_reset_token

    token = (token or "").strip()
    if not token:
        return redirect(url_for("hub_auth.forgot_password"))

    valid_user = find_user_for_reset_token(token)
    if valid_user is None:
        return render_template("reset_password.html", token=token, invalid=True, error=None)

    if request.method == "GET":
        return render_template("reset_password.html", token=token, invalid=False, error=None)

    password = request.form.get("password") or ""
    password2 = request.form.get("password_confirm") or ""
    if len(password) < 8:
        return render_template(
            "reset_password.html",
            token=token,
            invalid=False,
            error="Password must be at least 8 characters.",
        )
    if password != password2:
        return render_template(
            "reset_password.html",
            token=token,
            invalid=False,
            error="Passwords do not match.",
        )

    if not consume_reset_token_and_update_password(raw_token=token, new_password=password):
        return render_template(
            "reset_password.html",
            token=token,
            invalid=True,
            error="This reset link is invalid or has expired.",
        )
    flash("Password reset complete. You can now log in.", "ok")
    return redirect(url_for("hub_auth.login"))


@hub_auth_bp.post("/logout")
def logout():
    logout_user()
    return redirect("/")


@hub_auth_bp.get("/account")
@login_required
def account():
    from app.services.register_team_options import team_snapshot_for_membership

    rows = db.session.scalars(
        select(GmLeagueMembership).where(GmLeagueMembership.user_id == current_user.id)
    ).all()
    membership_rows = [(m, team_snapshot_for_membership(m.league_slug, m.team_id)) for m in rows]
    active_slugs = {m.league_slug for m in rows if (m.status or "").strip() == "active"}
    league_news_links = [e for e in LEAGUES if e.slug in active_slugs]
    return render_template(
        "account.html",
        memberships=membership_rows,
        leagues=LEAGUES,
        league_news_links=league_news_links,
    )


@hub_auth_bp.get("/admin/memberships")
@login_required
def admin_memberships():
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    from app.services.register_team_options import team_snapshot_for_membership

    rows = db.session.execute(
        select(GmLeagueMembership, User)
        .join(User, User.id == GmLeagueMembership.user_id)
        .order_by(GmLeagueMembership.created_at.desc())
    ).all()
    enriched = [
        (r[0], r[1], team_snapshot_for_membership(r[0].league_slug, r[0].team_id)) for r in rows
    ]
    return render_template("admin_memberships.html", rows=enriched)


@hub_auth_bp.get("/admin/memberships/<int:mid>/remove")
@login_required
def admin_remove_membership_confirm(mid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    from app.services.register_team_options import team_snapshot_for_membership

    m = db.session.get(GmLeagueMembership, mid)
    if m is None:
        flash("That membership row no longer exists.", "error")
        return redirect(url_for("hub_auth.admin_memberships"))
    u = db.session.get(User, m.user_id)
    if u is None:
        db.session.delete(m)
        db.session.commit()
        flash("Removed orphan membership row.", "ok")
        return redirect(url_for("hub_auth.admin_memberships"))
    snap = team_snapshot_for_membership(m.league_slug, m.team_id)
    return render_template(
        "admin_memberships_remove.html",
        membership=m,
        user_row=u,
        team_snap=snap,
    )


@hub_auth_bp.post("/admin/memberships/<int:mid>/remove-only")
@login_required
def admin_remove_membership_only(mid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    m = db.session.get(GmLeagueMembership, mid)
    if m:
        db.session.delete(m)
        db.session.commit()
        flash("Membership removed from the dashboard (user account unchanged).", "ok")
    return redirect(url_for("hub_auth.admin_memberships"))


@hub_auth_bp.post("/admin/memberships/<int:mid>/ban")
@login_required
def admin_ban_membership(mid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    m = db.session.get(GmLeagueMembership, mid)
    if not m:
        return redirect(url_for("hub_auth.admin_memberships"))
    u = db.session.get(User, m.user_id)
    if u is None:
        db.session.delete(m)
        db.session.commit()
        flash("Removed orphan membership row.", "ok")
        return redirect(url_for("hub_auth.admin_memberships"))
    if u.id == current_user.id:
        flash("You cannot ban your own account.", "error")
        return redirect(url_for("hub_auth.admin_memberships"))
    note = (request.form.get("note") or "").strip()[:4000]
    email_norm = (u.email or "").strip().lower()
    league_slug = (m.league_slug or "").strip()
    existing = db.session.scalar(
        select(SiteBannedIdentity).where(SiteBannedIdentity.email_norm == email_norm).limit(1)
    )
    if existing is None:
        db.session.add(
            SiteBannedIdentity(
                email_norm=email_norm,
                discord_name=(u.discord_name or "")[:120],
                note=note,
                league_slug=league_slug,
                created_by_user_id=int(current_user.id),
            )
        )
    else:
        if note:
            prev = (existing.note or "").strip()
            existing.note = (prev + "\n" + note).strip()[:4000] if prev else note
    db.session.delete(m)
    u.revoked_at = datetime.utcnow()
    u.is_admin = False
    db.session.commit()
    flash("User archived to the ban list, this membership removed, and site login revoked.", "ok")
    return redirect(url_for("hub_auth.admin_memberships"))


@hub_auth_bp.get("/admin/banned")
@login_required
def admin_banned_list():
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    rows = db.session.scalars(select(SiteBannedIdentity).order_by(SiteBannedIdentity.created_at.desc())).all()
    return render_template("admin_banned.html", rows=rows)


@hub_auth_bp.post("/admin/banned/<int:bid>/lift")
@login_required
def admin_banned_lift(bid: int):
    if not current_user.is_admin:
        from flask import abort

        abort(403)
    row = db.session.get(SiteBannedIdentity, bid)
    if not row:
        return redirect(url_for("hub_auth.admin_banned_list"))
    email_norm = row.email_norm
    db.session.delete(row)
    u = db.session.scalar(select(User).where(func.lower(User.email) == email_norm).limit(1))
    if u is not None:
        u.revoked_at = None
    db.session.commit()
    flash("Ban record removed; if a matching user exists, site login is allowed again.", "ok")
    return redirect(url_for("hub_auth.admin_banned_list"))


@hub_auth_bp.post("/admin/memberships/<int:mid>/approve")
@login_required
def admin_approve_membership(mid: int):
    from datetime import datetime

    if not current_user.is_admin:
        from flask import abort

        abort(403)
    m = db.session.get(GmLeagueMembership, mid)
    if m:
        from app.services.register_team_options import fhm_team_id_for_league_team

        m.status = "active"
        m.approved_at = datetime.utcnow()
        fhm = fhm_team_id_for_league_team(m.league_slug, int(m.team_id))
        if fhm:
            m.fhm_team_id = fhm
        db.session.commit()
    return redirect(url_for("hub_auth.admin_memberships"))


@hub_auth_bp.post("/admin/memberships/<int:mid>/revoke")
@login_required
def admin_revoke_membership(mid: int):
    """Legacy: mark membership revoked without deleting the row. Prefer remove / ban flows."""
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

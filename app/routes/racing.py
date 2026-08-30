"""Public + admin routes for Formula BOWL / Demolition BOWL mounts."""
from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select
from werkzeug.utils import secure_filename

from app.auth_login import (
    ADMIN_ROLE_CONTENT,
    ADMIN_ROLE_LEAGUE,
    ADMIN_ROLE_STATS,
    ADMIN_ROLE_SUPER,
    require_admin_role,
)
from app.league_db import db
from app.racing_models import (
    RacingChannelCredit,
    RacingCircuit,
    RacingCircuitStanding,
    RacingEvent,
    RacingEventResult,
    RacingImportBatch,
    RacingNameAlias,
    RacingRacer,
)
from app.services.racing_ap import (
    dismiss_suggestion_batch,
    grant_suggestion_batch,
    pending_suggestions,
    suggestion_event_label,
)
from app.services.racing_discord import enqueue_after_import
from app.services.racing_import import import_all_from_raw_dir, import_csv_file
from app.services.racing_tracks import track_image_url
from app.services.racing_racers import (
    add_manual_alias,
    default_roster_txt_path,
    delete_racer,
    link_roster_txt,
    list_racers,
    set_racer_ap_target,
    sync_racers_from_cap,
)
from app.sqlite_retry import commit_with_sqlite_retry

racing_bp = Blueprint("racing", __name__)


def _slug() -> str:
    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _is_demolition() -> bool:
    return _slug() == "bowl-demolition"


def _active_circuit() -> RacingCircuit | None:
    return db.session.scalar(
        select(RacingCircuit)
        .where(RacingCircuit.status == "active")
        .order_by(RacingCircuit.id.desc())
        .limit(1)
    )


@racing_bp.route("/")
def home():
    circuit = _active_circuit()
    standings: list[RacingCircuitStanding] = []
    latest_event: RacingEvent | None = None
    latest_results: list[RacingEventResult] = []
    if circuit is not None:
        standings = list(
            db.session.scalars(
                select(RacingCircuitStanding)
                .where(RacingCircuitStanding.circuit_id == int(circuit.id))
                .order_by(RacingCircuitStanding.rank.asc(), RacingCircuitStanding.points.desc())
                .limit(10)
            ).all()
        )
        latest_event = db.session.scalar(
            select(RacingEvent)
            .where(RacingEvent.circuit_id == int(circuit.id))
            .order_by(RacingEvent.event_number.desc())
            .limit(1)
        )
        if latest_event is not None:
            latest_results = list(
                db.session.scalars(
                    select(RacingEventResult)
                    .where(RacingEventResult.event_id == int(latest_event.id))
                    .order_by(RacingEventResult.position.asc())
                    .limit(10)
                ).all()
            )
    return render_template(
        "racing/home.html",
        circuit=circuit,
        standings=standings,
        latest_event=latest_event,
        latest_results=latest_results,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/results")
def results_index():
    circuit = _active_circuit()
    events: list[RacingEvent] = []
    if circuit is not None:
        events = list(
            db.session.scalars(
                select(RacingEvent)
                .where(RacingEvent.circuit_id == int(circuit.id))
                .order_by(RacingEvent.event_number.desc())
            ).all()
        )
    return render_template(
        "racing/results_index.html",
        circuit=circuit,
        events=events,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/results/<int:event_id>")
def results_detail(event_id: int):
    event = db.session.get(RacingEvent, int(event_id))
    if event is None:
        flash("Event not found.", "error")
        return redirect(url_for("racing.results_index"))
    rows = list(
        db.session.scalars(
            select(RacingEventResult)
            .where(RacingEventResult.event_id == int(event_id))
            .order_by(RacingEventResult.position.asc())
        ).all()
    )
    return render_template(
        "racing/results_detail.html",
        event=event,
        rows=rows,
        is_demolition=_is_demolition(),
        track_image_url=track_image_url(_slug(), event.track_name),
    )


@racing_bp.route("/circuit")
def circuit_page():
    circuit = _active_circuit()
    standings: list[RacingCircuitStanding] = []
    if circuit is not None:
        standings = list(
            db.session.scalars(
                select(RacingCircuitStanding)
                .where(RacingCircuitStanding.circuit_id == int(circuit.id))
                .order_by(RacingCircuitStanding.rank.asc(), RacingCircuitStanding.points.desc())
            ).all()
        )
    return render_template(
        "racing/circuit.html",
        circuit=circuit,
        standings=standings,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/racers")
def racers_page():
    racers = list_racers(db.session, active_only=False)
    return render_template(
        "racing/racers.html",
        racers=racers,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/ledger")
def ledger_page():
    credits = list(
        db.session.scalars(
            select(RacingChannelCredit).order_by(
                RacingChannelCredit.channel_credits.desc(),
                RacingChannelCredit.display.asc(),
            )
        ).all()
    )
    return render_template(
        "racing/ledger.html",
        credits=credits,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/admin/racing")
@login_required
def admin_home():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS, ADMIN_ROLE_CONTENT)
    pending = pending_suggestions(db.session)
    batches = list(
        db.session.scalars(
            select(RacingImportBatch).order_by(RacingImportBatch.imported_at.desc()).limit(20)
        ).all()
    )
    return render_template(
        "racing/admin_home.html",
        pending_count=len(pending),
        batches=batches,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/admin/racing/import", methods=["GET", "POST"])
@login_required
def admin_import():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS)
    raw_dir = Path(current_app.config["RAW_IMPORT_DIR"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    if request.method == "POST":
        action = (request.form.get("action") or "upload").strip()
        try:
            if action == "scan":
                results = import_all_from_raw_dir(db.session, league_slug=_slug())
            else:
                files = request.files.getlist("csv_files")
                for f in files:
                    if not f or not f.filename:
                        continue
                    name = secure_filename(f.filename)
                    lower = name.lower()
                    if lower == "roster.txt" or (lower.endswith(".txt") and "roster" in lower):
                        f.save(raw_dir / "roster.txt")
                        continue
                    if not lower.endswith(".csv"):
                        continue
                    f.save(raw_dir / name)
                # Full raw-dir import links roster.txt first, then all CSVs.
                results = import_all_from_raw_dir(db.session, league_slug=_slug())
            commit_with_sqlite_retry(db.session)
            enqueue_after_import(db.session, league_slug=_slug(), import_results=results)
            commit_with_sqlite_retry(db.session)
            roster_bits = [r for r in results if r.get("kind") == "roster"]
            csv_bits = [r for r in results if r.get("kind") != "roster"]
            flash(
                f"Import complete ({len(csv_bits)} CSV file(s)"
                + (f", roster linked" if roster_bits else "")
                + ").",
                "success",
            )
        except Exception as exc:
            db.session.rollback()
            flash(f"Import failed: {exc}", "error")
        return redirect(url_for("racing.admin_import"))
    existing = sorted([p.name for p in raw_dir.glob("*.csv")])
    if (raw_dir / "roster.txt").is_file():
        existing = ["roster.txt"] + existing
    return render_template(
        "racing/admin_import.html",
        existing_files=existing,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/admin/racing/racers", methods=["GET", "POST"])
@login_required
def admin_racers():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "sync":
                stats = sync_racers_from_cap(db.session, include_historical_only=True)
                commit_with_sqlite_retry(db.session)
                msg = f"Mapped {stats['updated']} racer(s) to GM profiles."
                unmatched = stats.get("unmatched") or []
                if unmatched:
                    msg += " Unmatched: " + ", ".join(str(n) for n in unmatched)
                flash(msg, "success")
            elif action == "link_roster":
                roster_path = default_roster_txt_path(_slug())
                custom = (request.form.get("roster_path") or "").strip().strip('"')
                if custom:
                    roster_path = Path(custom).expanduser()
                create_unmatched = request.form.get("create_unmatched") == "1"
                prune_missing = request.form.get("prune_missing") == "1"
                stats = link_roster_txt(
                    db.session,
                    roster_path,
                    create_unmatched=create_unmatched,
                    prune_missing=prune_missing,
                )
                commit_with_sqlite_retry(db.session)
                msg = (
                    f"Linked roster from {stats['path']} - "
                    f"{stats['entries']} names, {stats['linked']} matched existing, "
                    f"{stats['created']} stubs created, {stats['aliased']} aliases."
                )
                pruned = stats.get("pruned") or []
                if pruned:
                    shown = ", ".join(str(n) for n in pruned[:8])
                    msg += f" Removed leftover stubs: {shown}"
                    if len(pruned) > 8:
                        msg += "..."
                conflicts = stats.get("conflicts") or []
                unmatched = stats.get("unmatched") or []
                if conflicts:
                    shown = ", ".join(conflicts[:8])
                    msg += f" Conflicts (already owned): {shown}"
                    if len(conflicts) > 8:
                        msg += "..."
                if unmatched:
                    shown = ", ".join(unmatched[:8])
                    msg += f" Unmatched left: {shown}"
                    if len(unmatched) > 8:
                        msg += "..."
                flash(msg, "success")
            elif action == "alias":
                add_manual_alias(
                    db.session,
                    int(request.form.get("racer_id") or 0),
                    str(request.form.get("alias") or ""),
                )
                commit_with_sqlite_retry(db.session)
                flash("Alias added.", "success")
            elif action == "remove":
                name = delete_racer(db.session, int(request.form.get("racer_id") or 0))
                commit_with_sqlite_retry(db.session)
                flash(f"Removed {name} from the racer roster.", "success")
            elif action == "ap_target":
                racer = db.session.get(RacingRacer, int(request.form.get("racer_id") or 0))
                if racer is None:
                    flash("Racer not found.", "error")
                else:
                    set_racer_ap_target(
                        db.session,
                        racer,
                        ap_league_slug=str(request.form.get("ap_league_slug") or ""),
                        ap_team_id=int(request.form.get("ap_team_id") or 0),
                    )
                    commit_with_sqlite_retry(db.session)
                    flash("AP target updated.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("racing.admin_racers"))

    racers = list_racers(db.session, active_only=False)
    aliases: dict[int, list] = {}
    for a in db.session.scalars(select(RacingNameAlias)).all():
        aliases.setdefault(int(a.racer_id), []).append(a)
    roster_path = default_roster_txt_path(_slug())
    return render_template(
        "racing/admin_racers.html",
        racers=racers,
        aliases=aliases,
        is_demolition=_is_demolition(),
        roster_txt_path=str(roster_path) if roster_path else "",
        roster_txt_exists=bool(roster_path and roster_path.is_file()),
    )


@racing_bp.route("/admin/racing/ap-grants", methods=["GET", "POST"])
@login_required
def admin_ap_grants():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS)
    scope = (request.args.get("scope") or request.form.get("scope") or "").strip() or None
    currency = (request.args.get("currency") or request.form.get("currency") or "ap").strip() or "ap"
    if currency not in ("ap", "channel_points"):
        currency = "ap"
    if request.method == "POST":
        dest = str(request.form.get("destination_league_slug") or "").strip() or None
        ids = [int(x) for x in request.form.getlist("suggestion_id") if str(x).isdigit()]
        action = str(request.form.get("action") or "grant").strip()
        try:
            if action == "dismiss":
                stats = dismiss_suggestion_batch(db.session, ids)
                flash(
                    f"Removed {stats['dismissed']} already-paid row(s) from the list.",
                    "success" if stats["dismissed"] else "warning",
                )
            else:
                stats = grant_suggestion_batch(
                    db.session,
                    ids,
                    destination_league_slug=dest,
                    created_by_user_id=int(current_user.id) if current_user.is_authenticated else None,
                    racing_league_slug=_slug(),
                )
                label = "Channel Points marked paid" if currency == "channel_points" else "AP grant"
                flash(
                    f"{label} — granted {stats['granted']}, skipped {stats['skipped']}, blocked {stats['blocked']}.",
                    "success" if stats["granted"] else "warning",
                )
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("racing.admin_ap_grants", scope=scope or "", currency=currency))

    suggestions = pending_suggestions(db.session, scope=scope, currency=currency)
    return render_template(
        "racing/admin_ap_grants.html",
        suggestions=suggestions,
        suggestion_event_label=suggestion_event_label,
        scope=scope or "",
        currency=currency,
        is_demolition=_is_demolition(),
    )


@racing_bp.route("/admin/racing/rewards", methods=["GET", "POST"])
@login_required
def admin_rewards():
    """Edit race/circuit AP and Twitch Channel Points place tables."""
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS, ADMIN_ROLE_CONTENT)
    from app.services.racing_rewards import (
        ALL_SCHEDULE_KEYS,
        SCHEDULE_LABELS,
        ensure_default_reward_tiers,
        format_schedule_form_text,
        get_schedule_table,
        parse_schedule_form_text,
        replace_schedule,
    )

    ensure_default_reward_tiers(db.session, league_slug=_slug())
    commit_with_sqlite_retry(db.session)

    if request.method == "POST":
        key = str(request.form.get("schedule_key") or "").strip()
        try:
            pairs = parse_schedule_form_text(str(request.form.get("tiers_text") or ""))
            replace_schedule(db.session, key, pairs)
            commit_with_sqlite_retry(db.session)
            flash(f"Saved {SCHEDULE_LABELS.get(key, key)}.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("racing.admin_rewards"))

    schedules = []
    for key in ALL_SCHEDULE_KEYS:
        table = get_schedule_table(db.session, key)
        schedules.append(
            {
                "key": key,
                "label": SCHEDULE_LABELS[key],
                "text": format_schedule_form_text(table),
                "table": table,
            }
        )
    return render_template(
        "racing/admin_rewards.html",
        schedules=schedules,
        is_demolition=_is_demolition(),
    )

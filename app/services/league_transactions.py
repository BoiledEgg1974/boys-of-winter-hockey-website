"""League transaction feed: CSV import, FHM roster deltas, and trade-log merge."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from flask import current_app
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import LeagueTransaction, Player, Team, db
from app.services.trade_log import TradeLogRow, trade_log_rows, trade_log_source_label
from scripts.import_pipeline.encoding_utils import cell_val, parse_fhm_date, read_csv_normalized, to_int


def _team_by_fhm_or_abbr(key: str | None) -> Team | None:
    if not key:
        return None
    k = str(key).strip()
    t = db.session.scalars(select(Team).where(Team.fhm_team_id == k).limit(1)).first()
    if t:
        return t
    return db.session.scalars(select(Team).where(Team.abbreviation == k).limit(1)).first()


log = logging.getLogger(__name__)

_KIND_LABELS: dict[str, str] = {
    "trade": "Trade",
    "signing": "Signing",
    "waiver": "Waiver",
    "claim": "Claim",
    "release": "Release",
    "call_up": "Call-up",
    "send_down": "Send down",
    "roster_move": "Roster move",
    "other": "Transaction",
}


def transaction_kind_label(kind: str) -> str:
    k = (kind or "").strip().lower()
    return _KIND_LABELS.get(k, k.replace("_", " ").title() or "Transaction")


def _normalize_kind(raw: str | None) -> str:
    k = (raw or "other").strip().lower().replace(" ", "_")
    if k in _KIND_LABELS:
        return k
    aliases = {
        "fa_signing": "signing",
        "free_agent": "signing",
        "waivers": "waiver",
        "callup": "call_up",
        "senddown": "send_down",
        "move": "roster_move",
    }
    return aliases.get(k, "other")


def import_transactions_csv(raw_dir, app) -> int:
    """Import ``transactions.csv`` (replace rows with ``source=csv``)."""
    path = raw_dir / "transactions.csv"
    if not path.is_file():
        return 0
    with app.app_context():
        db.session.execute(delete(LeagueTransaction).where(LeagueTransaction.source == "csv"))
        db.session.commit()
        df = read_csv_normalized(path)
        n = 0
        for _, row in df.iterrows():
            r = row.to_dict()
            team = _team_by_fhm_or_abbr(cell_val(r, "team", "team_abbr", "team_id"))
            other = _team_by_fhm_or_abbr(cell_val(r, "other_team", "other_team_abbr", "other_team_id"))
            raw_date = cell_val(r, "transaction_date", "date", "occurred_on")
            tx_date = parse_fhm_date(raw_date) if raw_date else None
            kind = _normalize_kind(cell_val(r, "type", "kind", "category"))
            player_name = cell_val(r, "player", "player_name")
            player_id = None
            fhm_pid = to_int(cell_val(r, "player_id", "playerid", "fhm_player_id"))
            if fhm_pid is not None:
                pl = db.session.scalars(
                    select(Player).where(Player.fhm_player_id == str(int(fhm_pid))).limit(1)
                ).first()
                if pl:
                    player_id = int(pl.id)
                    if not player_name:
                        player_name = pl.full_name
            headline = (cell_val(r, "headline", "title") or "").strip()
            body = (cell_val(r, "body", "summary", "notes", "description") or "").strip()
            if not headline:
                if player_name and team:
                    headline = f"{player_name} — {team.abbreviation}"
                elif team:
                    headline = f"{team.full_display_name()} — {transaction_kind_label(kind)}"
                else:
                    headline = transaction_kind_label(kind)
            if not team and not body and kind == "other" and not player_name:
                continue
            ext = (cell_val(r, "external_id", "transaction_id", "id") or "").strip() or None
            if ext and len(ext) > 96:
                ext = ext[:96]
            db.session.add(
                LeagueTransaction(
                    transaction_date=tx_date,
                    kind=kind,
                    team_id=int(team.id) if team else None,
                    other_team_id=int(other.id) if other else None,
                    player_id=player_id,
                    headline=headline,
                    body=body,
                    external_id=ext,
                    source="csv",
                )
            )
            n += 1
        db.session.commit()
    return n


def _main_league_fhm_team_ids(session: Session, raw_dir) -> frozenset[int]:
    from app.services.relegation import filter_teams_to_main_tiers, get_tier_config

    tier_cfg = get_tier_config(session, raw_import_dir=raw_dir)
    teams = filter_teams_to_main_tiers(list(session.scalars(select(Team)).all()), tier_cfg)
    return frozenset(int(t.fhm_team_id) for t in teams if t.fhm_team_id is not None)


def record_roster_moves_from_import(raw_dir, app) -> int:
    """Compare ``player_master.csv`` to the last snapshot; log BLUP/BLOW roster changes."""
    path = raw_dir / "player_master.csv"
    if not path.is_file():
        return 0
    with app.app_context():
        from app.site_models import RosterImportSnapshot

        slug = str(current_app.config.get("LEAGUE_SLUG") or "")
        if slug != "bowl-fantasy":
            return 0
        main_fhm_teams = _main_league_fhm_team_ids(db.session, raw_dir)
        if not main_fhm_teams:
            return 0

        current: dict[str, int] = {}
        names: dict[str, str] = {}
        for _, row in read_csv_normalized(path).iterrows():
            r = row.to_dict()
            pid = to_int(cell_val(r, "playerid", "player_id"))
            tid = to_int(cell_val(r, "teamid", "team_id"))
            if pid is None or tid is None:
                continue
            key = str(int(pid))
            current[key] = int(tid)
            fn = cell_val(r, "first_name", "firstname") or ""
            ln = cell_val(r, "last_name", "lastname") or ""
            names[key] = f"{fn} {ln}".strip() or f"Player {pid}"

        snap = db.session.scalars(
            select(RosterImportSnapshot).where(RosterImportSnapshot.league_slug == slug).limit(1)
        ).first()
        if snap is None:
            snap = RosterImportSnapshot(league_slug=slug, snapshot_json="{}")
            db.session.add(snap)
        try:
            previous: dict[str, int] = json.loads(snap.snapshot_json or "{}")
        except json.JSONDecodeError:
            previous = {}
        if not isinstance(previous, dict):
            previous = {}

        if not previous:
            snap.snapshot_json = json.dumps(current)
            snap.updated_at = datetime.utcnow()
            db.session.commit()
            return 0

        today = date.today()
        n = 0
        for pid_key, new_fhm in current.items():
            old_fhm = previous.get(pid_key)
            if old_fhm is None or int(old_fhm) == int(new_fhm):
                continue
            if int(old_fhm) not in main_fhm_teams and int(new_fhm) not in main_fhm_teams:
                continue
            new_team = db.session.scalars(
                select(Team).where(Team.fhm_team_id == str(int(new_fhm))).limit(1)
            ).first()
            old_team = db.session.scalars(
                select(Team).where(Team.fhm_team_id == str(int(old_fhm))).limit(1)
            ).first()
            pl = db.session.scalars(
                select(Player).where(Player.fhm_player_id == pid_key).limit(1)
            ).first()
            pname = names.get(pid_key) or (pl.full_name if pl else f"Player {pid_key}")
            old_label = old_team.abbreviation if old_team else str(old_fhm)
            new_label = new_team.abbreviation if new_team else str(new_fhm)
            headline = f"{pname} → {new_label}"
            body = f"Roster update from FHM import ({old_label} → {new_label})."
            ext = f"roster_delta:{pid_key}:{old_fhm}:{new_fhm}"
            exists = db.session.scalars(
                select(LeagueTransaction.id).where(LeagueTransaction.external_id == ext).limit(1)
            ).first()
            if exists:
                continue
            db.session.add(
                LeagueTransaction(
                    transaction_date=today,
                    kind="roster_move",
                    team_id=int(new_team.id) if new_team else None,
                    other_team_id=int(old_team.id) if old_team else None,
                    player_id=int(pl.id) if pl else None,
                    headline=headline,
                    body=body,
                    external_id=ext,
                    source="roster_delta",
                )
            )
            n += 1

        snap.snapshot_json = json.dumps(current)
        snap.updated_at = datetime.utcnow()
        db.session.commit()
    return n


def _serialize_db_row(row: LeagueTransaction) -> dict[str, Any]:
    team = row.team
    other = row.other_team
    pl = row.player
    return {
        "sort_at": (
            datetime.combine(row.transaction_date, datetime.min.time()).isoformat()
            if row.transaction_date
            else ""
        ),
        "date": row.transaction_date.isoformat() if row.transaction_date else None,
        "kind": row.kind,
        "kind_label": transaction_kind_label(row.kind),
        "headline": row.headline,
        "body": (row.body or "").strip(),
        "team_id": int(row.team_id) if row.team_id is not None else None,
        "team_abbr": team.abbreviation if team else None,
        "other_team_id": int(row.other_team_id) if row.other_team_id is not None else None,
        "other_team_abbr": other.abbreviation if other else None,
        "player_name": pl.full_name if pl else None,
        "is_trade": row.kind == "trade",
        "source_label": "CSV import" if row.source == "csv" else "FHM import",
        "entry_id": int(row.id),
    }


def _serialize_trade_row(row: TradeLogRow) -> dict[str, Any]:
    sort_at = row.sort_at.isoformat() if row.sort_at and row.sort_at != datetime.min else ""
    return {
        "sort_at": sort_at,
        "date": row.trade_date.isoformat() if row.trade_date else None,
        "kind": "trade",
        "kind_label": "Trade",
        "headline": row.title,
        "body": (row.body or "").strip(),
        "team_id": int(row.team_a.id) if row.team_a else None,
        "team_abbr": row.team_a.abbreviation if row.team_a else None,
        "other_team_id": int(row.team_b.id) if row.team_b else None,
        "other_team_abbr": row.team_b.abbreviation if row.team_b else None,
        "player_name": None,
        "is_trade": True,
        "source_label": trade_log_source_label(row.source),
        "trade_log_key": row.log_key,
    }


def league_transactions_payload(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    team_id: int | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Merged feed for homepage and team panels."""
    cap = max(1, min(100, int(limit)))
    merged: list[tuple[str, dict[str, Any]]] = []

    q = select(LeagueTransaction).order_by(
        LeagueTransaction.transaction_date.desc().nulls_last(),
        LeagueTransaction.id.desc(),
    )
    if team_id is not None:
        tid = int(team_id)
        rows = league_session.scalars(q).all()
        rows = [
            r
            for r in rows
            if (r.team_id == tid or r.other_team_id == tid)
        ]
    else:
        rows = list(league_session.scalars(q.limit(500)).all())

    for row in rows:
        payload = _serialize_db_row(row)
        merged.append((payload.get("sort_at") or "", payload))

    for trow in trade_log_rows(
        league_session, site_session, league_slug=league_slug, team_id=team_id, limit=200
    ):
        payload = _serialize_trade_row(trow)
        merged.append((payload.get("sort_at") or "", payload))

    merged.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in merged:
        dedupe = f"{item.get('kind')}:{item.get('headline')}:{item.get('date')}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append(item)
        if len(out) >= cap:
            break
    return out

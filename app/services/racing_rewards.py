"""Admin-configurable race/circuit AP and Twitch channel-point reward tables."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.racing_models import RacingRewardTier

SCHEDULE_RACE_AP = "race_ap"
SCHEDULE_CIRCUIT_AP = "circuit_ap"
SCHEDULE_RACE_CP = "race_channel_points"
SCHEDULE_CIRCUIT_CP = "circuit_channel_points"

ALL_SCHEDULE_KEYS = (
    SCHEDULE_RACE_AP,
    SCHEDULE_CIRCUIT_AP,
    SCHEDULE_RACE_CP,
    SCHEDULE_CIRCUIT_CP,
)

SCHEDULE_LABELS = {
    SCHEDULE_RACE_AP: "Individual race / night — Action Points (AP)",
    SCHEDULE_CIRCUIT_AP: "Circuit standings — Action Points (AP)",
    SCHEDULE_RACE_CP: "Individual race / night — Twitch Channel Points",
    SCHEDULE_CIRCUIT_CP: "Circuit standings — Twitch Channel Points",
}


def default_tiers_for_league(league_slug: str) -> dict[str, list[tuple[int, int]]]:
    """Seed tables from game docs; admin can change any place/amount."""
    is_derby = league_slug == "bowl-demolition"
    if is_derby:
        return {
            SCHEDULE_RACE_AP: [(1, 6), (2, 5), (3, 4), (4, 3), (5, 2), (6, 1)],
            SCHEDULE_CIRCUIT_AP: [(1, 30), (2, 25), (3, 20), (4, 15), (5, 10), (6, 5)],
            # Kill-rank style payouts commonly used on stream (editable).
            SCHEDULE_RACE_CP: [(1, 1000), (2, 800), (3, 600), (4, 400), (5, 200)],
            SCHEDULE_CIRCUIT_CP: [(1, 1500), (2, 1000), (3, 750), (4, 500), (5, 250)],
        }
    # Formula: race AP for claimed podium-ish finishes; CP for P11+ style depth.
    return {
        SCHEDULE_RACE_AP: [(1, 10), (2, 8), (3, 6), (4, 5), (5, 4), (6, 3), (7, 2), (8, 1)],
        SCHEDULE_CIRCUIT_AP: [(1, 30), (2, 25), (3, 20), (4, 15), (5, 10), (6, 5)],
        SCHEDULE_RACE_CP: [
            (11, 200),
            (12, 180),
            (13, 160),
            (14, 140),
            (15, 120),
            (16, 100),
            (17, 80),
            (18, 60),
            (19, 40),
            (20, 25),
        ],
        SCHEDULE_CIRCUIT_CP: [(1, 1000), (2, 800), (3, 600), (4, 400), (5, 200)],
    }


def ensure_racing_reward_schema(engine: Engine) -> None:
    """Add currency column on existing racing SQLite files; create_all adds new tables."""
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='racing_ap_suggestions' LIMIT 1"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(racing_ap_suggestions)")).fetchall()}
        if "currency" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE racing_ap_suggestions "
                    "ADD COLUMN currency VARCHAR(32) NOT NULL DEFAULT 'ap'"
                )
            )


def ensure_default_reward_tiers(session: Session, *, league_slug: str) -> None:
    """Seed missing schedule keys; never overwrite admin-edited amounts."""
    existing_keys = {
        str(k)
        for k in session.scalars(select(RacingRewardTier.schedule_key).distinct()).all()
    }
    defaults = default_tiers_for_league(league_slug)
    for key, pairs in defaults.items():
        if key in existing_keys:
            continue
        for place, amount in pairs:
            session.add(RacingRewardTier(schedule_key=key, place=int(place), amount=int(amount)))


def get_schedule_table(session: Session, schedule_key: str) -> dict[int, int]:
    rows = list(
        session.scalars(
            select(RacingRewardTier)
            .where(RacingRewardTier.schedule_key == schedule_key)
            .order_by(RacingRewardTier.place.asc())
        ).all()
    )
    return {int(r.place): int(r.amount) for r in rows}


def amount_for_place(session: Session, schedule_key: str, place: int) -> int:
    if place <= 0:
        return 0
    table = get_schedule_table(session, schedule_key)
    return int(table.get(int(place), 0))


def replace_schedule(
    session: Session,
    schedule_key: str,
    place_amounts: list[tuple[int, int]],
) -> None:
    if schedule_key not in ALL_SCHEDULE_KEYS:
        raise ValueError(f"Unknown schedule key: {schedule_key}")
    existing = list(
        session.scalars(select(RacingRewardTier).where(RacingRewardTier.schedule_key == schedule_key)).all()
    )
    for row in existing:
        session.delete(row)
    session.flush()
    for place, amount in place_amounts:
        p = int(place)
        a = int(amount)
        if p <= 0 or a < 0:
            continue
        session.add(RacingRewardTier(schedule_key=schedule_key, place=p, amount=a))


def parse_schedule_form_text(raw: str) -> list[tuple[int, int]]:
    """Parse lines like ``1=25`` or ``1,25`` or ``1 25``."""
    out: list[tuple[int, int]] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        for sep in ("=", ",", ":"):
            if sep in text:
                left, right = text.split(sep, 1)
                break
        else:
            parts = text.split()
            if len(parts) != 2:
                raise ValueError(f"Bad schedule line: {text!r}")
            left, right = parts[0], parts[1]
        place = int(left.strip())
        amount = int(right.strip())
        out.append((place, amount))
    # de-dupe by place (last wins)
    merged: dict[int, int] = {}
    for place, amount in out:
        merged[place] = amount
    return sorted(merged.items(), key=lambda x: x[0])


def format_schedule_form_text(table: dict[int, int]) -> str:
    return "\n".join(f"{place}={amount}" for place, amount in sorted(table.items()))

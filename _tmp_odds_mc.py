"""Reproduce Cap playoff % for OTT/BOS using live league3.db."""
from __future__ import annotations

import os
import sys

os.environ["LEAGUE_SLUG"] = "bowl-cap"
sys.path.insert(0, r"C:\Users\keeno\Projects\Boys-Of-Winter-League")

from app import create_app
from app.config import make_league_config
from app.models import Team, TeamStanding, db
from app.services.postseason_odds import (
    _load_monte_carlo_context,
    _nhl_style_qualifiers,
    build_postseason_odds_payload,
)
from sqlalchemy import select


def main() -> None:
    app = create_app(make_league_config("bowl-cap"))
    with app.app_context():
        standings = list(db.session.scalars(select(TeamStanding).where(TeamStanding.season_id == 1)).all())
        teams = {t.id: t for t in db.session.scalars(select(Team)).all()}
        tm_map = {st.team_id: teams[st.team_id] for st in standings if st.team_id in teams}

        ctx = _load_monte_carlo_context(db.session, 1, tm_map)
        assert ctx is not None
        base_rows, remaining, base_by_id, two_conf, team_ids = ctx
        print("n_teams", len(base_rows), "remaining", len(remaining), "two_conf", two_conf)
        from collections import defaultdict

        by_conf = defaultdict(list)
        for t in base_rows:
            by_conf[t.conference].append(t)
        for ck, rows in by_conf.items():
            print(f"conf={ck!r} size={len(rows)}")
            q = _nhl_style_qualifiers(rows)
            print("  baseline qualifiers (no rem games sim)", len(q))
            by_div = defaultdict(list)
            for t in rows:
                by_div[t.division].append(t)
            for dk, div_ts in sorted(by_div.items()):
                div_ts = sorted(div_ts, key=lambda x: (x.pts, x.w), reverse=True)
                print(
                    f"  div {dk}: "
                    f"{[(teams[t.team_id].abbreviation, t.w, t.pts) for t in div_ts]}"
                )

        payload = build_postseason_odds_payload(db.session, 1, tm_map, n_sims=600)
        assert payload
        for abbr in ("OTT", "BOS", "BUF", "MTL", "ANA"):
            tm = next(t for t in tm_map.values() if t.abbreviation == abbr)
            po = payload["by_slug"][tm.slug]
            print(f"{abbr} slug={tm.slug} playoffs={po['playoffs']*100:.1f}% division={po['division']*100:.1f}%")

        # Simulate standings enrichment path with Wales-only filter (15 teams)
        wales = [st for st in standings if teams.get(st.team_id) and teams[st.team_id].fhm_conference_id == 0]
        tm_wales = {st.team_id: teams[st.team_id] for st in wales}
        print("\nWales-only teams", len(tm_wales))
        payload_w = build_postseason_odds_payload(db.session, 1, tm_wales, n_sims=600)
        assert payload_w
        for abbr in ("OTT", "BOS"):
            tm = next(t for t in tm_wales.values() if t.abbreviation == abbr)
            po = payload_w["by_slug"][tm.slug]
            print(f"WALES {abbr} playoffs={po['playoffs']*100:.1f}%")

        # Northeast division only (5 teams) — n<=8 path
        ne = [st for st in standings if (st.division or "") == "Northeast Division"]
        tm_ne = {st.team_id: teams[st.team_id] for st in ne}
        print("\nNortheast-only teams", len(tm_ne))
        payload_ne = build_postseason_odds_payload(db.session, 1, tm_ne, n_sims=600)
        assert payload_ne
        for abbr in ("OTT", "BOS"):
            tm = next(t for t in tm_ne.values() if t.abbreviation == abbr)
            po = payload_ne["by_slug"][tm.slug]
            print(f"NE {abbr} playoffs={po['playoffs']*100:.1f}%")


if __name__ == "__main__":
    main()

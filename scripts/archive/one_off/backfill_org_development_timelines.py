"""Backfill org-development snapshot timelines onto the in-game calendar."""
from __future__ import annotations

from app import create_app
from app.config import LEAGUES, make_league_config
from app.models import db
from app.services.org_development import persist_org_development_reports_for_league
from app.services.org_development_timeline import backfill_null_snapshot_timelines


def main() -> None:
    for entry in LEAGUES:
        slug = entry.slug
        app = create_app(make_league_config(slug))
        with app.app_context():
            backfilled = backfill_null_snapshot_timelines(db.session)
            rebuilt = persist_org_development_reports_for_league(db.session, slug)
            print(f"{slug}: backfilled={backfilled} reports_upserted={rebuilt}")


if __name__ == "__main__":
    main()

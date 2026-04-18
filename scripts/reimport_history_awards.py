"""Clear ``history_awards`` and reload from ``history_awards.csv`` in ``RAW_IMPORT_DIR``.

Run from repo root (uses the same league DB as the app, e.g. ``LEAGUE_SLUG`` / default config):

  PYTHONPATH=. python scripts/reimport_history_awards.py
"""
from __future__ import annotations

from pathlib import Path

from app import create_app
from scripts.import_pipeline.runner import import_history_awards


def main() -> None:
    app = create_app()
    with app.app_context():
        raw = Path(str(app.config["RAW_IMPORT_DIR"]))
        n = import_history_awards(raw, app, replace_all=True)
        print(f"Imported {n} history_awards row(s) from {raw}.")


if __name__ == "__main__":
    main()

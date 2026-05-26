"""Trade log manual entries, AI prompt builders, and public route smoke tests."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import MagicMock

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Team, TradeLogEntry
from app.routes.site_portal import (
    _manual_trade_summary_from_parts,
    _manual_trade_summary_parts,
)
from app.services.trade_ai_opinion import (
    build_logged_trade_prompt_block,
    build_trade_prompt_block,
    recent_trades_prompt_block,
)
from app.services.trade_log import (
    TradeLogRow,
    format_recent_trades_for_prompt,
    resolve_trade_log_row,
    trade_log_rows,
    trade_log_source_label,
)


class TradeLogSourceLabelTest(unittest.TestCase):
    def test_source_labels(self) -> None:
        self.assertEqual(trade_log_source_label("manual"), "Manual")
        self.assertEqual(trade_log_source_label("csv"), "CSV import")
        self.assertEqual(trade_log_source_label("site"), "Trade Tool")


class ManualTradeLogSummaryTest(unittest.TestCase):
    def test_manual_summary_round_trips_split_fields(self) -> None:
        summary = _manual_trade_summary_from_parts(
            team_a_label="Springfield",
            team_b_label="Shelbyville",
            team_a_outgoing="Player A\n2027 2nd",
            team_b_outgoing="Player B",
        )

        self.assertIn("Springfield sends:", summary)
        self.assertEqual(
            _manual_trade_summary_parts(summary),
            ("Player A\n2027 2nd", "Player B"),
        )

    def test_legacy_manual_summary_stays_editable(self) -> None:
        self.assertEqual(
            _manual_trade_summary_parts("Old one-field summary"),
            ("Old one-field summary", ""),
        )


class TradeLogPromptTest(unittest.TestCase):
    def test_recent_trades_prompt_includes_rows(self) -> None:
        row = TradeLogRow(
            sort_at=__import__("datetime").datetime(2024, 6, 1),
            trade_date=date(2024, 6, 1),
            team_a=None,
            team_b=None,
            title="Trade: A ↔ B",
            body="Player X for pick Y",
            source="manual",
            entry_id=1,
            log_key="manual:1",
        )
        block = format_recent_trades_for_prompt([row])
        self.assertIn("Recent league trades", block)
        self.assertIn("Manual", block)
        self.assertIn("Player X for pick Y", block)

    def test_logged_trade_prompt_contains_summary(self) -> None:
        from datetime import datetime

        from unittest.mock import MagicMock

        ta = MagicMock()
        ta.full_display_name.return_value = "Toronto"
        tb = MagicMock()
        tb.full_display_name.return_value = "Boston"
        row = TradeLogRow(
            sort_at=datetime(2023, 1, 15),
            trade_date=date(2023, 1, 15),
            team_a=ta,
            team_b=tb,
            title="Trade: Toronto ↔ Boston",
            body="Big one-for-one swap",
            source="manual",
            entry_id=9,
            log_key="manual:9",
        )
        block = build_logged_trade_prompt_block(row)
        self.assertIn("Toronto", block)
        self.assertIn("Big one-for-one swap", block)

    def test_hypothetical_prompt_accepts_recent_context(self) -> None:
        from unittest.mock import MagicMock

        session = MagicMock()
        block = build_trade_prompt_block(
            session,
            None,
            None,
            [],
            [],
            "",
            recent_trades_context="Recent league trades:\n  • test",
        )
        self.assertIn("Recent league trades", block)

    def test_recent_trades_prompt_block_wrapper(self) -> None:
        block = recent_trades_prompt_block([])
        self.assertIn("(none on record)", block)


class TradeLogIntegrationTest(unittest.TestCase):
    def test_manual_entry_appears_in_trade_log_rows(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            teams = list(db.session.scalars(select(Team).limit(2)).all())
            if len(teams) < 2:
                self.skipTest("Need at least two teams in league DB")
            ent = TradeLogEntry(
                trade_date=date(1999, 1, 1),
                team_a_id=int(teams[0].id),
                team_b_id=int(teams[1].id),
                summary="Test manual trade (unit test)",
                source="manual",
                external_id=None,
            )
            db.session.add(ent)
            db.session.commit()
            eid = int(ent.id)
            try:
                slug = str(app.config.get("LEAGUE_SLUG") or "")
                rows = trade_log_rows(db.session, db.session, league_slug=slug, limit=500)
                match = [r for r in rows if r.entry_id == eid and r.source == "manual"]
                self.assertTrue(match)
                self.assertIn("Test manual trade", match[0].body)
                resolved = resolve_trade_log_row(
                    db.session, db.session, league_slug=slug, source="manual", row_id=eid
                )
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved.entry_id, eid)
            finally:
                db.session.delete(db.session.get(TradeLogEntry, eid))
                db.session.commit()

    def test_csv_entries_are_not_public_trade_log_rows(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            teams = list(db.session.scalars(select(Team).limit(2)).all())
            if len(teams) < 2:
                self.skipTest("Need at least two teams in league DB")
            ent = TradeLogEntry(
                trade_date=date(1999, 2, 1),
                team_a_id=int(teams[0].id),
                team_b_id=int(teams[1].id),
                summary="Test CSV trade should be hidden",
                source="csv",
                external_id="unit-test-hidden-csv",
            )
            db.session.add(ent)
            db.session.commit()
            eid = int(ent.id)
            try:
                slug = str(app.config.get("LEAGUE_SLUG") or "")
                rows = trade_log_rows(db.session, db.session, league_slug=slug, limit=500)
                self.assertFalse([r for r in rows if r.entry_id == eid])
            finally:
                db.session.delete(db.session.get(TradeLogEntry, eid))
                db.session.commit()

    def test_transaction_news_without_trade_title_is_hidden(self) -> None:
        league_session = MagicMock()
        league_session.scalars.return_value.all.return_value = []
        site_session = MagicMock()
        article = MagicMock(
            id=123,
            title="Petteri Nummelin Claimed from Waivers",
            body="Waiver claim story",
            published_at=datetime(2026, 5, 26),
            created_at=datetime(2026, 5, 26),
        )
        site_session.scalars.return_value.all.return_value = [article]

        rows = trade_log_rows(league_session, site_session, league_slug="bowl-cap")

        self.assertEqual(rows, [])

    def test_trade_tool_news_title_is_included(self) -> None:
        league_session = MagicMock()
        league_session.scalars.return_value.all.return_value = []
        site_session = MagicMock()
        article = MagicMock(
            id=124,
            title="Trade: Team A ↔ Team B",
            body="A player for a pick",
            published_at=datetime(2026, 5, 26),
            created_at=datetime(2026, 5, 26),
        )
        site_session.scalars.return_value.all.return_value = [article]

        rows = trade_log_rows(league_session, site_session, league_slug="bowl-cap")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "site")
        self.assertEqual(rows[0].article_id, 124)


class TradeLogRoutesTest(unittest.TestCase):
    def test_trade_log_renders_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            app = create_app(make_league_config(slug))
            with app.test_client() as client:
                r = client.get("/trade-log")
                self.assertEqual(r.status_code, 200, slug)
                self.assertIn(b"Trade Log", r.data)

    def test_ai_trade_tool_redirects_without_login(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            r = client.get("/ai-trade-tool", follow_redirects=False)
            self.assertIn(r.status_code, (302, 401))


if __name__ == "__main__":
    unittest.main()

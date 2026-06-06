"""Trade Market service validation and sorting."""
from __future__ import annotations

import unittest
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.services.trade_market import (
    BUYING_CATEGORY_KEYS,
    annotate_trade_market_need_matches,
    annotate_trade_market_watchlist,
    build_trade_market_activity_ticker,
    buying_discord_update_should_enqueue,
    cleanup_stale_selling_listings,
    enrich_listing_row,
    listing_freshness_badge,
    maybe_enqueue_buying_discord,
    maybe_enqueue_selling_discord,
    replace_buying_needs,
    replace_selling_listings,
    selling_discord_update_should_enqueue,
    sort_selling_rows,
    _validate_owned_asset,
    _listing_expired_by_ingame_days,
)
from app.services.trade_tool import validate_ledger


class TradeMarketServiceTest(unittest.TestCase):
    def test_sort_selling_by_ovr_desc(self) -> None:
        rows = [
            {"asset_label": "Low", "ovr": 70},
            {"asset_label": "High", "ovr": 92},
            {"asset_label": "Mid", "ovr": 80},
        ]
        out = sort_selling_rows(rows, sort_key="ovr", order="desc")
        self.assertEqual([r["asset_label"] for r in out], ["High", "Mid", "Low"])

    def test_sort_selling_by_team_asc(self) -> None:
        rows = [
            {"team_name": "Toronto"},
            {"team_name": "Anaheim"},
        ]
        out = sort_selling_rows(rows, sort_key="team", order="asc")
        self.assertEqual(out[0]["team_name"], "Anaheim")

    def test_replace_buying_dedupes_categories(self) -> None:
        site = MagicMock()
        site.execute.return_value = None
        added = []

        def capture(row):
            added.append(row)

        site.add.side_effect = capture
        site.flush.return_value = None
        rows = replace_buying_needs(
            site,
            league_slug="bowl-cap",
            user_id=1,
            team_id=10,
            categories=["prospects", "prospects", "goalie", "invalid"],
            note="Need help",
        )
        self.assertEqual(len(rows), 2)
        cats = {r.category for r in rows}
        self.assertEqual(cats, {"prospects", "goalie"})
        self.assertTrue(cats <= BUYING_CATEGORY_KEYS)

    def test_selling_delete_only_update_skips_discord(self) -> None:
        old_rows = [
            MagicMock(asset_type="contract", asset_ref="player:1:roster"),
            MagicMock(asset_type="draft_pick", asset_ref="dpick:42"),
        ]
        remaining_rows = [MagicMock(asset_type="contract", asset_ref="player:1:roster")]
        added_rows = [
            MagicMock(asset_type="contract", asset_ref="player:1:roster"),
            MagicMock(asset_type="prospect", asset_ref="player:2:unsigned"),
        ]

        self.assertFalse(selling_discord_update_should_enqueue(old_rows, remaining_rows))
        self.assertFalse(selling_discord_update_should_enqueue(old_rows, []))
        self.assertTrue(selling_discord_update_should_enqueue(old_rows, added_rows))

    def test_buying_delete_only_update_skips_discord(self) -> None:
        old_rows = [
            MagicMock(category="prospects"),
            MagicMock(category="goalie"),
        ]
        remaining_rows = [MagicMock(category="prospects")]
        added_rows = [
            MagicMock(category="prospects"),
            MagicMock(category="top_4_defense"),
        ]

        self.assertFalse(buying_discord_update_should_enqueue(old_rows, remaining_rows))
        self.assertFalse(buying_discord_update_should_enqueue(old_rows, []))
        self.assertTrue(buying_discord_update_should_enqueue(old_rows, added_rows))

    def test_empty_trade_market_lists_do_not_enqueue_discord(self) -> None:
        with patch("app.services.trade_market.enqueue_discord_event") as enqueue:
            maybe_enqueue_selling_discord(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-cap",
                team_id=10,
                listings=[],
            )
            maybe_enqueue_buying_discord(
                MagicMock(),
                league_slug="bowl-cap",
                team_id=10,
                needs=[],
            )

        enqueue.assert_not_called()

    def test_listing_freshness_badge_new_vs_updated(self) -> None:
        now = datetime(2026, 6, 6, 12, 0, 0)
        created = now - timedelta(hours=2)
        self.assertEqual(
            listing_freshness_badge(created_at=created, updated_at=created, now=now),
            "new",
        )
        self.assertEqual(
            listing_freshness_badge(
                created_at=created - timedelta(days=3),
                updated_at=created,
                now=now,
            ),
            "updated",
        )

    def test_activity_ticker_orders_recent_updates(self) -> None:
        older = datetime(2026, 6, 1, 12, 0, 0)
        newer = datetime(2026, 6, 5, 12, 0, 0)
        ticker = build_trade_market_activity_ticker(
            [{"team_name": "A", "asset_label": "Player", "updated_at": older}],
            [{"team_name": "B", "category_labels": "Goalie", "updated_at": newer}],
            limit=5,
        )
        self.assertEqual(len(ticker), 2)
        self.assertEqual(ticker[0]["team_name"], "B")

    def test_watchlist_and_need_match_annotations(self) -> None:
        rows = [{"team_id": 3, "wants": ["goalie"]}]
        annotate_trade_market_watchlist(rows, watchlist_team_ids={3})
        annotate_trade_market_need_matches(rows, my_buying_categories={"goalie"})
        self.assertTrue(rows[0]["watchlist_match"])
        self.assertTrue(rows[0]["need_match"])

    def test_replace_selling_stores_free_text_wants(self) -> None:
        site = MagicMock()
        site.execute.return_value = None
        site.flush.return_value = None
        added = []
        def capture(row):
            row.id = len(added) + 1
            added.append(row)

        site.add.side_effect = capture
        with patch("app.services.trade_market._validate_owned_asset", return_value=True):
            with patch("app.services.trade_market._enqueue_trade_market_watch_alerts"):
                rows, err = replace_selling_listings(
                    site,
                    MagicMock(),
                    league_slug="bowl-cap",
                    user_id=1,
                    team_id=10,
                    items=[
                        {
                            "asset_type": "contract",
                            "asset_ref": "player:99:roster",
                            "asking_price": "2nd round pick",
                            "wants": "Top-four RD or defensive center",
                            "note": "",
                        }
                    ],
                    raw_dir=None,
                )

        self.assertIsNone(err)
        self.assertEqual(rows, added)
        self.assertEqual(json.loads(rows[0].wants_json), ["Top-four RD or defensive center"])

    def test_validate_owned_asset_rejects_unknown_player(self) -> None:
        site = MagicMock()
        league = MagicMock()
        with patch(
            "app.services.trade_market.selectable_selling_assets",
            return_value={"roster": [], "unsigned": [], "draft_picks": []},
        ):
            with patch(
                "app.services.trade_market.draft_pick_owned_by_team",
                return_value=None,
            ):
                ok = _validate_owned_asset(
                    site,
                    league,
                    league_slug="bowl-cap",
                    team_id=1,
                    asset_type="contract",
                    asset_ref="player:999:roster",
                    raw_dir=None,
                )
        self.assertFalse(ok)

    def test_validate_owned_asset_rejects_unavailable_draft_pick(self) -> None:
        site = MagicMock()
        league = MagicMock()
        with patch(
            "app.services.trade_market.owned_draft_pick_drag_keys",
            return_value=set(),
        ):
            ok = _validate_owned_asset(
                site,
                league,
                league_slug="bowl-cap",
                team_id=1,
                asset_type="draft_pick",
                asset_ref="dpick:42",
                raw_dir=None,
            )
        self.assertFalse(ok)

    def test_enrich_listing_marks_stale_draft_pick_unavailable(self) -> None:
        listing = MagicMock(
            id=10,
            team_id=1,
            user_id=2,
            league_slug="bowl-cap",
            asset_type="draft_pick",
            asset_ref="dpick:42",
            asking_price="",
            wants_json="[]",
            note="",
            updated_at=datetime.now(UTC),
        )
        with patch(
            "app.services.trade_market.owned_draft_pick_drag_keys",
            return_value=set(),
        ):
            row = enrich_listing_row(
                MagicMock(),
                MagicMock(),
                listing,
                teams_by_id={},
                users_by_id={},
            )
        self.assertFalse(row["is_current_asset"])

    def test_listing_expires_after_45_ingame_days(self) -> None:
        listing = MagicMock(posted_game_date=date(2001, 1, 1))
        self.assertFalse(
            _listing_expired_by_ingame_days(
                listing,
                latest_game_date=date(2001, 2, 15),
            )
        )
        self.assertTrue(
            _listing_expired_by_ingame_days(
                listing,
                latest_game_date=date(2001, 2, 16),
            )
        )

    def test_cleanup_deletes_invalid_active_selling_listing(self) -> None:
        site = MagicMock()
        league = MagicMock()
        listing = MagicMock(
            league_slug="bowl-cap",
            team_id=10,
            asset_type="contract",
            asset_ref="player:99:roster",
            posted_game_date=date(2001, 1, 1),
        )
        site.scalars.return_value.all.return_value = [listing]
        with patch("app.services.trade_market.latest_trade_market_game_date", return_value=date(2001, 1, 2)):
            with patch("app.services.trade_market._validate_owned_asset", return_value=False):
                deleted = cleanup_stale_selling_listings(
                    site,
                    league,
                    league_slug="bowl-cap",
                    raw_dir=None,
                )

        self.assertEqual(deleted, 1)
        site.delete.assert_called_once_with(listing)

    def test_trade_tool_blocks_manual_picks_when_admin_ownership_exists(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.trade_tool.draft_pick_ownership_exists",
            return_value=True,
        ):
            err = validate_ledger(
                session,
                from_team_id=1,
                to_team_id=2,
                left_out=["mpleft:1:testpick"],
                right_out=[],
                raw_dir=None,
                league_slug="bowl-cap",
                draft_round_cap=12,
            )
        self.assertEqual(
            err,
            "One or more assets leaving your team are not valid for your roster.",
        )


if __name__ == "__main__":
    unittest.main()

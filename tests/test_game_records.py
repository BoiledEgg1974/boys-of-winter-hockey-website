"""Game records service and public route."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import GameRecordBaseline, GameSkaterStat, Player, Team
from app.services.game_records import (
    GameRecordHolder,
    GameRecordMetric,
    MANUAL_BASELINE_NOTE,
    _merge_holders,
    baseline_team_choices_for_admin,
    build_game_records_page,
    format_game_record_value,
    game_record_season_year,
    game_record_metrics,
    resolve_game_record,
    sync_game_record_baselines,
    upsert_baseline,
)
from app.services.rookie_eligibility import (
    is_nhl_style_rookie,
    prior_skater_gp_by_season_for_players,
    rookie_cutoff_date,
)


class GameRecordsServiceTest(unittest.TestCase):
    def test_format_toi_and_plus_minus(self) -> None:
        toi_metric = GameRecordMetric("toi_seconds", "Min Played", "skater", value_kind="time")
        pm_metric = GameRecordMetric("plus_minus_high", "Highest Plus Minus", "skater", value_kind="plus_minus")
        self.assertEqual(format_game_record_value(889, toi_metric), "14:49")
        self.assertEqual(format_game_record_value(3, pm_metric), "+3")
        self.assertEqual(format_game_record_value(-2, pm_metric), "-2")

    def test_merge_prefers_better_boxscore_when_higher_is_better(self) -> None:
        metric = GameRecordMetric("goals", "Goals", "skater")
        baseline = GameRecordHolder(
            metric=metric,
            value=5.0,
            display_value="5",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label="1968-69",
            game_id=None,
            source="baseline",
        )
        computed = GameRecordHolder(
            metric=metric,
            value=6.0,
            display_value="6",
            player=None,
            team=None,
            opponent_team=None,
            game_date=date(1969, 1, 2),
            season_label="1968-69",
            game_id=99,
            source="boxscore",
        )
        merged = _merge_holders(baseline, computed, metric)
        self.assertEqual(merged.source, "boxscore")
        self.assertEqual(merged.value, 6.0)

    def test_merge_keeps_baseline_when_boxscore_does_not_beat_it(self) -> None:
        metric = GameRecordMetric("goals", "Goals", "skater")
        baseline = GameRecordHolder(
            metric=metric,
            value=6.0,
            display_value="6",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label=None,
            game_id=None,
            source="baseline",
        )
        computed = GameRecordHolder(
            metric=metric,
            value=4.0,
            display_value="4",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label=None,
            game_id=1,
            source="boxscore",
        )
        merged = _merge_holders(baseline, computed, metric)
        self.assertEqual(merged.source, "baseline")

    def test_goalie_metrics_exclude_game_rating_and_save_pct(self) -> None:
        metrics = game_record_metrics(player_kind="goalie")
        by_key = {m.key: m for m in metrics}

        self.assertNotIn("game_rating", by_key)
        self.assertNotIn("save_pct", by_key)
        self.assertNotIn("minutes_played", by_key)
        self.assertIn("goals_allowed", by_key)
        self.assertTrue(by_key["goals_allowed"].higher_is_better)

    def test_goals_allowed_prefers_most_allowed(self) -> None:
        metric = GameRecordMetric("goals_allowed", "Goals Allowed", "goalie")
        baseline = GameRecordHolder(
            metric=metric,
            value=4.0,
            display_value="4",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label=None,
            game_id=None,
            source="baseline",
        )
        computed = GameRecordHolder(
            metric=metric,
            value=7.0,
            display_value="7",
            player=None,
            team=None,
            opponent_team=None,
            game_date=None,
            season_label=None,
            game_id=1,
            source="boxscore",
        )

        merged = _merge_holders(baseline, computed, metric)
        self.assertEqual(merged.source, "boxscore")
        self.assertEqual(merged.value, 7.0)

    def test_game_record_season_year_prefers_label_and_handles_winter_dates(self) -> None:
        self.assertEqual(game_record_season_year(date(1993, 2, 10), "1992-93"), 1992)
        self.assertEqual(game_record_season_year(date(1992, 11, 25), None), 1992)
        self.assertEqual(game_record_season_year(date(1993, 2, 10), None), 1992)

    def test_baseline_team_choices_include_defunct_identities(self) -> None:
        current = SimpleNamespace(
            id=7,
            fhm_team_id="23",
            name="Carolina",
            nickname="Hurricanes",
            full_display_name=lambda: "Carolina Hurricanes",
        )
        identity = SimpleNamespace(
            id=42,
            team_id=7,
            team_fhm_id="23",
            display_name="Hartford Whalers",
            start_year=1989,
            end_year=1992,
        )
        session = MagicMock()
        teams_scalars = MagicMock()
        teams_scalars.all.return_value = [current]
        identities_scalars = MagicMock()
        identities_scalars.all.return_value = [identity]
        session.scalars.side_effect = [teams_scalars, identities_scalars]

        choices = baseline_team_choices_for_admin(session)

        self.assertIn(("Carolina Hurricanes", 7), [(c.label, c.value) for c in choices])
        self.assertIn(("Hartford Whalers (1989-1992)", 7), [(c.label, c.value) for c in choices])

    def test_rookie_cutoff_date_uses_season_start(self) -> None:
        season = SimpleNamespace(start_year=1969, end_year=1970)
        self.assertEqual(rookie_cutoff_date(season), date(1969, 9, 15))

    def test_nhl_style_rookie_rejects_veteran_gp(self) -> None:
        season = SimpleNamespace(start_year=1969, end_year=1970)
        self.assertFalse(is_nhl_style_rookie([30], date(1950, 1, 1), season))

    def test_prior_skater_gp_aggregates_first_row_without_keyerror(self) -> None:
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            (1, 1990, 10),
            (1, 1991, 15),
            (2, 1990, 5),
        ]
        result = prior_skater_gp_by_season_for_players(
            session,
            player_ids=[1, 2],
            before_season_year=1992,
            league_ids=(0,),
        )
        self.assertEqual(result[1], [10, 15])
        self.assertEqual(result[2], [5])

    def test_upsert_baseline_creates_and_updates(self) -> None:
        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        row = upsert_baseline(
            session,
            metric_key="goals",
            segment="rs",
            scope="all",
            player_kind="skater",
            value=4.0,
            player_id=1,
        )
        session.add.assert_called_once()
        self.assertEqual(row.metric_key, "goals")
        session.scalars.return_value.first.return_value = row
        upsert_baseline(
            session,
            metric_key="goals",
            segment="rs",
            scope="all",
            player_kind="skater",
            value=5.0,
        )
        self.assertEqual(row.value, 5.0)


class GameRecordsRoutesTest(unittest.TestCase):
    def test_game_records_renders_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            app = create_app(make_league_config(slug))
            with app.test_client() as client:
                r = client.get("/game-records")
                self.assertEqual(r.status_code, 200, slug)
                self.assertIn(b"Game Records", r.data)
                self.assertIn(b"game-records-grid", r.data)

    def test_game_records_filter_tabs(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            r = client.get("/game-records?segment=po&scope=rookie&kind=goalie")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"Playoffs", r.data)
            self.assertIn(b"Rookies", r.data)
            self.assertIn(b"Saves", r.data)

    def test_cap_rookie_skater_game_records_renders(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_client() as client:
            r = client.get("/game-records?scope=rookie&kind=skater")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"Rookies", r.data)
            self.assertIn(b"game-records-grid", r.data)

    def test_build_page_includes_skater_metrics(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            page = build_game_records_page(db.session, segment="rs", scope="all", player_kind="skater")
            titles = {c["title"] for c in page["cards"]}
            self.assertIn("Goals", titles)
            self.assertIn("Points", titles)
            self.assertIn("PP Goals", titles)

    def test_cap_game_record_card_uses_canadiens_era_logo_for_1992_montreal(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_request_context("/game-records?segment=po"):
            from app.services.season_team_logo_bundle import get_season_team_logo_bundle

            team = db.session.scalar(select(Team).where(Team.fhm_team_id == "0").limit(1))
            if team is None:
                self.skipTest("missing Montreal team in cap test db")
            card = {
                "team": team,
                "season_year": 1992,
                "season_year_label": "1992-93",
            }
            logo_url = get_season_team_logo_bundle(app).season_team_logo_url(card)

            self.assertIsNotNone(logo_url)
            self.assertIn("montreal_canadiens_1956-1998.png", logo_url)
            self.assertNotIn("wanderers", logo_url.lower())


class GameRecordsBaselineIntegrationTest(unittest.TestCase):
    def test_resolve_uses_baseline_when_no_boxscore_beats_it(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            db.session.query(GameRecordBaseline).delete()
            player = db.session.scalar(select(Player).limit(1))
            if player is None:
                self.skipTest("no players in test db")
            upsert_baseline(
                db.session,
                metric_key="goals",
                segment="rs",
                scope="all",
                player_kind="skater",
                value=99.0,
                player_id=int(player.id),
            )
            db.session.commit()
            metric = GameRecordMetric("goals", "Goals", "skater")
            holder = resolve_game_record(db.session, metric, "rs", "all")
            self.assertIsNotNone(holder)
            self.assertGreaterEqual(holder.value or 0, 99.0)
            db.session.query(GameRecordBaseline).delete()
            db.session.commit()


class GameRecordsBaselineSyncTest(unittest.TestCase):
    def test_sync_promotes_boxscore_leaders_and_never_downgrades(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            if db.session.query(GameSkaterStat).count() == 0:
                self.skipTest("no boxscore rows in test db (re-import FHM boxscores to exercise sync)")
            db.session.query(GameRecordBaseline).delete()
            db.session.commit()

            promoted = sync_game_record_baselines(db.session)
            self.assertGreater(promoted, 0, "expected historical boxscores to seed baselines")
            db.session.commit()

            goals_row = db.session.scalar(
                select(GameRecordBaseline).where(
                    GameRecordBaseline.metric_key == "goals",
                    GameRecordBaseline.segment == "rs",
                    GameRecordBaseline.scope == "all",
                    GameRecordBaseline.player_kind == "skater",
                )
            )
            self.assertIsNotNone(goals_row)
            self.assertIsNotNone(goals_row.player_id)
            self.assertGreater(goals_row.value, 0)

            promoted_again = sync_game_record_baselines(db.session)
            self.assertEqual(promoted_again, 0, "second sync should not rewrite unchanged baselines")

            upsert_baseline(
                db.session,
                metric_key="goals",
                segment="rs",
                scope="all",
                player_kind="skater",
                value=1.0,
                player_id=int(goals_row.player_id),
            )
            db.session.commit()

            metric = GameRecordMetric("goals", "Goals", "skater")
            holder = resolve_game_record(db.session, metric, "rs", "all")
            self.assertIsNotNone(holder)
            self.assertGreater(holder.value or 0, 1.0)

            sync_game_record_baselines(db.session)
            db.session.commit()
            refreshed = db.session.scalar(
                select(GameRecordBaseline).where(
                    GameRecordBaseline.metric_key == "goals",
                    GameRecordBaseline.segment == "rs",
                    GameRecordBaseline.scope == "all",
                    GameRecordBaseline.player_kind == "skater",
                )
            )
            self.assertIsNotNone(refreshed)
            self.assertGreater(refreshed.value, 1.0)

            db.session.query(GameRecordBaseline).delete()
            db.session.commit()


class GameRecordsSeasonResetTest(unittest.TestCase):
    def test_manual_baselines_survive_boxscore_wipe_for_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            with self.subTest(league=slug):
                app = create_app(make_league_config(slug))
                with app.app_context():
                    db.session.query(GameRecordBaseline).delete()
                    player = db.session.scalar(select(Player).limit(1))
                    if player is None:
                        self.skipTest(f"no players in {slug} test db")
                    upsert_baseline(
                        db.session,
                        metric_key="goals",
                        segment="rs",
                        scope="all",
                        player_kind="skater",
                        value=99.0,
                        player_id=int(player.id),
                        notes=MANUAL_BASELINE_NOTE,
                    )
                    db.session.commit()

                    metric = GameRecordMetric("goals", "Goals", "skater")
                    with patch("app.services.game_records._computed_game_record", return_value=None):
                        holder = resolve_game_record(db.session, metric, "rs", "all")
                        self.assertIsNotNone(holder)
                        self.assertEqual(holder.value, 99.0)
                        self.assertEqual(holder.player.id, int(player.id))

                        promoted = sync_game_record_baselines(db.session)
                        self.assertEqual(
                            promoted,
                            0,
                            f"sync must not change manual baselines without a game log ({slug})",
                        )

                    row = db.session.scalar(
                        select(GameRecordBaseline).where(
                            GameRecordBaseline.metric_key == "goals",
                            GameRecordBaseline.segment == "rs",
                            GameRecordBaseline.scope == "all",
                            GameRecordBaseline.player_kind == "skater",
                        )
                    )
                    self.assertIsNotNone(row)
                    self.assertEqual(row.value, 99.0)
                    self.assertEqual(row.notes, MANUAL_BASELINE_NOTE)

                    db.session.query(GameRecordBaseline).delete()
                    db.session.commit()


if __name__ == "__main__":
    unittest.main()

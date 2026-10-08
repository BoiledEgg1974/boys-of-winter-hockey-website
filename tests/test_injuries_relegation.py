"""Injury classification and Discord delta helpers."""
from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import discord_injury_report_channel_id
from app.services.injuries import classify_injury_status
from app.services.injury_discord import diff_injury_snapshots, maybe_enqueue_injury_report_delta
from scripts.league_discord_bot.formatters import format_discord_messages


class InjuryStatusTests(unittest.TestCase):
    def test_short_max_days_is_day_to_day(self) -> None:
        self.assertEqual(classify_injury_status(5, 3, 5), "day_to_day")

    def test_long_recovery_is_out(self) -> None:
        self.assertEqual(classify_injury_status(200, 90, 160), "out")

    def test_diff_detects_add_and_remove(self) -> None:
        prev = [{"player_id": 1, "team_id": 2, "injury_name": "Concussion", "recovery_days": 10}]
        cur = [{"player_id": 2, "team_id": 3, "injury_name": "Sprain", "recovery_days": 7}]
        delta = diff_injury_snapshots(prev, cur)
        self.assertEqual(len(delta["added"]), 1)
        self.assertEqual(len(delta["removed"]), 1)
        self.assertEqual(delta["added"][0]["player_id"], 2)


class InjuryDiscordFormatterTest(unittest.TestCase):
    def test_active_list_renders_players(self) -> None:
        parts = format_discord_messages(
            {
                "event_key": "injury_report_delta",
                "league_slug": "bowl-fantasy",
                "payload": {
                    "title": "BLUP / BLOW injury report",
                    "active": [
                        {
                            "player_name": "Patrik Laine",
                            "team_abbr": "MTL",
                            "injury_name": "Knee sprain",
                            "status_label": "Day-to-day",
                            "recovery_days": 5,
                        }
                    ],
                },
            },
            max_parts=1,
        )
        content = str(parts[0].get("content") or "")
        self.assertIn("Patrik Laine (MTL)", content)
        self.assertIn("Knee sprain", content)

    def test_empty_active_renders_none(self) -> None:
        parts = format_discord_messages(
            {
                "event_key": "injury_report_delta",
                "league_slug": "bowl-fantasy",
                "payload": {"title": "BLUP / BLOW injury report", "active": []},
            },
            max_parts=1,
        )
        content = str(parts[0].get("content") or "")
        self.assertIn("None", content)


class InjuryDiscordEnqueueTest(unittest.TestCase):
    def test_first_import_with_no_injuries_posts_none(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        with patch(
            "app.services.injury_discord._snapshot_rows",
            return_value=[],
        ), patch(
            "app.services.discord_events.enqueue_discord_event",
            return_value=object(),
        ) as enqueue:
            ok = maybe_enqueue_injury_report_delta(session, "bowl-fantasy")
        self.assertTrue(ok)
        enqueue.assert_called_once()
        payload = enqueue.call_args.kwargs["payload"]
        self.assertEqual(payload["active"], [])
        self.assertEqual(payload["total_active"], 0)

    def test_unchanged_snapshot_skips_enqueue(self) -> None:
        import json

        from app.site_models import InjuryImportSnapshot

        snap = InjuryImportSnapshot(
            league_slug="bowl-fantasy",
            snapshot_json=json.dumps([{"player_id": 1, "injury_name": "X"}]),
        )
        session = MagicMock()
        session.scalar.return_value = snap
        with patch(
            "app.services.injury_discord._snapshot_rows",
            return_value=[{"player_id": 1, "injury_name": "X"}],
        ), patch(
            "app.services.discord_events.enqueue_discord_event",
        ) as enqueue:
            ok = maybe_enqueue_injury_report_delta(session, "bowl-fantasy")
        self.assertFalse(ok)
        enqueue.assert_not_called()


class InjuryImportTierFilterTests(unittest.TestCase):
    def test_import_skips_non_blup_blow_team_injuries(self) -> None:
        from app import create_app
        from app.config import make_league_config
        from app.models import Player, PlayerInjury, Team, db
        from scripts.import_pipeline.fhm_loader import import_injuries

        app = create_app(make_league_config("bowl-fantasy"))
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "league_data.csv").write_text(
                "LeagueId;Name;Abbr\n0;BOWL-Upper;BLUP\n1;BOWL-Lower;BLOW\n2;AHL;AHL\n",
                encoding="utf-8",
            )
            (raw / "injuries_data.csv").write_text(
                "Injury Id;Name;Min Days;Max Days\n5;Sprained Knee;7;14\n",
                encoding="utf-8",
            )
            (raw / "player_injuries.csv").write_text(
                "PlayerId;Team Id;Franchise Id;Injury Id;Recovery Time\n"
                "101;10;10;5;10\n"
                "102;20;20;5;12\n",
                encoding="utf-8",
            )
            with app.app_context():
                db.create_all()
                suffix = uuid.uuid4().hex[:8]
                blup = Team(
                    fhm_team_id=f"inj-blup-{suffix}",
                    fhm_league_id=0,
                    slug=f"blup-t-{suffix}",
                    abbreviation="BLU",
                    name="BLUP Club",
                )
                ahl = Team(
                    fhm_team_id=f"inj-ahl-{suffix}",
                    fhm_league_id=2,
                    slug=f"ahl-t-{suffix}",
                    abbreviation="AHL",
                    name="Farm Club",
                )
                p1 = Player(
                    fhm_player_id=f"inj-p1-{suffix}",
                    first_name="A",
                    last_name="One",
                    full_name="A One",
                )
                p2 = Player(
                    fhm_player_id=f"inj-p2-{suffix}",
                    first_name="B",
                    last_name="Two",
                    full_name="B Two",
                )
                db.session.add_all([blup, ahl, p1, p2])
                db.session.commit()
                n = import_injuries(raw, {101: p1.id, 102: p2.id}, {10: blup.id, 20: ahl.id})
                self.assertEqual(n, 1)
                rows = list(db.session.scalars(db.select(PlayerInjury)))
                self.assertEqual(len(rows), 1)
                self.assertEqual(int(rows[0].team_id), int(blup.id))


class InjuryDiscordSnapshotFilterTests(unittest.TestCase):
    def test_snapshot_excludes_farm_team_injuries(self) -> None:
        from app.services.injury_discord import _snapshot_rows

        session = MagicMock()
        with patch(
            "app.services.injuries.blup_blow_injury_team_ids",
            return_value=frozenset({5}),
        ), patch(
            "app.services.injuries.injury_payload_league_wide",
            return_value=[
                {"player_id": 1, "team_id": 5, "player_name": "Main", "team_abbr": "MTL"},
                {"player_id": 2, "team_id": 99, "player_name": "Farm", "team_abbr": "AHL"},
            ],
        ):
            rows = _snapshot_rows(session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player_id"], 1)


class InjuryDiscordChannelConfigTest(unittest.TestCase):
    def test_default_relegation_injury_channel(self) -> None:
        self.assertEqual(
            discord_injury_report_channel_id("bowl-fantasy"),
            "1209648773307957258",
        )


if __name__ == "__main__":
    unittest.main()

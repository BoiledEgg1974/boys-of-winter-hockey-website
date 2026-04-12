import csv
import unittest
from pathlib import Path

from app import create_app
from app.config import LEAGUES, make_league_config
from app.models import Player, Team


def _read_semicolon_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f, delimiter=";"))
        except UnicodeDecodeError:
            continue
    return []


class DepthChartOrgGuardTests(unittest.TestCase):
    def test_cross_team_line_players_do_not_render_on_depth_page(self) -> None:
        for league in LEAGUES:
            app = create_app(make_league_config(league.slug))
            with app.app_context():
                csv_path = Path(app.config["RAW_IMPORT_DIR"]) / "team_lines.csv"
                rows = _read_semicolon_rows(csv_path)
                if not rows:
                    continue

                teams = {
                    str(t.fhm_team_id): t
                    for t in Team.query.all()
                    if t.fhm_team_id is not None and str(t.fhm_team_id).strip()
                }
                players = {
                    str(p.fhm_player_id): p
                    for p in Player.query.all()
                    if p.fhm_player_id is not None and str(p.fhm_player_id).strip()
                }

                league_cases: list[tuple[str, str]] = []
                for row in rows:
                    team_fhm = (row.get("TeamId") or row.get("teamid") or "").strip()
                    if not team_fhm:
                        continue
                    team = teams.get(team_fhm)
                    if not team:
                        continue
                    for val in row.values():
                        pid = str(val or "").strip()
                        if not pid.isdigit():
                            continue
                        pl = players.get(pid)
                        if not pl or pl.current_team_id is None:
                            continue
                        if pl.current_team_id != team.id:
                            league_cases.append((team.slug, pl.full_name))

                if not league_cases:
                    continue

                with app.test_client() as client:
                    for team_slug, player_name in league_cases:
                        resp = client.get(f"/team/{team_slug}?panel=depth")
                        self.assertEqual(
                            resp.status_code,
                            200,
                            f"{league.slug}: depth page should load for {team_slug}",
                        )
                        html = resp.get_data(as_text=True)
                        self.assertNotIn(
                            player_name,
                            html,
                            f"{league.slug}: out-of-org player {player_name} leaked into {team_slug} depth page",
                        )

        # Clean imports often have no line/roster mismatches; that is success. When mismatches
        # exist, the loop above still asserts they do not appear on the depth panel HTML.


if __name__ == "__main__":
    unittest.main()

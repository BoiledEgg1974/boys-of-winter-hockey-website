# Team Season Records Template Notes

These notes apply to:

- `bowl_historical/team_season_records_template.csv`
- `bowl_fantasy/team_season_records_template.csv`
- `bowl_cap/team_season_records_template.csv`

## Optional override columns

- `Team Name Override`
  - Use this when `Team ID` does not map to a row in `team_data.csv`.
  - If `Team ID` is found, this can be left blank.
- `Logo File Override`
  - Optional static path (relative to `app/static/`) for a custom logo.
  - Example: `logos/teams/custom/mtl_1993.png`
  - If `Team ID` resolves normally and no override is set, the standard team logo is used.

## Suggested workflow for custom logos

1. Add logo files under `app/static/logos/teams/custom/` (or another static subfolder).
2. Set `Logo File Override` to the file path relative to `app/static/`.
3. Use `Team Name Override` when needed for unresolved team IDs.

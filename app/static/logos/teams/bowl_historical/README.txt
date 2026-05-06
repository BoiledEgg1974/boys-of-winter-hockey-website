Team logos - BOWL-Historical
============================

Place all team logos for BOWL-Historical in this folder.

You can also put files under data/imports/raw/bowl_historical/team_logos/ (or logos/);
running import_data.py with LEAGUE_SLUG=bowl-historical copies them here automatically.

Naming convention (same as historical site):
- filename = team slug from database
- extensions supported: .png, .webp, .jpg, .svg

Example:
- wolves.png
- new_york_rangers.svg

If a team logo is missing, placeholder.svg is used.

Year-based logos (timeline filenames)
-------------------------------------
Use when the same franchise uses different marks in different eras. The app maps the file to
every **season start year** from START through END (inclusive).

Pattern (stem before extension):

  <normalized_team_name>_<START>-<END>
  <normalized_team_name>_<START>-present
  <normalized_team_name>_<YEAR>        (single season start year only)

- Use lowercase, words separated by underscores (hyphens in the name become spaces when matched).
- START, END, and YEAR are four-digit years (season **start** year, e.g. 2013 for 2013-14).
- **present** means “still current”: treated as through year **2100** (same convention as
  ``team_identity_history.csv``). Prefer this over guessing an end year.

Resolution on the site uses the row’s **season start year** and team name / FHM id (see
``season_team_logo_url``): ``team_identity_history.csv`` and per-year id overrides win first,
then **name + year** timeline files, then ``*-t<id>`` files, then static name fallbacks.

If two files map the same team name and year, whichever is registered last during folder scan
wins—avoid overlapping ranges for the same mark when possible.

Examples:

  anaheim_ducks_2013-present.png     -> Anaheim Ducks logo from 2013-14 onward
  montreal_maroons_1924.png          -> Maroons logo for season start 1924 only
  pit-t122.png  -> Penguins mark tied to FHM team id (also used for 1968-71 in team_identity_history.csv)

Prefer ``team_identity_history.csv`` in ``data/imports/raw/<league>/`` when you need to tie a
logo to **FHM team_id** or an exact display name; use timeline filenames for quick name+year assets.

Supported extensions: .png, .webp, .jpg, .jpeg, .svg (same as above).

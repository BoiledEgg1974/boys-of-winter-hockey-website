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

- Use lowercase, words separated by underscores (hyphens in the name become spaces when matched).
- START and END are four-digit years (season **start** year, e.g. 2013 for 2013-14).
- **present** means “still current”: treated as through year **2100** (same convention as
  ``team_identity_history.csv``). Prefer this over guessing an end year.

Examples:

  anaheim_ducks_2013-present.png     -> Anaheim Ducks logo from 2013-14 onward
  pit-t122.png  -> Penguins mark tied to FHM team id (also used for 1968-71 in team_identity_history.csv)

Prefer ``team_identity_history.csv`` in ``data/imports/raw/<league>/`` when you need to tie a
logo to **FHM team_id** or an exact display name; use timeline filenames for quick name+year assets.

Supported extensions: .png, .webp, .jpg, .jpeg, .svg (same as above).

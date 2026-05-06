Team logos - BOWL-Fantasy
=========================

Place all team logos for BOWL-Fantasy in this folder.

Naming convention (same as historical site):
- filename = team slug from database
- extensions supported: .png, .webp, .jpg, .svg

Example:
- wolves.png
- new_york_rangers.svg

If a team logo is missing, placeholder.svg is used.

Year-based timeline filenames (optional): ``<team_name>_<start>-<end>.ext`` or
``<team_name>_<start>-present.ext`` (``present`` = through season year 2100). See
``bowl_historical/README.txt`` for the full convention. Per-year overrides also use
``data/imports/raw/bowl_fantasy/team_identity_history.csv``. Rows use the same
``team_fhm_id`` for the whole franchise; each relocation segment has
``start_year``/``end_year``, ``team_name``, and ``logo_file``. Where no era PNG
exists yet, the CSV points at ``placeholder.svg``—drop in a matching file and
update the CSV ``logo_file`` cell to use it.

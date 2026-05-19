Team logos - BOWL-Fantasy
=========================

Place all team logos for BOWL-Fantasy in this folder.

Naming convention:
- Canonical mapping: ``app/logo_urls.py`` → ``FANTASY_ROSTER_LOGO_FILES`` (slug → filename).
- Many teams use descriptive filenames (e.g. ``fort_wayne_komets.png`` for ``fw-t26``); others
  use slug filenames (e.g. ``tor-t3.png``).
- Era / history rows: ``data/imports/raw/bowl_fantasy/team_identity_history.csv``.
- extensions: .png, .webp, .jpg, .svg

If a team logo is missing, placeholder.svg is used.

Year-based timeline filenames (optional): ``<team_name>_<start>-<end>.ext`` or
``<team_name>_<start>-present.ext`` (``present`` = through season year 2100). See
``bowl_historical/README.txt`` for the full convention. Per-year overrides also use
``data/imports/raw/bowl_fantasy/team_identity_history.csv``. Rows use the same
``team_fhm_id`` for the whole franchise; each relocation segment has
``start_year``/``end_year``, ``team_name``, and ``logo_file``. Use the exact
filename in this folder (e.g. ``london_black_knights.png``, ``vcr-t280.png`` for
current Vancouver Giants, ``vic-t16.png`` for Victoria Royals,
``fla-t21.png`` for Florida Party Animals). For eras without a bespoke asset,
point ``logo_file`` at ``placeholder.svg`` or add a PNG and update the CSV.

"""One-off: download NHL-style team logos as PNG into ``app/static/logos/teams/bowl_cap/``.

Uses Wikimedia APIs (commons + en.wikipedia) with a per-team fallback list of ``File:`` titles.
Run: PYTHONPATH=. python scripts/_fetch_cap_logos.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
UA = "BoysOfWinterLeague/1.0 (https://github.com/BoiledEgg1974/boys-of-winter-hockey-website; educational)"

# Primary titles are usually en.wikipedia infobox SVGs; fallbacks for renames / Coyotes / etc.
ABBREV_TO_FILES: dict[str, list[str]] = {
    "ANA": ["File:Anaheim Ducks.svg", "File:Mighty Ducks of Anaheim.svg"],
    "BOS": ["File:Boston Bruins.svg"],
    "BUF": ["File:Buffalo Sabres.svg"],
    "CAR": ["File:Carolina Hurricanes.svg"],
    "CGY": ["File:Calgary Flames.svg"],
    "CHI": ["File:Chicago Blackhawks.svg"],
    "COL": ["File:Colorado Avalanche.svg"],
    "DAL": ["File:Dallas Stars.svg"],
    "DET": ["File:Detroit Red Wings logo.svg"],
    "EDM": ["File:Edmonton Oilers.svg"],
    "FLA": ["File:Florida Panthers.svg", "File:Florida Panthers 2016.svg"],
    "LAK": ["File:Los Angeles Kings.svg", "File:LA Kings wordmark logo.svg"],
    "MTL": ["File:Montreal Canadiens.svg"],
    "NAS": ["File:Nashville Predators.svg"],
    "NJD": ["File:New Jersey Devils.svg"],
    "NYI": ["File:New York Islanders.svg"],
    "NYR": ["File:New York Rangers.svg"],
    "OTT": ["File:Ottawa Senators.svg", "File:Ottawa Senators 1997-2007 logo.svg"],
    "PHI": ["File:Philadelphia Flyers.svg"],
    "PHX": ["File:Phoenix Coyotes.svg", "File:Arizona Coyotes.svg"],
    "PIT": ["File:Pittsburgh Penguins.svg"],
    "SJS": ["File:San Jose Sharks.svg"],
    "STL": ["File:St. Louis Blues.svg"],
    "TBL": ["File:Tampa Bay Lightning.svg"],
    "TOR": ["File:Toronto Maple Leafs 2016 logo.svg", "File:Toronto Maple Leafs.svg"],
    "VAN": ["File:Vancouver Canucks.svg"],
    "WAS": ["File:Washington Capitals.svg"],
}


def _thumb_from_api(site: str, title: str) -> str | None:
    base = f"https://{site}/w/api.php"
    q = base + "?" + urllib.parse.urlencode(
        {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "256",
            "format": "json",
        }
    )
    req = urllib.request.Request(q, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        j = json.load(r)
    pages = j["query"]["pages"]
    p = next(iter(pages.values()))
    if p.get("missing") or p.get("invalid"):
        return None
    ii = (p.get("imageinfo") or [{}])[0]
    return ii.get("thumburl") or ii.get("url")


def _first_thumb(title: str) -> str | None:
    for site in ("en.wikipedia.org", "commons.wikimedia.org"):
        u = _thumb_from_api(site, title)
        if u:
            return u
    return None


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
        f.write(r.read())


def main() -> None:
    out_dir = _REPO / "app" / "static" / "logos" / "teams" / "bowl_cap"
    out_dir.mkdir(parents=True, exist_ok=True)
    db = _REPO / "instance" / "league3.db"
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT slug, abbreviation FROM teams ORDER BY slug"
    ).fetchall()
    conn.close()
    for slug, abbrev in rows:
        dest = out_dir / f"{slug}.png"
        if dest.is_file() and dest.stat().st_size > 500:
            print(f"skip existing {slug}.png")
            continue
        titles = ABBREV_TO_FILES.get(abbrev)
        if not titles:
            print(f"no mapping for {abbrev} ({slug})")
            continue
        thumb = None
        for t in titles:
            thumb = _first_thumb(t)
            if thumb:
                print(f"{slug} <- {t}")
                break
            time.sleep(0.15)
        if not thumb:
            print(f"FAILED {slug} {abbrev} tried {titles}")
            continue
        try:
            _download(thumb, dest)
            print(f"  wrote {dest.name} ({dest.stat().st_size} bytes)")
        except Exception as e:
            print(f"  download error {slug}: {e}")
        time.sleep(0.25)


if __name__ == "__main__":
    main()

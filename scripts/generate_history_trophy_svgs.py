"""Write stylized placeholder trophy SVGs under ``app/static/img/history/trophies/bowl-fantasy/``.

Filenames match :func:`app.routes.main._slugify_award_key` stems (e.g. ``hart_trophy.svg``).
Replace these with real artwork anytime; keep the same stem names.

Run from repo root:

  python scripts/generate_history_trophy_svgs.py
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Fallback-only art; league PNGs belong in ``app/static/img/trophies/bowl-fantasy/`` (see readme there).
OUT = ROOT / "app" / "static" / "img" / "history" / "trophies" / "bowl-fantasy"

_AWARD_NAMES: tuple[str, ...] = (
    "ART ROSS TROPHY",
    "RICHARD TROPHY",
    "NORRIS TROPHY",
    "BOURQUE TROPHY",
    "LANGWAY TROPHY",
    "CALDER TROPHY",
    "SELKE TROPHY",
    "VEZINA TROPHY",
    "LADY BYNG TROPHY",
    "CONN SMYTHE TROPHY",
    "HART TROPHY",
    "JACK ADAMS TROPHY",
    "WILLIAM JENNINGS  TROPHY",
    "TED LINDSAY  TROPHY",
    "MASTERTON TROPHY",
    "BOILEDEGG'S TROPHY",
    "PRINCE OF WALES TROPHY",
    "CLARENCE CAMPBELL TROPHY",
    "BOWL CUP TROPHY",
    "JIM GREGORY TROPHY",
    "MARK MESSIER LEADERSHIP AWARD",
    "ROGER CROZIER SAVING GRACE TROPHY",
    "PLUS/MINUS TROPHY",
    "THE MASTERS' GREEN JACKET",
    "BOWL RISING STAR",
)


def slugify_award_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _hues(stem: str) -> tuple[str, str]:
    h = (hash(stem) % 360) / 360.0
    h2 = (h + 0.08) % 1.0
    r1, g1, b1 = colorsys.hls_to_rgb(h, 0.55, 0.45)
    r2, g2, b2 = colorsys.hls_to_rgb(h2, 0.35, 0.35)
    c1 = f"#{int(r1*255):02x}{int(g1*255):02x}{int(b1*255):02x}"
    c2 = f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    return c1, c2


def svg_for_stem(stem: str) -> str:
    c1, c2 = _hues(stem)
    gid = stem.replace("-", "_")[:42]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 88" aria-hidden="true">
  <defs>
    <linearGradient id="g_{gid}" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <ellipse cx="36" cy="28" rx="20" ry="16" fill="url(#g_{gid})" stroke="#64748b" stroke-width="1.2" opacity="0.95"/>
  <path d="M16 28v5q0 11 20 11t20-11v-5" fill="none" stroke="#64748b" stroke-width="1.4" stroke-linecap="round"/>
  <line x1="36" y1="44" x2="36" y2="70" stroke="#64748b" stroke-width="2.2" stroke-linecap="round"/>
  <rect x="18" y="72" width="36" height="10" rx="2" fill="url(#g_{gid})" stroke="#64748b" stroke-width="1.2"/>
</svg>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in _AWARD_NAMES:
        stem = slugify_award_key(name)
        if not stem:
            continue
        path = OUT / f"{stem}.svg"
        path.write_text(svg_for_stem(stem), encoding="utf-8")
        n += 1
    print(f"Wrote {n} SVG trophy placeholders to {OUT}")


if __name__ == "__main__":
    main()

"""One-off: strip studio backdrops from desktop achievement badges into static PNG."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(r"C:\Users\keeno\OneDrive\Desktop\Achievement Badges")
DST = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "achievements"
MAX_SIDE = 512

FILE_TO_KEY = {
    "100PointSkater.png": "century_club",
    "40WinGoalie.png": "forty_win_goalie",
    "4GoalNight.png": "four_goal_night",
    "50GoalScorer.png": "fifty_goals",
    "AGoalieWin.png": "goalie_win",
    "AllNatural.png": "all_natural",
    "Back-to-BackPlayoffShutouts.png": "playoff_shutout_pair",
    "BargainBin.png": "bargain_bin",
    "CalderClub.png": "calder_club",
    "CombackKids.png": "comeback_kids",
    "CupAs8Seed.png": "cup_eight_seed",
    "DraftSteal.png": "draft_steal",
    "ELCLightning.png": "elc_lightning",
    "ExportStreak.png": "export_streak",
    "FightNight.png": "fight_night",
    "game54.png": "game54",
    "GordieHoweHatTrick.png": "gordie_howe",
    "HomeCooking.png": "home_cooking",
    "HomegrownCore.png": "homegrown_core",
    "HomegrownCup.png": "homegrown_cup",
    "HometownRoster.png": "hometown_roster",
    "IronDecade.png": "iron_decade",
    "KidLineEnergy.png": "kid_line_energy",
    "KissFromARose.png": "make_playoffs",
    "LeagueFirstFourGoalNight.png": "league_first_four",
    "LeagueFirstHatTrick.png": "league_first_hat",
    "LeagueFirstShutout.png": "league_first_shutout",
    "Nationalism.png": "nationalism",
    "Nemesis.png": "nemesis",
    "NewTeamWhosThis.png": "new_team_mvp",
    "OnAHeater.png": "on_a_heater",
    "OutracingTheRocket.png": "rocket",
    "OvertimeMerchant.png": "overtime_merchant",
    "PerfectAttendance.png": "perfect_attendance",
    "PlayoffOTHero.png": "playoff_ot_hero",
    "PlayoffSweep.png": "playoff_sweep",
    "PresidentsClub.png": "presidents",
    "RealDynasty.png": "dynasty",
    "RoadWarrior.png": "road_warrior",
    "SpecialTeamsSeason.png": "special_teams_season",
    "StatementWin.png": "statement_win",
    "SweptNotForgotten.png": "swept_not_forgotten",
    "The200Club.png": "two_hundred",
    "TheBender.png": "the_bender",
    "TheGuarantee.png": "guarantee",
    "TheGuranteeRemixed.png": "guarantee_remixed",
    "TheHeist.png": "the_heist",
    "ThePinnacle.png": "pinnacle",
    "TheSeniorTeam.png": "senior_team",
    "ThreeStarSeason.png": "three_star_season",
    "TrueFranchiseManager.png": "true_franchise",
    "Upset.png": "upset",
    "You'reSpecial.png": "youre_special",
    "ZeroToHero.png": "zero_to_hero",
}


def _is_studio_pixel(r: int, g: int, b: int) -> bool:
    mn = min(r, g, b)
    mx = max(r, g, b)
    return mn >= 188 and (mx - mn) <= 36


def _flood_studio_mask(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    studio = (np.minimum(np.minimum(r, g), b) >= 188) & (sat <= 36)
    mask = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()

    def seed(y: int, x: int) -> None:
        if studio[y, x] and not mask[y, x]:
            mask[y, x] = True
            q.append((y, x))

    for x in range(w):
        seed(0, x)
        seed(h - 1, x)
    for y in range(h):
        seed(y, 0)
        seed(y, w - 1)

    while q:
        y, x = q.popleft()
        if y > 0 and studio[y - 1, x] and not mask[y - 1, x]:
            mask[y - 1, x] = True
            q.append((y - 1, x))
        if y + 1 < h and studio[y + 1, x] and not mask[y + 1, x]:
            mask[y + 1, x] = True
            q.append((y + 1, x))
        if x > 0 and studio[y, x - 1] and not mask[y, x - 1]:
            mask[y, x - 1] = True
            q.append((y, x - 1))
        if x + 1 < w and studio[y, x + 1] and not mask[y, x + 1]:
            mask[y, x + 1] = True
            q.append((y, x + 1))
    return mask


def _dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy == 0 and dx == 0:
                continue
            shifted = np.zeros_like(mask)
            ys = slice(max(0, dy), mask.shape[0] + min(0, dy))
            xs = slice(max(0, dx), mask.shape[1] + min(0, dx))
            src_ys = slice(max(0, -dy), mask.shape[0] - max(0, dy))
            src_xs = slice(max(0, -dx), mask.shape[1] - max(0, dx))
            shifted[ys, xs] = mask[src_ys, src_xs]
            out |= shifted
    return out


def remove_studio_background(im: Image.Image) -> Image.Image:
    rgb = np.asarray(im.convert("RGB"))
    bg = _flood_studio_mask(rgb)
    if int(bg.mean() * 100) < 8:
        # Flood failed; fall back to global studio key from the corners.
        r, g, b = rgb[0, 0]
        if _is_studio_pixel(int(r), int(g), int(b)):
            sat = np.maximum(np.maximum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]) - np.minimum(
                np.minimum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]
            )
            bg = (np.minimum(np.minimum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]) >= 200) & (sat <= 28)
    bg = _dilate(bg, 1)
    rgba = np.asarray(im.convert("RGBA")).copy()
    rgba[bg, 3] = 0
    alpha = rgba[:, :, 3]
    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return Image.fromarray(rgba)
    pad = 6
    left = max(0, int(xs.min()) - pad)
    top = max(0, int(ys.min()) - pad)
    right = min(rgba.shape[1], int(xs.max()) + 1 + pad)
    bottom = min(rgba.shape[0], int(ys.max()) + 1 + pad)
    cropped = rgba[top:bottom, left:right]
    out = Image.fromarray(cropped)
    w, h = out.size
    scale = MAX_SIDE / max(w, h)
    if scale < 1:
        out = out.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    return out


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    src_names = {p.name for p in SRC.iterdir() if p.is_file() and p.suffix.lower() == ".png"}
    missing = [name for name in FILE_TO_KEY if name not in src_names]
    extra = sorted(src_names - set(FILE_TO_KEY))
    if missing:
        raise SystemExit(f"Missing source files: {missing}")
    if extra:
        print("Unmapped source files:", extra)
    for name, key in FILE_TO_KEY.items():
        src = SRC / name
        dest = DST / f"{key}.png"
        out = remove_studio_background(Image.open(src))
        out.save(dest, format="PNG", optimize=True)
        print(f"{name} -> {dest.name} {out.size} {dest.stat().st_size}")


if __name__ == "__main__":
    main()

"""Build small placeholder looping GIFs under app/static/img/motion/.

Requires Pillow (dev-only): pip install pillow
Regenerate after replacing art, or delete this script once real assets land.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "motion"

PALETTE = {
    "historical": (34, 197, 94),
    "fantasy": (59, 130, 246),
    "cap": (239, 68, 68),
    "formula": (249, 115, 22),
    "demolition": (251, 146, 60),
    "gold": (250, 204, 21),
    "cyan": (34, 211, 238),
}


def _save(frames: list[Image.Image], path: Path, *, duration: int, loop: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quantized = [im.convert("P", palette=Image.Palette.ADAPTIVE, colors=48) for im in frames]
    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=duration,
        loop=loop,
        optimize=True,
        disposal=2,
    )


def _swirl_overlay(color: tuple[int, int, int], n: int = 12, size: int = 220) -> list[Image.Image]:
    frames = []
    cx = cy = size / 2
    spark = (255, 255, 255)
    for i in range(n):
        im = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(im)
        t = i / n
        ring = size * (0.42 + 0.03 * math.sin(t * math.tau))
        d.ellipse(
            (cx - ring, cy - ring, cx + ring, cy + ring),
            outline=color,
            width=5,
        )
        inner = ring - 14
        d.ellipse(
            (cx - inner, cy - inner, cx + inner, cy + inner),
            outline=tuple(min(255, c + 80) for c in color),
            width=2,
        )
        for k in range(14):
            ang = t * math.tau + k * (math.tau / 14)
            rad = size * 0.46
            x = cx + math.cos(ang) * rad
            y = cy + math.sin(ang) * rad
            r = 7 if k % 2 == 0 else 4
            d.ellipse((x - r, y - r, x + r, y + r), fill=spark if k % 2 else color)
        for k in range(8):
            ang = -t * math.tau * 1.6 + k * (math.tau / 8)
            rad = size * 0.33
            x = cx + math.cos(ang) * rad
            y = cy + math.sin(ang) * rad
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=spark)
        frames.append(im)
    return frames


def _checkered(n: int = 12, size: int = 220) -> list[Image.Image]:
    """Checkered ring only — center stays black so the splash logo shows through."""
    frames = []
    cell = 14
    orange = PALETTE["formula"]
    band = 28
    for i in range(n):
        im = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(im)
        shift = int(i * 3)
        for y in range(0, size, cell):
            for x in range(-cell, size + cell, cell):
                on_edge = x + shift < band or x + shift > size - band - cell or y < band or y > size - band - cell
                if not on_edge:
                    continue
                xx = x + shift
                if ((xx // cell) + (y // cell)) % 2 == 0:
                    d.rectangle((xx, y, xx + cell - 1, y + cell - 1), fill=(236, 236, 236))
        d.ellipse((band + 6, band + 6, size - band - 7, size - band - 7), outline=orange, width=7)
        frames.append(im)
    return frames


def _sparks(n: int = 12, size: int = 220, seed: int = 7) -> list[Image.Image]:
    rng = random.Random(seed)
    particles = [
        {
            "x": rng.uniform(40, size - 40),
            "y": rng.uniform(size * 0.55, size - 20),
            "vx": rng.uniform(-3.2, 3.2),
            "vy": rng.uniform(-9.0, -4.0),
            "life": rng.randint(4, n),
        }
        for _ in range(28)
    ]
    frames = []
    amber = PALETTE["demolition"]
    for i in range(n):
        im = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((size * 0.32, size * 0.58, size * 0.68, size * 0.92), fill=(40, 12, 0))
        for p in particles:
            age = (i + p["life"]) % n
            x = p["x"] + p["vx"] * age
            y = p["y"] + p["vy"] * age + 0.35 * age * age
            r = 5 if age < n // 2 else 3
            col = amber if age % 2 == 0 else (255, 220, 120)
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        frames.append(im)
    return frames


def _celebrate(n: int = 16, size: int = 240) -> list[Image.Image]:
    rng = random.Random(3)
    bits = [
        {
            "x": rng.uniform(30, size - 30),
            "vx": rng.uniform(-4, 4),
            "color": rng.choice(
                [PALETTE["gold"], PALETTE["cyan"], (248, 250, 252), PALETTE["historical"], PALETTE["fantasy"]]
            ),
            "w": rng.randint(4, 8),
        }
        for _ in range(36)
    ]
    frames = []
    gold = PALETTE["gold"]
    cx = cy = size / 2
    for i in range(n):
        im = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(im)
        t = i / max(1, n - 1)
        ring = 18 + t * 78
        fade = int(180 * (1 - t))
        d.ellipse(
            (cx - ring, cy - ring, cx + ring, cy + ring),
            outline=(gold[0], gold[1], min(255, gold[2] + fade // 4)),
            width=5,
        )
        cup_w = 28
        d.rectangle((cx - cup_w / 2, cy - 8, cx + cup_w / 2, cy + 22), fill=gold)
        d.polygon(
            [(cx - cup_w / 2, cy - 8), (cx + cup_w / 2, cy - 8), (cx, cy - 28)],
            fill=gold,
        )
        d.rectangle((cx - 6, cy + 22, cx + 6, cy + 40), fill=gold)
        d.rectangle((cx - 18, cy + 40, cx + 18, cy + 48), fill=gold)
        for p in bits:
            x = p["x"] + p["vx"] * i
            y = 20 + i * 9 + (p["w"] % 5)
            d.rectangle((x, y, x + p["w"], y + p["w"] + 2), fill=p["color"])
        frames.append(im)
    return frames


def _talk(n: int = 8, size: int = 128) -> list[Image.Image]:
    frames = []
    cyan = PALETTE["cyan"]
    for i in range(n):
        im = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(im)
        for b, phase in enumerate((0, 1.2, 2.1, 0.6)):
            h = 18 + 28 * abs(math.sin(i / n * math.tau + phase))
            x0 = 28 + b * 20
            y0 = size - 18 - h
            d.rectangle((x0, y0, x0 + 12, size - 16), fill=cyan)
        d.ellipse((48, 28, 80, 60), outline=cyan, width=3)
        frames.append(im)
    return frames


def _flag_banner(n: int = 12, w: int = 320, h: int = 140) -> list[Image.Image]:
    frames = []
    cell = 16
    for i in range(n):
        im = Image.new("RGB", (w, h), (0, 0, 0))
        d = ImageDraw.Draw(im)
        for y in range(0, h, cell):
            wave = int(6 * math.sin((y / h) * math.tau + i / n * math.tau))
            for x in range(-cell, w + cell, cell):
                xx = x + wave
                if ((x // cell) + (y // cell)) % 2 == 0:
                    d.rectangle((xx, y, xx + cell - 1, y + cell - 1), fill=(236, 236, 236))
        d.rectangle((0, 0, 10, h), fill=PALETTE["formula"])
        frames.append(im)
    return frames


def _wreck_banner(n: int = 12, w: int = 320, h: int = 140) -> list[Image.Image]:
    rng = random.Random(11)
    sparks = [(rng.uniform(40, w - 40), rng.uniform(40, h - 20), rng.uniform(-2, 2), rng.uniform(-5, -1)) for _ in range(40)]
    frames = []
    for i in range(n):
        im = Image.new("RGB", (w, h), (8, 4, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((w * 0.28, h * 0.35, w * 0.72, h * 0.95), fill=(60, 18, 0))
        for sx, sy, vx, vy in sparks:
            x = sx + vx * i * 3
            y = sy + vy * i + 0.4 * i * i
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 180, 60) if i % 2 else (255, 240, 180))
        frames.append(im)
    return frames


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    _save(_swirl_overlay(PALETTE["historical"]), ROOT / "splash-historical.gif", duration=90, loop=0)
    _save(_swirl_overlay(PALETTE["fantasy"]), ROOT / "splash-fantasy.gif", duration=90, loop=0)
    _save(_swirl_overlay(PALETTE["cap"]), ROOT / "splash-cap.gif", duration=90, loop=0)
    _save(_checkered(), ROOT / "splash-formula.gif", duration=80, loop=0)
    _save(_sparks(), ROOT / "splash-demolition.gif", duration=80, loop=0)
    _save(_celebrate(), ROOT / "moment-celebrate.gif", duration=90, loop=1)
    _save(_talk(), ROOT / "trade-bot-talk.gif", duration=90, loop=0)
    _save(_flag_banner(), ROOT / "racing-formula.gif", duration=90, loop=0)
    _save(_wreck_banner(), ROOT / "racing-demolition.gif", duration=90, loop=0)
    for p in sorted(ROOT.glob("*.gif")):
        print(f"{p.name:28} {p.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()

"""Build the BOWL-Relegation league overview GM guide PDF."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "BOWL-Relegation-GM-Guide.pdf"

NAVY = HexColor("#071a33")
BLUE = HexColor("#005da6")
SKY = HexColor("#e5f1fb")
INK = HexColor("#0f172a")
MUTED = HexColor("#475569")
LINE = HexColor("#cbd5e1")
LIME = HexColor("#a4c639")

W, H = letter
LEFT = 0.7 * inch
RIGHT = W - 0.7 * inch
CONTENT_W = RIGHT - LEFT


def _register_fonts() -> tuple[str, str]:
    arial = Path(r"C:\Windows\Fonts\arial.ttf")
    arial_bd = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if arial.is_file() and arial_bd.is_file():
        pdfmetrics.registerFont(TTFont("Guide", str(arial)))
        pdfmetrics.registerFont(TTFont("Guide-Bold", str(arial_bd)))
        return "Guide", "Guide-Bold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_B = _register_fonts()


def draw_header_bar(c: Canvas, title: str) -> None:
    c.setFillColor(NAVY)
    c.rect(0, H - 0.48 * inch, W, 0.48 * inch, fill=1, stroke=0)
    c.setFillColor(LIME)
    c.rect(0, H - 0.52 * inch, W, 0.04 * inch, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(FONT_B, 10)
    c.drawString(LEFT, H - 0.32 * inch, "BOWL-Relegation")
    c.setFont(FONT, 9)
    c.drawRightString(RIGHT, H - 0.32 * inch, title)


def draw_footer(c: Canvas, page: int, total: int) -> None:
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(LEFT, 0.48 * inch, RIGHT, 0.48 * inch)
    c.setFillColor(MUTED)
    c.setFont(FONT, 8)
    c.drawString(LEFT, 0.32 * inch, "For GMs  ·  September 2026")
    c.drawRightString(RIGHT, 0.32 * inch, f"{page} / {total}")


def wrapped_text(
    c: Canvas,
    text: str,
    x: float,
    y: float,
    max_w: float,
    font: str,
    size: int,
    leading: float,
    color=INK,
) -> float:
    c.setFont(font, size)
    c.setFillColor(color)
    words = text.split()
    line = ""
    while words:
        trial = (line + " " + words[0]).strip()
        if c.stringWidth(trial, font, size) <= max_w:
            line = trial
            words.pop(0)
        else:
            if not line:
                line = words.pop(0)
            c.drawString(x, y, line)
            y -= leading
            line = ""
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def bullet(c: Canvas, text: str, x: float, y: float, max_w: float) -> float:
    c.setFillColor(BLUE)
    c.circle(x + 3, y + 3, 2.2, fill=1, stroke=0)
    return wrapped_text(c, text, x + 12, y, max_w - 12, FONT, 10, 13, INK)


def section_title(c: Canvas, y: float, text: str) -> float:
    c.setFillColor(INK)
    c.setFont(FONT_B, 16)
    c.drawString(LEFT, y, text)
    return y - 22


def _text_block_height(
    c: Canvas,
    text: str,
    max_w: float,
    font: str,
    size: int,
    leading: float,
) -> float:
    if not text.strip():
        return leading
    words = text.split()
    line = ""
    lines = 1
    while words:
        trial = (line + " " + words[0]).strip()
        if c.stringWidth(trial, font, size) <= max_w:
            line = trial
            words.pop(0)
        else:
            if not line:
                line = words.pop(0)
            lines += 1
            line = ""
    return lines * leading


def draw_table(
    c: Canvas,
    y: float,
    rows: list[tuple[str, str]],
    width: float = 3.8 * inch,
    *,
    col1_width: float | None = None,
) -> float:
    pad = 10
    gap = 10
    c1_w = col1_width if col1_width is not None else (2.5 * inch if width >= 6 * inch else 1.35 * inch)
    c2_w = width - c1_w - pad * 2 - gap
    c1_x = LEFT + pad
    c2_x = c1_x + c1_w + gap
    leading = 13
    size = 10

    row_heights: list[float] = []
    for i, (a, b) in enumerate(rows):
        fa = FONT_B if i == 0 else FONT
        fb = FONT_B if i == 0 else FONT
        ha = _text_block_height(c, a, c1_w, fa, size, leading)
        hb = _text_block_height(c, b, c2_w, fb, size, leading)
        row_heights.append(max(ha, hb, leading) + 6)

    total_h = sum(row_heights) + pad
    top = y
    c.setFillColor(SKY)
    c.roundRect(LEFT, top - total_h, width, total_h, 6, fill=1, stroke=0)

    y_row = top - pad
    for i, (a, b) in enumerate(rows):
        fa = FONT_B if i == 0 else FONT
        fb = FONT_B if i == 0 else FONT
        ca = BLUE if i == 0 else INK
        cb = BLUE if i == 0 else INK
        y1 = wrapped_text(c, a, c1_x, y_row, c1_w, fa, size, leading, ca)
        y2 = wrapped_text(c, b, c2_x, y_row, c2_w, fb, size, leading, cb)
        y_row = min(y1, y2) - 6
    return y_row - 8


def page_cover(c: Canvas) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(0, H - 1.55 * inch, W, 1.55 * inch, fill=1, stroke=0)
    c.setFillColor(LIME)
    c.rect(0, H - 1.62 * inch, W, 0.07 * inch, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(FONT, 11)
    c.drawString(LEFT, H - 0.55 * inch, "BOYS OF WINTER LEAGUE")
    c.setFont(FONT_B, 26)
    c.drawString(LEFT, H - 1.05 * inch, "Promotion & Relegation")
    c.setFont(FONT, 13)
    c.drawString(LEFT, H - 1.38 * inch, "BOWL-Relegation  ·  GM guide")

    y = H - 2.15 * inch
    c.setFillColor(HexColor("#93c5fd"))
    c.setFont(FONT_B, 11)
    c.drawString(LEFT, y, "What this is")
    y -= 18
    y = wrapped_text(
        c,
        "The /bowl-fantasy site — a two-tier BOWL league modeled on European pro hockey. "
        "Clubs sign and transfer players instead of relying on an NHL-style entry draft. "
        "Upper (BLUP) and Lower (BLOW) tables are linked by promotion and relegation after the playoffs.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        11,
        15,
        white,
    )
    y -= 10
    c.setFillColor(HexColor("#93c5fd"))
    c.setFont(FONT_B, 11)
    c.drawString(LEFT, y, "What this is not")
    y -= 18
    y = wrapped_text(
        c,
        "It is not the BOWL-Cap or BOWL-Historical entry-draft workflow. Draft Hub, Draft Eligible, "
        "and draft lottery pages are off on Relegation. Cross-league fees and the Transfer Tool are "
        "covered in the separate Cross-League Transfers GM guide.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        11,
        15,
        white,
    )
    y -= 18
    c.setFillColor(LIME)
    c.roundRect(LEFT, y - 92, CONTENT_W, 86, 8, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont(FONT_B, 11)
    c.drawString(LEFT + 14, y - 22, "At a glance")
    wrapped_text(
        c,
        "Bottom 2 in Upper relegated  ·  Top 2 in Lower promoted  ·  Combined / Upper / Lower tabs on the site  ·  "
        "Signing: Prospects, Signable (18–20), Overseas Transfers, Free Agents  ·  AI transfers via Transfer Tool; "
        "human BLUP/BLOW sales via Transfer Tool (partner approval).",
        LEFT + 14,
        y - 40,
        CONTENT_W - 28,
        FONT,
        10,
        13,
        NAVY,
    )
    c.setFillColor(HexColor("#7dd3fc"))
    c.setFont(FONT, 9)
    c.drawString(LEFT, 0.55 * inch, "Live tiers and standings come from the current FHM CSV import.")


def page_structure(c: Canvas) -> None:
    draw_header_bar(c, "League structure")
    y = section_title(c, H - 0.85 * inch, "Two tiers in FHM")
    y = wrapped_text(
        c,
        "BOWL-Relegation splits competitive play into an upper division and a lower division. "
        "On the website, use Combined, Upper, or Lower scope tabs on standings, team stats, and records.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        10,
        13,
    )
    y -= 6
    y = draw_table(
        c,
        y,
        [
            ("Tier", "Role"),
            ("Upper (BLUP)", "Top division — relegation risk at the bottom"),
            ("Lower (BLOW)", "Second division — promotion chase at the top"),
        ],
    )
    y -= 4
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "League-wide (unchanged by tier)")
    y -= 16
    for item in (
        "One shared player pool and import-driven rosters on the site.",
        "Combined all-time records and league history.",
        "Same salary cap framework as other BOWL mounts.",
    ):
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 4
    y -= 6
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "Tier-scoped on the site")
    y -= 16
    for item in (
        "Standings, schedules, and filtered team pages when you pick Upper or Lower.",
        "After movement, your club plays in BLUP or BLOW in the FHM save.",
    ):
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 4

    y -= 8
    c.setFillColor(SKY)
    c.roundRect(LEFT, y - 58, CONTENT_W, 52, 8, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 10)
    c.drawString(LEFT + 12, y - 18, "Under construction banner")
    wrapped_text(
        c,
        "If Upper / Lower is not live yet, use combined standings until the post–season-reset import "
        "activates separate tiers. Promotion / Relegation tracking goes live with the split.",
        LEFT + 12,
        y - 34,
        CONTENT_W - 24,
        FONT,
        9.5,
        12,
        INK,
    )


def page_movement(c: Canvas) -> None:
    draw_header_bar(c, "Movement")
    y = section_title(c, H - 0.85 * inch, "House rules")
    y = wrapped_text(
        c,
        "Promotion and relegation are decided after the season playoffs, not during the regular season.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        10,
        13,
    )
    y -= 8
    for item in (
        "Relegation: the bottom two teams in the Upper tier move down to the Lower tier.",
        "Promotion: the top two teams in the Lower tier move up to the Upper tier.",
        "During the season, open Promotion / Relegation in the main nav for relegation danger and promotion zone watch lists.",
        "Combined standings show U / L badges for each club’s current tier.",
    ):
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 4

    y -= 8
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "Why the table matters")
    y -= 16
    y = wrapped_text(
        c,
        "Unlike a draft that sends young talent to weak teams, relegation does not redistribute stars for you. "
        "Dropping a tier usually means tighter budgets, harder retention, and a longer climb back — so academy "
        "signings, transfers, and roster planning matter more. The competitive story is in the standings, not lottery odds.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        10,
        13,
    )


def page_roster(c: Canvas) -> None:
    draw_header_bar(c, "Building your roster")
    y = section_title(c, H - 0.85 * inch, "No entry draft on Relegation")
    y = wrapped_text(
        c,
        "Draft History, Draft Eligible, Draft Hub, and the draft lottery are off on /bowl-fantasy. "
        "Add players through scouting boards, signings, trades, and transfer fees — the European club model.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        10,
        13,
    )
    y -= 8
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "Signing pages (main nav)")
    y -= 16
    y = draw_table(
        c,
        y,
        [
            ("Page", "Pool"),
            ("Prospects", "Age 17 and under, not on BLUP/BLOW"),
            ("Signable (18–20)", "Ages 18–20, no BOWL org rights"),
            ("Overseas Transfers", "Transfer-eligible pro rosters + PTA flags"),
            ("Free Agents", "No FHM team; rights holder and fee shown"),
        ],
        width=CONTENT_W,
    )
    y -= 4
    c.setFont(FONT_B, 10)
    c.setFillColor(BLUE)
    c.drawString(LEFT, y, "Transfer-eligible leagues")
    y -= 14
    y = wrapped_text(
        c,
        "BLUP, BLOW, SHL, KHL, DEL, Liiga, NL, and ELH. Juniors, NCAA, and AHL are not in the cross-league transfer workflow.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        10,
        13,
    )
    y -= 8
    for item in (
        "PTA / transfer fees use the $95.5M reference cap and scale with the live cap (same as the Transfer Tool).",
        "KHL: player must be off contract — no PTA path while still under KHL contract.",
        "European UFAs age 22+: $0 fee on the UFA path.",
        "Human GM sellers (including BLUP/BLOW): same Transfer Tool — selling GM accepts on the site; league office publishes.",
    ):
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 4

    y -= 6
    c.setFillColor(MUTED)
    c.setFont(FONT, 9)
    wrapped_text(
        c,
        "Step-by-step Transfer Tool use, proposal statuses, and the full PTA table: "
        "BOWL-Relegation-Transfers-GM.pdf (same docs/guides folder).",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        9,
        12,
        MUTED,
    )


def page_tools(c: Canvas) -> None:
    draw_header_bar(c, "Tools & season checklist")
    y = section_title(c, H - 0.85 * inch, "Which tool when")
    y = draw_table(
        c,
        y,
        [
            ("Goal", "Tool"),
            ("AI external club (SHL, KHL, etc.)", "Transfer Tool, then office publish, then FHM"),
            ("Another human BOWL GM (one player in)", "Transfer Tool (partner approval)"),
            ("Multi-asset trade (picks, both sides)", "Commissioner Trade Tool"),
            ("Update the website roster", "Apply in FHM; site updates on next CSV import"),
        ],
        width=CONTENT_W,
    )
    y -= 4
    y = bullet(
        c,
        "Cross-league transfers: one player in per proposal; cash plus up to two BOWL players; no draft picks.",
        LEFT,
        y,
        CONTENT_W,
    )
    y -= 12
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "Season checklist")
    y -= 16
    steps = (
        "Know your tier (U / L) and whether you are in relegation danger or the promotion zone.",
        "Plan adds through Prospects, Signable, Overseas, and Free Agents — not the entry draft.",
        "Use the Transfer Tool for AI clubs and human BLUP/BLOW sellers; use commissioner trades for balanced multi-asset deals.",
        "After the office publishes a move, execute it in FHM before expecting the site to match.",
        "When movement is applied for the new season, confirm your club is in BLUP or BLOW in FHM.",
    )
    for i, item in enumerate(steps, start=1):
        c.setFont(FONT_B, 10)
        c.setFillColor(BLUE)
        c.drawString(LEFT, y, f"{i}.")
        y = wrapped_text(c, item, LEFT + 16, y, CONTENT_W - 16, FONT, 10, 13, INK)
        y -= 4

    y -= 10
    note = (
        "Questions on tier placement or published moves go to the league office — "
        "not the AI clubs in the Transfer Tool."
    )
    note_h = _text_block_height(c, note, CONTENT_W - 24, FONT, 10, 13) + 24
    c.setFillColor(SKY)
    c.roundRect(LEFT, y - note_h, CONTENT_W, note_h, 8, fill=1, stroke=0)
    c.setFillColor(NAVY)
    wrapped_text(
        c,
        note,
        LEFT + 12,
        y - 16,
        CONTENT_W - 24,
        FONT,
        10,
        13,
        NAVY,
    )


def main() -> None:
    pages = [page_cover, page_structure, page_movement, page_roster, page_tools]
    c = Canvas(str(OUT), pagesize=letter)
    c.setTitle("BOWL-Relegation — GM guide")
    c.setAuthor("Boys of Winter League")
    total = len(pages)
    for i, fn in enumerate(pages, start=1):
        fn(c)
        if i > 1:
            draw_footer(c, i, total)
        c.showPage()
    c.save()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()

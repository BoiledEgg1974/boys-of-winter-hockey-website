"""Build the BOWL-Relegation transfer-tool GM guide PDF."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "_transfer_guide_assets"
OUT_FULL = ROOT / "BOWL-Relegation-Transfers.pdf"
OUT_GM = ROOT / "BOWL-Relegation-Transfers-GM.pdf"

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


def crop_whitespace(src: Path, dest: Path, bg: tuple[int, int, int] = (240, 247, 252)) -> None:
    im = Image.open(src).convert("RGB")
    # Cursor browser captures sometimes include a second copy of the page on the right.
    if im.width >= 1200:
        im = im.crop((0, 0, 1000, im.height))
    px = im.load()
    w, h = im.size

    def is_bg(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        return abs(r - bg[0]) < 18 and abs(g - bg[1]) < 18 and abs(b - bg[2]) < 18

    top = 0
    while top < h and all(is_bg(x, top) for x in range(0, w, 8)):
        top += 1
    bottom = h - 1
    while bottom > top and all(is_bg(x, bottom) for x in range(0, w, 8)):
        bottom -= 1
    left = 0
    while left < w and all(is_bg(left, y) for y in range(top, bottom + 1, 8)):
        left += 1
    right = w - 1
    while right > left and all(is_bg(right, y) for y in range(top, bottom + 1, 8)):
        right -= 1
    pad = 8
    box = (
        max(0, left - pad),
        max(0, top - pad),
        min(w, right + pad + 1),
        min(h, bottom + pad + 1),
    )
    im.crop(box).save(dest, "PNG")


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


def draw_footer(c: Canvas, page: int, total: int, *, gm_only: bool = False) -> None:
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(LEFT, 0.48 * inch, RIGHT, 0.48 * inch)
    c.setFillColor(MUTED)
    c.setFont(FONT, 8)
    audience = "For GMs  ·  September 2026" if gm_only else "For GMs and the league office  ·  September 2026"
    c.drawString(LEFT, 0.32 * inch, audience)
    c.drawRightString(RIGHT, 0.32 * inch, f"{page} / {total}")


def wrapped_text(c: Canvas, text: str, x: float, y: float, max_w: float, font: str, size: int, leading: float, color=INK) -> float:
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


def draw_image(c: Canvas, path: Path, x: float, y_top: float, max_w: float, max_h: float) -> float:
    im = Image.open(path)
    iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    dw, dh = iw * scale, ih * scale
    y = y_top - dh
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.setFillColor(white)
    c.roundRect(x - 2, y - 2, dw + 4, dh + 4, 4, fill=1, stroke=1)
    c.drawImage(str(path), x, y, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
    return y


def caption(c: Canvas, text: str, y: float) -> float:
    return wrapped_text(c, text, LEFT, y - 12, CONTENT_W, FONT, 8.5, 11, MUTED)


def page_cover(c: Canvas, *, gm_only: bool = False) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(0, H - 1.55 * inch, W, 1.55 * inch, fill=1, stroke=0)
    c.setFillColor(LIME)
    c.rect(0, H - 1.62 * inch, W, 0.07 * inch, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(FONT, 11)
    c.drawString(LEFT, H - 0.55 * inch, "BOYS OF WINTER LEAGUE")
    c.setFont(FONT_B, 28)
    c.drawString(LEFT, H - 1.05 * inch, "Cross-League Transfers")
    c.setFont(FONT, 13)
    subtitle = "BOWL-Relegation  ·  GM guide" if gm_only else "BOWL-Relegation  ·  GM & commissioner guide"
    c.drawString(LEFT, H - 1.38 * inch, subtitle)

    y = H - 2.15 * inch
    c.setFillColor(HexColor("#93c5fd"))
    c.setFont(FONT_B, 11)
    c.drawString(LEFT, y, "What this is")
    y -= 18
    y = wrapped_text(
        c,
        "A transfer is how a BOWL-Relegation GM acquires a player from an external professional league "
        "(SHL, KHL, DEL, Liiga, NL, or ELH). The other club is run by the AI. You do not message that "
        "club — the site simulates the negotiation, then the league office approves the deal.",
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
        "It is not a trade between two human GMs. Those still go through the Trade Tool. It is also not "
        "a loan office, junior/AHL pipeline, or a way to send a BOWL player out of the league.",
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
    c.drawString(LEFT + 14, y - 22, "The short path")
    path = (
        "1. Open Transfer Tool  ·  2. Pick league, team, and player  ·  3. Pay the PTA fee plus optional cash or BOWL players  ·  4. AI accepts, counters, or declines  ·  5. Wait for the league office  ·  6. After it is published, apply the move in FHM."
        if gm_only
        else "1. Open Transfer Tool  ·  2. Pick league, team, and player  ·  3. Pay the PTA fee plus optional cash or BOWL players  ·  4. AI accepts, counters, or declines  ·  5. Commissioner publishes  ·  6. You apply the move in FHM."
    )
    y = wrapped_text(
        c,
        path,
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
    c.drawString(LEFT, 0.55 * inch, "Screenshots use example names. Live rosters and fees come from the current FHM import.")


def page_rules(c: Canvas) -> None:
    draw_header_bar(c, "Rules")
    y = H - 0.85 * inch
    c.setFillColor(INK)
    c.setFont(FONT_B, 16)
    c.drawString(LEFT, y, "House rules")
    y -= 22
    items = [
        "Relegation only. Logged-in GMs with an active team membership.",
        "Direction: you acquire one player from an AI external club. Human GMs on the other side are blocked — talk to them directly.",
        "Eligible leagues: SHL, KHL, DEL, Liiga, National League (NL), and ELH. Juniors, NCAA, and AHL are out of this tool.",
        "Compensation is cash and up to two BOWL players. Draft picks are not allowed.",
        "KHL: the player must be off contract. There is no PTA agreement, so a player still under KHL contract is blocked.",
        "European players under contract pay a PTA / transfer fee. European UFAs age 22+ follow the no-fee UFA path.",
        "Fees are the confirmed table at a $95,500,000 salary cap and scale if the live cap or team budget ceiling changes.",
        "PTA plus extra cash cannot exceed your remaining budget (cap minus current roster AAV).",
        "The AI is deliberately tough. Fair packages get through; fleeces are countered or declined.",
        "Approved deals post to the same Discord #confirm-trade channel as trades, then you apply the move in FHM. The site roster updates on the next CSV import.",
    ]
    for item in items:
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 6

    y -= 8
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT, y, "PTA fee table at the $95.5M cap")
    y -= 16

    rows = [
        ("League", "Fee"),
        ("SHL", "$400,000"),
        ("Liiga", "$375,000"),
        ("NL", "$350,000"),
        ("DEL", "$325,000"),
        ("ELH", "$300,000"),
        ("Other / default", "$350,000"),
    ]
    col1, col2 = LEFT, LEFT + 2.4 * inch
    c.setFillColor(SKY)
    c.roundRect(LEFT, y - 7 * 16 - 6, 3.6 * inch, 7 * 16 + 10, 6, fill=1, stroke=0)
    for i, (a, b) in enumerate(rows):
        c.setFont(FONT_B if i == 0 else FONT, 10)
        c.setFillColor(BLUE if i == 0 else INK)
        c.drawString(col1 + 10, y - 2, a)
        c.drawString(col2, y - 2, b)
        y -= 16

    y -= 10
    c.setFillColor(MUTED)
    c.setFont(FONT, 9)
    wrapped_text(
        c,
        "Age overlay (used when age is known): under 22 = $250,000  ·  ages 22–25 = $350,000  ·  age 26+ = $450,000. "
        "These dollars move with the cap. Example: if the cap doubled, a $350,000 fee would become $700,000.",
        LEFT,
        y,
        CONTENT_W,
        FONT,
        9,
        12,
        MUTED,
    )


def page_step(
    c: Canvas,
    heading: str,
    body: str,
    image_name: str,
    caption_text: str,
    max_h: float,
) -> None:
    draw_header_bar(c, "How to use the Transfer Tool")
    y = H - 0.85 * inch
    c.setFillColor(INK)
    c.setFont(FONT_B, 16)
    c.drawString(LEFT, y, heading)
    y -= 16
    y = wrapped_text(c, body, LEFT, y, CONTENT_W, FONT, 10, 13)
    img = ASSETS / image_name
    y = draw_image(c, img, LEFT, y - 8, CONTENT_W, max_h)
    caption(c, caption_text, y)


def page_howto_open(c: Canvas) -> None:
    page_step(
        c,
        "1. Open the tool",
        "Sign in as a Relegation GM. In the GM / Admin header, click Transfer Tool. That page is Relegation-only.",
        "01-nav-crop.png",
        "Figure 1. Transfer Tool is in the GM header. League, team, and player start empty until you choose a league.",
        5.4 * inch,
    )


def page_howto_build(c: Canvas) -> None:
    page_step(
        c,
        "2. Build the offer",
        "Choose external league, then team, then player. The site shows the required PTA fee, the league cap, and your remaining budget. Enter at least that PTA amount, add cash if needed, and optionally tick up to two of your players. Notes are optional. Then submit for AI review.",
        "02-build-offer-crop.png",
        "Figure 2. Example SHL offer. Names and dollars are samples; your live page uses current import data.",
        5.5 * inch,
    )


def page_howto_ai(c: Canvas) -> None:
    page_step(
        c,
        "3. Read the AI answer",
        "Accept (about 97%+ of the club's valuation) goes to the commissioner. A counter (roughly 72-97%) asks for more cash or a player; you can accept the counter or revise. Below that, the deal is declined. KHL players still under contract are blocked before valuation.",
        "03-ai-counter-crop.png",
        "Figure 3. Example counter. Accept the extra cash, or send a revised package from the same page.",
        5.3 * inch,
    )


def page_howto_admin(c: Canvas) -> None:
    page_step(
        c,
        "4. League office publishes",
        "Commissioners open Admin, then Cross-league transfer proposals. Approve & publish writes the transaction news and posts to #confirm-trade. Deny returns a note to the GM. After approval, make the move in the FHM save.",
        "04-admin-approve-crop.png",
        "Figure 4. Commissioner queue and the approve / deny actions on a pending proposal.",
        5.3 * inch,
    )


def page_after(c: Canvas, *, gm_only: bool = False) -> None:
    draw_header_bar(c, "After you submit")
    y = H - 0.85 * inch
    c.setFillColor(INK)
    c.setFont(FONT_B, 16)
    c.drawString(LEFT, y, "Status meanings")
    y -= 20
    statuses = [
        ("pending_ai", "Just submitted; the AI partner is evaluating."),
        ("ai_counter", "Close but short. Accept their counter or revise cash / players."),
        ("ai_declined", "Too far below value, or blocked by a rule (for example KHL still under contract)."),
        ("pending_commissioner", "AI accepted. Waiting on the league office."),
        ("published", "Approved. News is up and Discord #confirm-trade has the post. Apply it in FHM."),
        ("commissioner_declined", "League office denied. Check the note on the proposal."),
    ]
    for key, expl in statuses:
        c.setFont(FONT_B, 10)
        c.setFillColor(BLUE)
        c.drawString(LEFT, y, key)
        y = wrapped_text(c, expl, LEFT + 2.15 * inch, y, CONTENT_W - 2.15 * inch, FONT, 10, 13, INK)
        y -= 6

    y -= 8
    c.setFillColor(INK)
    c.setFont(FONT_B, 16)
    c.drawString(LEFT, y, "Applying the move in FHM")
    y -= 18
    for item in (
        "Wait until the proposal is published. Do not move the player in FHM on a counter or a decline.",
        "After it is published, transfer the player in the FHM save the same way you apply a confirmed trade.",
        "The website roster updates on the next CSV import — same pattern as the Trade Tool.",
    ):
        y = bullet(c, item, LEFT, y, CONTENT_W)
        y -= 4

    y -= 12
    box_h = 98 if gm_only else 112
    c.setFillColor(SKY)
    c.roundRect(LEFT, y - box_h, CONTENT_W, box_h, 8, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont(FONT_B, 12)
    c.drawString(LEFT + 12, y - 20, "Quick reminders")
    y -= 38
    reminders = [
        "One player in per proposal in this version.",
        "Picks cannot be added, even by editing the form.",
        "If the required PTA is more than your remaining budget, you cannot submit.",
        "Questions on a live deal go to the league office, not the AI club.",
    ]
    for item in reminders:
        y = bullet(c, item, LEFT + 12, y, CONTENT_W - 24)
        y -= 2


def _write_pdf(path: Path, pages: list, *, gm_only: bool, title: str) -> None:
    c = Canvas(str(path), pagesize=letter)
    c.setTitle(title)
    c.setAuthor("Boys of Winter League")
    total = len(pages)
    for i, fn in enumerate(pages, start=1):
        fn(c)
        if i > 1:
            draw_footer(c, i, total, gm_only=gm_only)
        c.showPage()
    c.save()
    print(f"Wrote {path}")


def main() -> None:
    pairs = [
        ("01-nav.png", "01-nav-crop.png"),
        ("02-build-offer.png", "02-build-offer-crop.png"),
        ("03-ai-counter.png", "03-ai-counter-crop.png"),
        ("04-admin-approve.png", "04-admin-approve-crop.png"),
    ]
    for src_name, dest_name in pairs:
        src = ASSETS / src_name
        if src.is_file():
            crop_whitespace(src, ASSETS / dest_name)

    _write_pdf(
        OUT_FULL,
        [
            lambda c: page_cover(c, gm_only=False),
            page_rules,
            page_howto_open,
            page_howto_build,
            page_howto_ai,
            page_howto_admin,
            lambda c: page_after(c, gm_only=False),
        ],
        gm_only=False,
        title="BOWL-Relegation Cross-League Transfers",
    )
    _write_pdf(
        OUT_GM,
        [
            lambda c: page_cover(c, gm_only=True),
            page_rules,
            page_howto_open,
            page_howto_build,
            page_howto_ai,
            lambda c: page_after(c, gm_only=True),
        ],
        gm_only=True,
        title="BOWL-Relegation Cross-League Transfers — GM guide",
    )


if __name__ == "__main__":
    main()

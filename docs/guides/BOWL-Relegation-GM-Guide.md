# BOWL-Relegation — GM guide

**Audience:** GMs on the **`/bowl-fantasy`** site (BOWL-Relegation).  
**Related:** [Signing pools](../../BOWL-Relegation-Signing-Pools.md) · **PDF:** [BOWL-Relegation-GM-Guide.pdf](BOWL-Relegation-GM-Guide.pdf) (`python docs/guides/build_relegation_gm_guide_pdf.py`) · [Cross-league transfers (GM PDF)](BOWL-Relegation-Transfers-GM.pdf)

---

## What this league is

BOWL-Relegation models **European-style professional hockey**, not the NHL entry-draft loop. Talent is built through **signings, development, and transfers** (with fees where rules say so), while **competitive tiers** are separated by **promotion and relegation**—similar in spirit to SHL/Allsvenskan or DEL/DEL2.

In Franchise Hockey Manager you will see two main tiers:

| Tier | Typical FHM label | Role |
|------|-------------------|------|
| **Upper** | BLUP (BOWL Upper) | Top division — relegation risk at the bottom of the table |
| **Lower** | BLOW (BOWL Lower) | Second division — promotion chase at the top |

On the website, use the **Combined / Upper / Lower** tabs on standings, stats, and records to switch views. **Combined** is the full league picture; **Upper** and **Lower** are the tables that drive movement.

---

## Promotion & relegation (house rules)

Movement is decided **after the season playoffs**, not mid-season.

- **Relegation:** The **bottom two** teams in the **Upper** tier move down to the **Lower** tier.
- **Promotion:** The **top two** teams in the **Lower** tier move up to the **Upper** tier.

During the season, open **Promotion / Relegation** in the main nav for a live watch list (relegation danger in the Upper table, promotion zone in the Lower table). Standings badges (**U** / **L**) on combined views show each club’s current tier.

**Why it matters (real pro/rel dynamics):** Unlike a draft that sends young talent to weak teams, relegation mostly **does not** redistribute stars for you. Dropping a tier usually means **tighter budgets, harder retention, and a longer climb back**—so academy signings, smart transfers, and roster planning matter more. That tension is intentional: the story is in the table, not in lottery odds.

**What stays league-wide**

- One **shared player pool** and the same import-driven rosters on the site.
- **Combined all-time records** and league-wide history.
- The same **salary cap** framework as other BOWL mounts (your team’s cap math is unchanged).

**What is tier-scoped**

- **Standings, schedules, and tier-filtered team pages** when you pick Upper or Lower.
- **Who plays whom in which FHM league file** follows BLUP vs BLOW after movement is applied in the save.

If the site shows an **Upper / Lower — under construction** banner, the split is not live yet—use **combined** standings and records until the post–season-reset import activates separate tiers. Movement tracking on **Promotion / Relegation** goes live with that split.

---

## Building your roster (no entry draft)

BOWL-Relegation **does not run an NHL-style entry draft** on the site. Draft History, Draft Eligible, Draft Hub, and the draft lottery are **off** on this mount. You add players the way European clubs do: **scout, sign, trade, and pay transfer fees**.

Use these **Signing** pages (main nav):

| Page | Who is listed |
|------|----------------|
| **Prospects** | Age **17 and under**, worldwide, **not** on a BLUP/BLOW roster |
| **Signable (18–20)** | Ages **18–20**, no BOWL org rights, not on BLUP/BLOW |
| **Overseas Transfers** | Players on **transfer-eligible** pro league rosters (see below) with PTA/fee and eligibility flags |
| **Free Agents** | No FHM team assignment; shows **rights holder** and fee when another club holds rights |

**Transfer-eligible external leagues** (for Overseas Transfers and the Transfer Tool): **BLUP, BLOW, SHL, KHL, DEL, Liiga, NL, ELH**. Juniors, NCAA, and AHL are **not** part of this cross-league transfer workflow.

**PTA / transfer fees** are set at a **$95.5M** reference cap and **scale** with the live league cap—the same numbers as the Transfer Tool. Notable rules:

- **KHL:** Player must be **off contract**; there is no PTA path while still under KHL contract.
- **European UFAs age 22+:** **$0** fee on the UFA path.
- **Deals involving another human BOWL GM (BLUP/BLOW):** use the **Transfer Tool** — same PTA, budget, and sweeteners as AI clubs; the selling GM accepts on the site, then the league office publishes.

For step-by-step Transfer Tool use, statuses, and the fee table, see **[BOWL-Relegation-Transfers-GM.pdf](BOWL-Relegation-Transfers-GM.pdf)**.

Multi-asset **trades** between human GMs (picks, balanced deals) are still entered by the league office in the **Trade Tool**.

---

## Tools you still use

| Goal | Tool |
|------|------|
| Acquire from an **AI** external club (SHL, KHL, etc.) | **Transfer Tool** (Relegation GM header) → AI review → league office publish → apply in FHM |
| Deal with **another human** BOWL GM | **Trade Tool** (unchanged) |
| Apply a published transfer or trade | **FHM save first**; site rosters refresh on the **next CSV import** |

Cross-league transfers are **one player in** per proposal, **cash plus up to two BOWL players**—**no draft picks** as sweeteners.

---

## Season checklist for GMs

1. Know your tier (**U** / **L**) and whether you are in **relegation danger** or the **promotion zone**.
2. Plan roster moves through **Prospects / Signable / Overseas / Free Agents**, not the entry draft.
3. Use the **Transfer Tool** for AI clubs; use **trades** for human partners.
4. After the office **publishes** a move, execute it in FHM and wait for the import before expecting the site to match.
5. When movement is applied for the new season, confirm your club is in the correct **BLUP or BLOW** league in FHM.

Questions on published moves or tier placement go to the **league office**, not the AI partner clubs in the Transfer Tool.

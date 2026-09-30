# BOWL-Relegation signing pools

BOWL-Relegation (`bowl-fantasy` mount) does **not** run an NHL-style entry draft. Public signing information lives on four pages:

| Nav link | URL | Pool |
|----------|-----|------|
| Prospects | `/prospects` | Age **17 and under**, worldwide, **not** on a BLUP/BLOW roster |
| Overseas Transfers | `/undrafted-prospects` | Players on transfer-eligible league rosters with **PTA/fee** and eligibility flags |
| Signable (18–20) | `/signable` | Ages **18–20** (Sep 15 / Dec 31 anchors), no BOWL org rights, not on BLUP/BLOW |
| Free Agents | `/free-agents` | **No FHM team assignment**; shows rights holder and fee when another club/league holds rights |

## Disabled on Relegation

- Draft History (`/draft`)
- Draft Eligible (`/draft-eligible`)
- Draft Hub, draft lottery preview, boost lottery (entry-draft workflow)
- Admin Draft Hub setup and Draft Eligible settings

Cap and Historical mounts are unchanged.

## Transfer-eligible leagues

Configured in [`config/transfer_rules_bowl_fantasy.json`](../config/transfer_rules_bowl_fantasy.json):

- **BLUP** (FHM league id 0), **BLOW** (id 1)
- **SHL, KHL, DEL, Liiga, NL, ELH** (ids 5–9, 16)

PTA amounts are defined at a **$95.5M** reference cap and **scale** with the live league cap (same as the Transfer Tool).

- **KHL under contract:** blocked (no PTA path until contract clears).
- **European UFA 22+:** $0 PTA on the UFA path.
- **Human GM sellers (including BLUP/BLOW):** negotiate off-tool; the AI Transfer Tool cannot submit for human-owned clubs, but **fees still apply** when the commissioner publishes the move.

PDF guides:

- League overview (tiers, movement, signing): [`docs/guides/BOWL-Relegation-GM-Guide.pdf`](guides/BOWL-Relegation-GM-Guide.pdf) — regenerate with `python docs/guides/build_relegation_gm_guide_pdf.py`
- Cross-league transfers: [`docs/guides/BOWL-Relegation-Transfers.pdf`](guides/BOWL-Relegation-Transfers.pdf) and [`BOWL-Relegation-Transfers-GM.pdf`](guides/BOWL-Relegation-Transfers-GM.pdf)

## Free Agents columns (Relegation)

- **Rights:** BOWL org from DB `prospects` + `player_rights.csv`, or “None”
- **PTA $ / Fee?:** computed from transfer rules when a rights holder exists; “Blocked” when KHL or other rule blocks acquisition

Regenerate PDFs after copy changes:

```bash
python docs/guides/build_relegation_gm_guide_pdf.py
python docs/guides/build_transfer_guide_pdf.py
```

**Trade Tool PTA dry-run** (Relegation only): default `RELEGATION_TRADE_PTA_DRY_RUN=1` blocks commissioner **Publish** and enables **Dry-run PTA check** on the commissioner Trade Tool and AI Trade Tool. Set `RELEGATION_TRADE_PTA_DRY_RUN=0` to allow direct commissioner publishes with PTA validation.

**GM trade proposals** (Relegation): default `RELEGATION_GM_TRADE_PROPOSALS=1` enables **Submit to partner** on the AI Trade Tool (ledger + PTA fields). Flow: proposer → partner approve/decline → commissioner publish (PTA re-checked). Set `RELEGATION_GM_TRADE_PROPOSALS=0` to disable.

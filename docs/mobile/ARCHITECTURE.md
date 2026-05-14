# Mobile shell decision (Boys of Winter League)

**Chosen approach: Option C — Hybrid**, with **Phase 1** delivered as a **Capacitor native shell** that loads the **production league site in a WebView** (same features as the browser, shared Flask session when cookies are enabled).

| Option | Role here |
|--------|-----------|
| A — WebView-first | **Phase 1 implementation** (Capacitor + trusted WebView to ship parity fast). |
| B — API-first native | **Future** high-traffic screens (draft board, trade review) when JSON endpoints are expanded. |
| C — Hybrid | **Overall strategy**: native shell (tabs, settings, push registration) + WebView for GM portal + selective native screens later. |

**Admin / commissioner:** Remains **web-only** at `/admin/…` on each league origin. The mobile app opens those URLs in the system browser or an in-app browser (see [PUSH_AND_DEEP_LINKS.md](./PUSH_AND_DEEP_LINKS.md) and main plan).

**Repository layout:** Native shell and Capacitor config live under [`mobile/`](../../mobile/) in this monorepo.

**Sound / haptics:** See [SOUND_UX.md](./SOUND_UX.md).

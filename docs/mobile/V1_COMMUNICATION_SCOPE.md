# Mobile v1 — communication scope

This document locks **product scope** for the first mobile releases (WebView shell + push). It avoids building a second chat product until you explicitly choose to.

## In scope for v1 (reuse existing web + API)

| Channel | What it is today | Mobile behavior |
|---------|------------------|-----------------|
| **GM ↔ GM messaging** | In-site threads (`GmLeagueMessage` in [app/site_models.py](../../app/site_models.py)); routes under `/gm-messages` ([site_portal](../../app/routes/site_portal.py)). | Same pages in WebView; push can deep-link to `/gm-messages/with/<peer_user_id>`. |
| **GM in-app notifications** | `GmInAppNotification` rows (e.g. news moderation); surfaced in GM Messages UI. | Same; push may mirror high-priority kinds later. |
| **League headlines / news** | Published articles; **comments** and **votes** via [`POST /api/news/...`](../../app/routes/api.py) when the viewer is allowed. | WebView for reading; optional native wrappers later; push can link to article anchors. |
| **Discord** | Outbound events / bot integration ([discord_events](../../app/services/discord_events.py)). | Remains **bridge only** for v1; no in-app Discord timeline. |

## Out of scope for v1 (requires new product work)

- **Public league-wide chat rooms** (persistent channels, moderation tooling, retention policies).
- **Rich media DMs** (voice notes, attachments beyond what the trade tool already supports on web).
- **Replacing Discord** for long-form league discussion.

## Moderation and safety (baseline)

- **News comments:** Reuse existing server rules and `viewer_can_react_on_news` checks; report/delete flows stay web-first until duplicated in native.
- **GM messages:** Treat as **private between GMs**; mobile must not log message bodies in analytics. Block/report path: if absent today, track as a **post-v1** web feature before marketing the app to minors-heavy audiences.
- **Terms:** Mobile WebView is still the same site; existing membership terms and league rules apply.

## Summary

**v1 communication = GM inbox + in-app GM notices + public news (read / comment / vote where permitted).** Anything broader is explicitly **v2+**.

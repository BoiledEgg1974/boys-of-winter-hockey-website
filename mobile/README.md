# Boys of Winter — mobile shell (Capacitor)

This folder is the **Phase 1 hybrid shell**: a small native app (iOS / Android) that primarily loads your **deployed league website** in a WebView so GMs get home-screen install, deep links, and (once configured server-side) push notifications without duplicating every Flask template.

## Prereqs

- Node.js 20+ and npm
- Xcode (iOS) and/or Android Studio (Android) for store builds

## Setup

```bash
cd mobile
npm install
npx cap sync
```

The tracked `capacitor.config.ts` is a minimal default. For dev WebView loads against a real host, add a `server` block (see `capacitor.config.example.ts`) or merge that file into your local config.

For a **local** Flask dev server over HTTP, use your machine’s LAN IP or `https://` tunnel; iOS ATS may block plain `http://` except localhost.

## iOS / Android projects

```bash
npx cap add ios
npx cap add android
```

Open `ios/App/App.xcworkspace` or `android/` in Android Studio to configure signing and run on device.

## Related docs

- [docs/mobile/ARCHITECTURE.md](../docs/mobile/ARCHITECTURE.md) — hybrid decision
- [docs/mobile/AUTH_AND_SESSION.md](../docs/mobile/AUTH_AND_SESSION.md)
- [docs/mobile/PUSH_AND_DEEP_LINKS.md](../docs/mobile/PUSH_AND_DEEP_LINKS.md) — includes `POST /api/mobile/push-token`
- [docs/mobile/API_PARITY_CHECKLIST.md](../docs/mobile/API_PARITY_CHECKLIST.md)
- [docs/mobile/SOUND_UX.md](../docs/mobile/SOUND_UX.md)

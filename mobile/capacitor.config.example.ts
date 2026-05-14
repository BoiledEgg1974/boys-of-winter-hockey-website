import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Copy to `capacitor.config.ts` (gitignored) and set `server.url` for dev WebView loads.
 * Production store builds typically omit `server` so the bundled `www/` loads (splash only)
 * or your CI injects the league URL at build time.
 */
const config: CapacitorConfig = {
  appId: "com.boysofwinter.league",
  appName: "Boys of Winter",
  webDir: "www",
  server: {
    url: "https://your-league-origin.example",
    cleartext: true,
  },
};

export default config;

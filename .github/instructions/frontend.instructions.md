---
applyTo: "frontend/**/*.js,frontend/**/*.css,frontend/**/*.html"
---

# Frontend (Vanilla JS/CSS) Conventions

- **Global scope only, no ES modules.** All functions in `app.js` are defined at
  global scope because `index.html` uses inline `onclick="..."` handlers that
  reference them directly. Don't introduce `import`/`export` or wrap code in
  modules — it will break those handlers.
- **No build tools.** `app.js`/`style.css` are served directly, unminified.
  Don't add a bundler/transpiler step.
- **Client-side routing** via the History API (`_routeMap`, `_viewToPath`).
- **Reuse existing shared utilities** instead of re-implementing them:
  - `apiRequest(url, options)` — centralized fetch wrapper with auth
  - `showNotification(message, type)` — toast notifications
  - `confirmAction(message)` — confirmation dialogs
  - `toggleFieldGroup(id)` / `setButtonState(btn, loading)` — UI helpers
  - `escapeAttr(str)` — XSS-safe string escaping for values injected into
    `onclick`/other HTML attributes — use this any time you interpolate
    server data into an inline handler
  - `formatDateTime(iso, { showSeconds })` — date formatting
  - `detectStreamType(url)` — RTSP/HTTP/device detection
  - `buildCaptureCardHtml()` / `buildVideoCardHtml()` — shared card templates
- **CSS variables** drive theming (dark/light mode, cosmic nebula default
  theme) — use existing variables rather than hardcoding colors.
- **PWA support** — `manifest.json` + `sw.js` (network-first caching) + iOS
  safe-area handling. Keep these in mind when changing navigation/layout.

## UI/UX Conventions

- **No emojis in the UI** — use CSS colors for status/concern indicators.
- **Subtext inline** with labels (flex baseline alignment), not below as a
  separate block.
- **No parentheses** around hint subtext.
- **Popover pattern** — reusable across features (e.g., GIF options):
  absolute-positioned panel below the trigger button, card background,
  border, shadow.

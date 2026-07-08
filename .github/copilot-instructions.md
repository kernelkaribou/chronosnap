# ChronoSnap — Copilot Instructions

> Project context and conventions for AI-assisted development.
> This is the source of truth for workflow, architecture, and preferences.

---

## Project Overview

**ChronoSnap** (formerly timelapse-manager) is a self-hosted, Docker-based timelapse capture management application. It captures images from RTSP streams, USB webcams, and Raspberry Pi camera modules on configurable schedules, then builds timelapse videos from those captures.

- **Repository:** `kernelkaribou/chronosnap`
- **Current version:** Read from `VERSION` file at repo root (currently 3.4.0)
- **Stack:** FastAPI (Python 3.11) backend, vanilla JS frontend, SQLite (WAL mode), Docker
- **Single container** serving on port 8080

---

## Git Workflow

### Branching Model

```
main  ← production releases (tagged with version)
  ↑ PR (manual, created by maintainer ONLY — never Copilot)
dev   ← integration branch, ALL work merges here first
  ↑ merge (after testing)
feature/xyz  ← individual feature branches
fix/xyz      ← bug fix branches
```

### Rules

1. **All work happens on branches off `dev`** — never commit directly to dev or main
2. **Always branch from the latest `dev`**: `git checkout dev && git pull origin dev`
3. **Feature branches** use `feature/<name>` prefix, bug fixes use `fix/<name>`
4. **Merge to dev** when feature is tested — delete the feature branch after merge
5. **Never create PRs** — the maintainer will manually create PRs from dev → main when ready. Do not create PRs or suggest PR creation until the maintainer explicitly asks.
6. **Never push directly to main**
7. **Delete feature branches** after merging to dev (both local and remote)
8. **Commit messages** follow conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
9. **Co-author trailer** required on all commits:
   ```
   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
   ```
10. **Before a release**, remind the maintainer to bump the `VERSION` file appropriately (semver: patch for fixes, minor for features, major for breaking changes)

### Keeping dev in Sync with main

After the maintainer merges dev → main for a release, merge main back into dev to pick up the merge commit. This prevents the branches from showing false divergence.

```bash
git checkout dev
git pull origin dev
git fetch origin main
git merge origin/main   # fast-forward or merge commit — either is fine
git push origin dev
```

### Starting New Work

```bash
git checkout dev
git pull origin dev
git checkout -b feature/<descriptive-name>
# ... do work, commit ...
# When done and tested:
git checkout dev
git merge feature/<descriptive-name>
git push origin dev
git branch -d feature/<descriptive-name>
git push origin --delete feature/<descriptive-name>
```

---

## Architecture

### Backend (`backend/`)

```
backend/
├── app.py                  # FastAPI app setup, lifespan, middleware
├── auth.py                 # API key + Referer auth, localhost bypass
├── config.py               # Environment variable configuration
├── database.py             # SQLite init, WAL mode, migrations
├── models.py               # Pydantic models (JobCreate, JobUpdate, etc.)
├── utils.py                # Module (get_now, to_iso, parse_iso) — do NOT convert to a package
├── helpers/
│   ├── db_helpers.py       # get_or_404, ensure_column, enrich_capture, normalize_favorite
│   ├── file_helpers.py     # validate_writable_directory, delete_capture_file, delete_video_files, cleanup_empty_parents
│   └── template_vars.py    # Shared template variable definitions
├── routers/
│   ├── jobs.py             # Job CRUD, scheduling config
│   ├── captures.py         # Capture listing, preview, metadata
│   ├── videos.py           # Video listing, build, GIF download
│   ├── settings.py         # App settings
│   ├── storage.py          # Storage dashboard data
│   ├── tags.py             # Tag CRUD, assignment
│   ├── devices.py          # Local camera device detection
│   ├── event_router.py     # Event log endpoints
│   └── import_router.py    # Import/export endpoints
└── services/
    ├── capture_scheduler.py    # Main scheduler loop (10s cycle)
    ├── capture_backends/       # V4L2, libcamera backends
    ├── image_capture.py        # RTSP/HTTP capture logic
    ├── video_processor.py      # FFmpeg timelapse builder + GIF generation
    ├── auto_builder.py         # Auto-build after capture sessions
    ├── text_overlay.py         # Text overlay on videos
    ├── job_state.py            # Job state machine logic
    ├── state_manager.py        # State persistence
    ├── event_service.py        # Event logging
    ├── import_service.py       # Import/export logic
    ├── maintenance.py          # Orphan cleanup, maintenance tasks
    ├── webhook.py              # Webhook notifications
    ├── url_tester.py           # Stream URL validation
    ├── duration_calculator.py  # Video duration math
    └── thumbnail_generator.py  # Capture thumbnails
```

### Frontend (`frontend/`)

```
frontend/
├── index.html              # Single-page app shell (~1,172 lines)
├── manifest.json           # PWA manifest (app name, icons, standalone display)
├── sw.js                   # Service worker (network-first caching)
└── static/
    ├── css/
    │   └── style.css       # All styles (~4,533 lines), CSS variables, dark/light themes
    └── js/
        └── app.js          # All client-side logic (~8,020 lines), global scope
```

- **Client-side routing** via History API (`_routeMap`, `_viewToPath`)
- **All JS functions are global scope** — no ES modules (onclick handlers reference globals)
- **CSS variables** for theming (dark/light mode), cosmic nebula default theme
- **No build tools** — vanilla JS/CSS served directly
- **PWA support** — manifest.json, service worker, iOS safe area handling

### Key Frontend Utilities

- `apiRequest(url, options)` — centralized fetch wrapper with auth
- `showNotification(message, type)` — toast notifications
- `confirmAction(message)` — confirmation dialogs
- `toggleFieldGroup(id)` / `setButtonState(btn, loading)` — UI helpers
- `escapeAttr(str)` — XSS-safe string escaping for onclick attributes
- `formatDateTime(iso, { showSeconds })` — date formatting
- `detectStreamType(url)` — RTSP/HTTP/device detection
- `buildCaptureCardHtml()` / `buildVideoCardHtml()` — shared card templates

### Database

- **SQLite** with WAL mode, stored at `/app/data/chronosnap.db` inside container
- Schema managed via `database.py` init + `ensure_column()` migrations
- No ORM — raw SQL via `sqlite3` module

---

## Docker

### Development Build & Test

```bash
# Build and run locally
docker compose -f docker-compose.dev.yml up -d --build

# View logs
docker compose -f docker-compose.dev.yml logs -f

# For JS/CSS-only changes: Ctrl+Shift+R in browser (hard refresh)
```

There is **no unit test suite** — testing is manual via Docker. For mobile testing, push to dev so GHCR builds the `dev` tag, then deploy on a network-accessible machine.

### Production Notes

- Image: `ghcr.io/kernelkaribou/chronosnap:latest`
- Dev image: `ghcr.io/kernelkaribou/chronosnap:dev` (built automatically on push to dev)
- Runs as non-root with PUID/PGID
- `cap_drop: ALL` + only CHOWN/SETUID/SETGID
- Health check: `GET /health`
- Production host uses **NFSv3 mounts** — avoid `chmod` in Dockerfile on volume-mounted dirs

### CI/CD (`.github/workflows/`)

- `build.yml` — Build + test on push/PR to main and dev (multi-arch: amd64, arm64)
- `dev-release.yml` — Dev pre-release image on dev push
- `release.yml` — Production release on version tag
- `update-deps.yml` — Weekly pip-compile update, pushes directly to dev (Dependabot can't handle pip-compile)
- `auto-merge-dependabot.yml` — Auto-approves and merges minor/patch Dependabot PRs

### Dependabot (`.github/dependabot.yml`)

- **github-actions** — Weekly updates for workflow action versions, PRs to dev
- **docker** — Weekly updates for Dockerfile base image, PRs to dev
- **Python deps excluded** — Dependabot doesn't support pip-compile; handled by `update-deps.yml` instead
- Requires "Allow auto-merge" enabled in repo settings for auto-merge workflow to function

---

## Version Management

Version is managed via the `VERSION` file at repo root.
- Backend reads it via `get_app_version()` in `app.py`
- Frontend cache-busts using `__APP_VERSION__` placeholder in `index.html`, replaced at serve time
- To bump version: edit `VERSION` file only
- **Reminder:** Before the maintainer merges dev → main for a release, prompt them to bump VERSION appropriately (semver)

---

## Development Preferences

- **Minimize dependencies** — prefer using what's already available (e.g., FFmpeg) over adding new packages. Only add a dependency if the benefit clearly justifies the maintenance cost.
- **Keep it simple** — straightforward solutions over clever ones. This is a self-hosted timelapse app, not enterprise software.
- **No backwards compatibility concerns** — this is a new application, no legacy migration needed.
- **Pace changes** — don't rush through implementation. Check in with the maintainer before merging feature branches to dev. Let them test before moving on.
- **Docker for everything** — all building, testing, and validation happens through Docker. Do not use local tooling (node, python, etc.) outside the container.

---

## UI/UX Conventions

- **No emojis in UI** — use CSS colors for status/concern indicators
- **Subtext inline** with labels (flex baseline alignment), not below as separate blocks
- **No parentheses** around hint subtext
- **Prefer colors over emojis** for status indicators
- **Popover pattern** — reusable across features (e.g., GIF options): absolute-positioned panel below trigger button with card background, border, shadow

---

## Development History

### v3.0.0 (major rebrand from timelapse-manager)
- DST scheduling bug fix, security audit (XSS, auth bypass)
- Full UI redesign with cosmic nebula theme
- Homepage with live polling, animated stats, spotlight
- Local camera support (V4L2, Raspberry Pi)
- Capture comparison, text overlay, tag system, favorites
- Share links, event log, webhooks, import/export
- Storage dashboard, auto-build, streaming export
- DRY refactor (helpers/ package, frontend utilities)
- loadJobDetail decomposed into 9 composable functions

### v3.1.0
- Template variables (`{month}`, `{day}`, `{hour}`, `{minute}`, `{second}`)
- Naming pattern validation with inline feedback

### v3.2.0
- Selection ribbon refactor (icon-based toolbar)
- Timelapse detail header redesign + batch download
- Detail view scroll fix

### v3.3.0
- `{year}` and `{full_month}` template variables
- Share popover cleanup, import button in nav bar

### v3.4.0
- Version file fix (VERSION now matches release tags)
- Empty folder cleanup on timelapse delete (cleanup_empty_parents helper)
- Mobile viewport fixes for captures date filter and timelapse detail views
- PWA support (manifest.json, service worker, iOS safe area handling)
- GIF download from timelapse detail page (FFmpeg two-pass palette, 128-color, min(720, half-source) width)
- README features section condensed

---

## Pending / In-Progress Ideas

- **Split app.js into module files** — natural section boundaries identified; planned approach is split source files + concatenation build script (no behavior change)
- **UI theming standardization** — CSS token system, button semantics, inline style extraction, mobile responsiveness, theme presets
- **Home Assistant integration** — full plan exists for HACS-installable custom integration (`ha-chronosnap`)
- **Animated WebP export** — as an alternative to GIF for significantly smaller file sizes (same FFmpeg pipeline, different output format)

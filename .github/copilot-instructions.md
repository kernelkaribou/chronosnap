# ChronoSnap — Copilot Instructions

> Project context and conventions for AI-assisted development.
> This is the source of truth for workflow, architecture, and preferences.

---

## Project Overview

**ChronoSnap** (formerly timelapse-manager) is a self-hosted, Docker-based timelapse capture management application. It captures images from RTSP streams, USB webcams, and Raspberry Pi camera modules on configurable schedules, then builds timelapse videos from those captures.

- **Repository:** `kernelkaribou/chronosnap`
- **Current version:** Read from `VERSION` file at repo root (currently 3.3.0)
- **Stack:** FastAPI (Python 3.11) backend, vanilla JS frontend, SQLite (WAL mode), Docker
- **Single container** serving on port 8080

---

## Git Workflow

### Branching Model

```
main  ← production releases (tagged with version)
  ↑ PR (manual, created by maintainer only)
dev   ← integration branch, all feature work merges here
  ↑ merge (after testing)
feature/xyz  ← individual feature branches
fix/xyz      ← bug fix branches
```

### Rules

1. **Always branch from the latest `dev`**: `git checkout dev && git pull origin dev`
2. **Feature branches** use `feature/<name>` prefix, bug fixes use `fix/<name>`
3. **Merge to dev** when feature is tested — delete the feature branch after merge
4. **Never create PRs** — the maintainer will manually create PRs from dev → main when ready
5. **Never push directly to main**
6. **Delete feature branches** after merging to dev
7. **Commit messages** follow conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
8. **Co-author trailer** required on all commits:
   ```
   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
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
│   ├── file_helpers.py     # validate_writable_directory, delete_capture_file, delete_video_files
│   └── template_vars.py    # Shared template variable definitions
├── routers/
│   ├── jobs.py             # Job CRUD, scheduling config
│   ├── captures.py         # Capture listing, preview, metadata
│   ├── videos.py           # Video listing, build, share links
│   ├── settings.py         # App settings
│   ├── storage.py          # Storage dashboard data
│   ├── tags.py             # Tag CRUD, assignment
│   ├── shared.py           # Share link endpoints
│   ├── devices.py          # Local camera device detection
│   ├── event_router.py     # Event log endpoints
│   └── import_router.py    # Import/export endpoints
└── services/
    ├── capture_scheduler.py    # Main scheduler loop (10s cycle)
    ├── capture_backends/       # V4L2, libcamera backends
    ├── image_capture.py        # RTSP/HTTP capture logic
    ├── video_processor.py      # FFmpeg timelapse builder
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
├── index.html              # Single-page app shell (~1,154 lines)
└── static/
    ├── css/
    │   └── style.css       # All styles (~4,336 lines), CSS variables, dark/light themes
    └── js/
        └── app.js          # All client-side logic (~7,949 lines), global scope
```

- **Client-side routing** via History API (`_routeMap`, `_viewToPath`)
- **All JS functions are global scope** — no ES modules (onclick handlers reference globals)
- **CSS variables** for theming (dark/light mode), cosmic nebula default theme
- **No build tools** — vanilla JS/CSS served directly

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

### Production Notes

- Image: `ghcr.io/kernelkaribou/chronosnap:latest`
- Runs as non-root with PUID/PGID
- `cap_drop: ALL` + only CHOWN/SETUID/SETGID
- Health check: `GET /health`
- Production host uses **NFSv3 mounts** — avoid `chmod` in Dockerfile on volume-mounted dirs

### CI/CD (`.github/workflows/`)

- `build.yml` — Build + test on push/PR to main and dev (multi-arch: amd64, arm64)
- `dev-release.yml` — Dev pre-release image on dev push
- `release.yml` — Production release on version tag
- `update-deps.yml` — Automated dependency updates

---

## Version Management

Version is managed via the `VERSION` file at repo root.
- Backend reads it via `get_app_version()` in `app.py`
- Frontend cache-busts using `__APP_VERSION__` placeholder in `index.html`, replaced at serve time
- To bump version: edit `VERSION` file only

---

## UI/UX Conventions

- **No emojis in UI** — use CSS colors for status/concern indicators
- **Subtext inline** with labels (flex baseline alignment), not below as separate blocks
- **No parentheses** around hint subtext
- **Prefer colors over emojis** for status indicators
- **No backwards compatibility concerns** — this is a new application

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

---

## Pending / In-Progress Ideas

- **Split app.js into module files** — natural section boundaries identified; planned approach is split source files + concatenation build script (no behavior change)
- **UI theming standardization** — CSS token system, button semantics, inline style extraction, mobile responsiveness, theme presets
- **Home Assistant integration** — full plan exists for HACS-installable custom integration (`ha-chronosnap`)

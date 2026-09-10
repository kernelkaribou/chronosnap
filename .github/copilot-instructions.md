# ChronoSnap — Copilot Instructions

> Repo-wide context for AI-assisted development. Trust this file. Only search the
> codebase further when something here is missing, ambiguous, or looks stale —
> and if you find it's stale, fix this file as part of your change.
>
> Path-specific conventions live in `.github/instructions/*.instructions.md` and are
> loaded automatically when you touch matching files. This file holds only the
> facts that apply regardless of which files a task touches.

---

## Project Overview

**ChronoSnap** is a self-hosted, Docker-based timelapse capture management
application. It captures images from RTSP streams, USB webcams, and Raspberry Pi
camera modules on configurable schedules, then builds timelapse videos from those
captures.

- **Repository:** `kernelkaribou/chronosnap`
- **Current version:** read from the `VERSION` file at repo root — do not hardcode
  a version number anywhere else (grep before assuming a version is current)
- **Stack:** FastAPI (Python 3.13) backend, vanilla JS frontend, SQLite (WAL mode),
  single Docker container
- **App port:** the container serves on `8080` by default. This is unrelated to the
  fixed test port below — never confuse the two.

---

## Git Workflow

```
main  ← production releases (tagged with version)
  ↑ PR (manual, created by the maintainer ONLY — never Copilot)
dev   ← integration branch, ALL work merges here first
  ↑ merge (after testing)
feature/xyz  ← individual feature branches
fix/xyz      ← bug fix branches
```

1. **All work happens on branches off `dev`** — never commit directly to `dev` or `main`.
2. **Always branch from the latest `dev`**: `git checkout dev && git pull origin dev`.
3. **Branch names:** `feature/<name>` for features, `fix/<name>` for bug fixes.
4. **Merge to `dev`** once a change is tested — delete the branch (local + remote) after merging.
5. **Never create PRs.** The maintainer creates PRs from `dev` → `main` manually when ready. Do not suggest opening one until explicitly asked.
6. **Never push directly to `main`.**
7. **Commit messages** follow conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`.
8. **Every commit** includes:
   ```
   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
   ```
9. **Before a release**, remind the maintainer to bump `VERSION` (semver: patch/minor/major).

After the maintainer merges `dev` → `main`, sync the merge commit back:
```bash
git checkout dev && git pull origin dev
git fetch origin main && git merge origin/main
git push origin dev
```

---

## Architecture Map

```
backend/
├── app.py                  # FastAPI app setup, lifespan, middleware, router registration
├── auth.py                 # API key auth + same-origin/localhost bypass (see Security section)
├── config.py               # Environment variable configuration
├── database.py             # SQLite init, WAL mode, ensure_column() migrations
├── models.py                # Pydantic request/response models
├── utils.py                 # Module (get_now, to_iso, parse_iso) — do NOT convert to a package
├── helpers/                 # get_or_404, file/path helpers, template variable helpers
├── routers/                  # jobs, captures, videos, settings, storage, tags, devices, events, import
└── services/                 # scheduler, capture backends, video processing, webhooks, etc.

frontend/
├── index.html               # Single-page app shell
├── manifest.json / sw.js    # PWA manifest + service worker
└── static/{css,js}/         # style.css and app.js — all global scope, no build step

tests/e2e/                    # Reusable Docker-based end-to-end test suite (see Testing section)
```

See `.github/instructions/backend.instructions.md` and
`.github/instructions/frontend.instructions.md` for conventions specific to those trees.

**Database:** SQLite (WAL mode) at `/app/data/chronosnap.db` inside the container. No
ORM — raw SQL via the stdlib `sqlite3` module. Schema changes go through
`database.py`'s `ensure_column()` migration helper, not destructive alters.

---

## Development Principles

- **Minimize dependencies.** Prefer what's already available (e.g., FFmpeg, stdlib)
  over adding a package. Only add a dependency when the benefit clearly justifies
  the ongoing maintenance cost.
- **Keep it simple.** Straightforward solutions over clever ones — this is a
  self-hosted timelapse app, not enterprise software.
- **No backwards-compatibility burden.** No legacy migrations to preserve.
- **High-quality reviews are mandatory.** Every non-trivial change should be
  reviewed as if by a careful senior engineer before it's considered done — check
  for correctness, security, and unnecessary complexity, not just "does it run."
- **Thorough testing is mandatory for feature additions.** New backend
  functionality needs a corresponding scenario in `tests/e2e/` (see Testing below)
  in addition to manual verification. Don't consider a feature done until both
  exist.
- **Pace changes.** Don't rush through implementation. Check in with the
  maintainer before merging feature branches to `dev`; let them test before
  moving on to the next thing.
- **Docker for everything.** All *building, running, and testing* of the
  application happens through Docker (`docker compose`) — never install/run
  the app directly with a host Python/Node interpreter. (Test-harness
  *orchestration* scripts that only shell out to `docker`/`docker compose` are
  fine — see Testing.) This does **not** extend to local editor/IDE tooling: a
  local `.venv` with `requirements.txt` installed is expected and used for
  real development (autocomplete, go-to-definition, type checking, linting) —
  it's not a stray artifact and should not be deleted or flagged as unused.

---

## Security & Privacy

Privacy and security are paramount for this project — it manages users' private
camera footage.

- **Auth model:** `backend/auth.py`'s `verify_api_key` dependency guards all
  `/api/*` routes except a few static/health endpoints. External callers must
  supply the `X-API-Key` header or `api_key` query param. Same-origin browser
  requests (and literal localhost connections with no `Referer`) bypass the key.
  **Known limitation:** this bypass trusts the client-supplied `Referer`/`Host`
  headers, which a non-browser caller can forge — treat it as a UX convenience
  for the bundled web UI, not as a hardened security boundary, when reasoning
  about what's actually protected.
- **Network posture:** CORS is same-origin only (`allow_origins=[]`).
  `SecurityHeadersMiddleware` sets `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  a `Referrer-Policy`, and a `Permissions-Policy` blocking camera/mic/geolocation.
- **Container hardening:** runs non-root via `PUID`/`PGID`, `cap_drop: ALL` +
  minimal `cap_add` (CHOWN/SETUID/SETGID), `no-new-privileges:true`.
- **Data minimization:** don't log API keys, full stream URLs with embedded
  credentials, or capture image contents. Don't add telemetry or outbound calls
  beyond what's documented (the optional, opt-out-able GitHub release version
  check) without calling it out explicitly in the README's data-privacy section.
- **Test data must never be real footage.** The e2e suite (see Testing) uses only
  `https://picsum.photos` (public placeholder images) as its capture source —
  never point automated tests at a real camera, RTSP stream, or any data that
  isn't disposable/public.

---

## Testing

There is no unit test suite for backend logic — validation is:
1. **Manual, interactive testing via Docker** for UI/UX, mobile, RTSP/V4L2/libcamera
   device behavior, and anything the e2e suite doesn't cover.
2. **The reusable Docker-based end-to-end suite** in `tests/e2e/` for the backend
   HTTP-capture-to-video pipeline (job creation → capture → video build → GIF →
   tags/favorites → storage/events → webhook delivery → cleanup). Run it with:
   ```bash
   ./tests/e2e/run.sh
   ```
   See `.github/instructions/testing.instructions.md` for what it covers, its
   scope boundaries, and how to add a new scenario.

**Fixed test port:** all ad hoc/manual container testing (`docker run`,
one-off `docker compose` files, etc.) must publish on **`127.0.0.1:28080`**,
always the same port, never an auto-assigned or incrementing one
(`-p 0:8080`, `18080`, `18081`, ... are not acceptable). This keeps test
instances unambiguous and never exposed beyond the local host. `28080` is
unrelated to the app's real default port (`8080`) — don't reuse `8080` for test
containers, to avoid confusing a test instance with a real dev instance.

For JS/CSS-only changes to a running dev container, a hard refresh
(Ctrl+Shift+R) is enough — no rebuild needed.

---

## Docker & CI/CD

- Production image: `ghcr.io/kernelkaribou/chronosnap:latest`; dev pre-release:
  `ghcr.io/kernelkaribou/chronosnap:dev` (built automatically on push to `dev`).
- Health check: `GET /health`.
- Production hosts use **NFSv3 mounts** — avoid `chmod` in the image on
  volume-mounted directories.
- See `.github/instructions/docker.instructions.md` for Dockerfile/Compose
  conventions and `.github/instructions/github-automation.instructions.md` for
  CI workflows and Dependabot policy.

---

## Version Management

Version is managed via the `VERSION` file at repo root.
- Backend reads it via `get_app_version()` in `app.py`.
- Frontend cache-busts using the `__APP_VERSION__` placeholder in `index.html`,
  replaced at serve time.
- To bump: edit `VERSION` only.
- **Reminder:** before the maintainer merges `dev` → `main` for a release, prompt
  them to bump `VERSION` appropriately (semver).

---

## Recent Releases

Full history is in git tags/release notes, not here — this section only tracks
the current and immediately prior release so past work isn't accidentally
re-suggested as new. Trim to the last 2 entries when adding a new one.

- **v3.8.0** — `VERSION_CHECK` env var to opt out of the GitHub release check,
  resource-limits documentation, security hardening (path traversal, ffmpeg
  concat escaping, frontend XSS, stream credential log leaks, 7z import
  crash/decompression-bomb fix), async offload for blocking routes, ffmpeg
  build stall watchdog.
- **v3.7.0** — Removed the share-link feature.

---

## Pending / In-Progress Ideas

- **Split `app.js` into module files** — split source files + concatenation build
  script, no behavior change.
- **UI theming standardization** — CSS token system, button semantics, inline
  style extraction, mobile responsiveness, theme presets.
- **Home Assistant integration** — plan exists for a HACS-installable custom
  integration (`ha-chronosnap`).
- **Animated WebP export** — alternative to GIF for smaller file sizes (same
  FFmpeg pipeline, different output format).

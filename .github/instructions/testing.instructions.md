---
applyTo: "tests/**"
---

# End-to-End Test Suite Conventions (`tests/e2e/`)

## What this suite is (and isn't)

This is **critical HTTP-capture-to-video API E2E coverage** — it exercises the
backend pipeline that a real HTTP-based capture job goes through: job
creation → scheduled/manual capture → capture storage → video build → GIF
export → tags/favorites → storage dashboard/event log → webhook delivery →
cleanup.

It is **not** a claim of "all functionality" coverage. Out of scope by design:
RTSP/V4L2/libcamera device capture, the browser UI/PWA behavior, mobile
testing, import/export of pre-existing file trees, time-window/DST edge cases,
and video cancellation. Those still need manual, interactive Docker testing.

**When adding a new backend feature, add or extend a scenario here** as part
of calling the feature "tested," per the root instructions' testing
principle — this suite is meant to grow, not stay frozen at its initial scope.

## Architecture

- `docker-compose.test.yml` — an isolated Compose project with two services:
  - `chronosnap` — builds the real `Dockerfile`, published on
    `127.0.0.1:28080:8080` (the fixed test port — see root instructions).
    Uses **named Docker volumes**, never host bind mounts, so test runs leave
    zero residue on disk.
  - `test-runner` — reuses the *same built image* (no second Dockerfile, no
    new dependencies) with its entrypoint/command overridden to run
    `python3 /tests/runner.py` instead of the app. It mounts `tests/e2e/`
    read-only as `/tests`, and mounts the `chronosnap` service's captures/
    timelapses volumes read-only for on-disk output inspection and the
    image's own `ffprobe`/`ffmpeg` for validation — no `docker exec`, no
    Docker socket access from the runner. The `/app/data` volume (containing
    the SQLite DB) is mounted **read-write** at the filesystem level — WAL
    mode needs to open the `-shm` index file in read-write mode even for a
    connection that only reads, so a `:ro` mount fails outright. Read-only
    enforcement for that connection comes from SQLite's own `mode=ro` URI
    flag instead (see `lib.py`), not from the mount.
- `run.sh` — orchestrator: `docker compose ... up --build --abort-on-container-exit
  --exit-code-from test-runner`, always followed by `docker compose ... down -v
  --remove-orphans` (in a trap, so it runs even on failure/interrupt). Exits
  with the runner's real exit code, so it's usable as a CI gate later.
- `runner.py` — waits for `/health`, reads the auto-generated API key directly
  from the shared SQLite DB via a `mode=ro` (read-only) connection, then runs
  an **explicit, ordered** list of scenario functions from `scenarios/`, passing a shared `ctx` dict forward (job/video/tag IDs
  created by earlier scenarios are used by later ones). Scenarios are
  intentionally **not** auto-discovered/order-independent — several depend on
  state from earlier ones, so an implicit discovery order would be fragile.
  Add a new feature's coverage by adding a function to `scenarios/` and adding
  it to the ordered list in `runner.py`, not by inventing a parallel harness.

## Test data rules

- **Only `https://picsum.photos` placeholder images** — never a real
  camera/RTSP/device source. Use a **seeded, stable URL**
  (`https://picsum.photos/seed/chronosnap-e2e/200.jpg`), not a random one —
  the manual-capture endpoint always re-reads the job's stored URL, so a
  "cache-busting" random query param would not actually vary between calls
  the way it might seem to.
- This suite makes real outbound network calls to picsum.photos — it requires
  internet access to run, and a picsum outage/rate-limit is a suite failure
  unrelated to ChronoSnap itself. Don't treat a picsum-side failure as a
  ChronoSnap regression without checking picsum's status first.
- Full teardown after every run, pass or fail: no leftover containers,
  networks, or volumes. Named test images may persist in the local Docker
  cache between runs (that's fine — rebuilding is slow and there's no privacy
  benefit to removing them); only runtime containers/volumes/networks need to
  be gone.

## Fixed test port

Always `127.0.0.1:28080` for anything under `tests/e2e/` — bound to loopback
only, never exposed on all interfaces, and never a different/incrementing
port per run. See the root `copilot-instructions.md` Testing section.

# Backend Unit Test Suite (`tests/unit/`)

## What this suite is for

Fast, isolated `pytest` coverage for backend logic that's impractical to
exercise meaningfully through the full E2E suite — path/containment
validation, sanitization helpers, and other pure-ish functions where you want
to assert exact edge-case behavior (traversal sequences, malformed input,
boundary strings) rather than a single happy-path HTTP call. It complements
the E2E suite; it does not replace it. Prefer E2E scenarios for "does the
real feature work end-to-end," and unit tests for "does this specific
function handle every edge case I can think of."

## Architecture

- `run.sh` — rebuilds the real `Dockerfile` (same image as production/E2E,
  no second Dockerfile), then runs pytest inside an ephemeral container with
  `pytest` installed **transiently** from `requirements-dev.txt` (repo root).
  `requirements-dev.txt` is never referenced by the Dockerfile and is never
  baked into the shipped image — it only exists for this test run.
- `conftest.py` — shared fixtures (`isolated_db`, `captures_base`,
  `videos_base`, `insert_job()`) providing a throwaway SQLite DB and
  temp-directory filesystem roots per test, via monkeypatching. Never touches
  real `/app/data`, `/captures`, or `/timelapses`.
- `pytest.ini` — redirects the pytest cache to `/tmp/.pytest_cache` since
  `tests/unit` is bind-mounted **read-only** into the container.
- Must run via `./tests/unit/run.sh` (not bare `pytest`) — `backend/config.py`
  creates its data directory at import time relative to a hardcoded `/app`
  path, so backend modules can only be imported successfully inside the
  container.

## Adding a new unit test

Add a new `test_*.py` file (or extend an existing one) under `tests/unit/`,
reuse the `conftest.py` fixtures, and import backend modules directly (e.g.
`from backend.helpers.file_helpers import validate_path_within`). When fixing
a bug (especially a security-relevant one), prove the test is meaningful:
confirm it fails against the pre-fix code before considering it done.

# Frontend Unit Test Suite (`tests/unit-js/`)

## What this suite is for

Targeted Node-based tests for specific `frontend/static/js/app.js` logic that
benefits from precise, automated verification — e.g. HTML-escaping
correctness for attribute vs. text-node contexts. This does **not** introduce
a build step, bundler, or dependency into the shipped frontend, which remains
plain vanilla JS/CSS with no build tools; it only tests the existing global
functions from the outside.

## Architecture

- `run.sh` — runs the tests inside an ephemeral, official `node:20-alpine`
  container (no Dockerfile change, no new frontend dependency). It mounts
  `tests/unit-js/` and `frontend/` read-only.
- Test files load the real `frontend/static/js/app.js` source into a Node
  `vm` context with a minimal hand-written DOM shim (just enough for
  `document.createElement`/`addEventListener`/`localStorage`/etc. to not
  throw when the script's top-level bootstrapping code runs), then call the
  actual global functions (e.g. `escapeAttr`, `buildCaptureCardHtml`) with
  adversarial input and assert on the real output. This proves the *actual*
  shipped code is safe, not a reimplementation of it.
- Prefer this "load the real file into a shim" approach over hand-copying
  logic into the test — it stays honest as the real source evolves and would
  fail loudly (a sandbox load error) if the DOM surface it depends on grows.

## Adding a new frontend unit test

If the DOM shim doesn't yet stub something a newly-tested function needs,
extend the shim in the test file rather than adding a real DOM dependency
(jsdom, etc.) — keep it dependency-free per the project's "no build tools"
frontend convention. As with backend fixes, confirm a new regression test
actually fails against the pre-fix code before considering it done.

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

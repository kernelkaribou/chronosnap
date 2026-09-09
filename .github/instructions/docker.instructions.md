---
applyTo: "Dockerfile,**/docker-compose*.yml,**/docker-compose*.yaml,entrypoint.sh"
---

# Docker & Container Conventions

- **Single container**, FastAPI app served by uvicorn, default port `8080`
  (`ENV PORT=8080` in the Dockerfile — note the uvicorn `CMD` currently
  hardcodes `--port 8080` rather than reading `$PORT`; keep that in mind if you
  ever change the default port, both places need updating).
- **Non-root by default in production**: `entrypoint.sh` creates/remaps a user
  from `PUID`/`PGID` env vars and `gosu`s into it. `PUID=0`/`PGID=0` (the
  default if unset) skips user creation and runs as root — don't rely on that
  for anything security-sensitive; production deployments should always set
  non-zero `PUID`/`PGID`.
- **Capabilities:** `cap_drop: ALL` + only `CHOWN`/`SETUID`/`SETGID` added back,
  plus `security_opt: no-new-privileges:true`. Any new compose service/override
  should keep this posture unless there's a concrete, documented reason not to.
- **Resource limits:** `deploy.resources.limits` (cpus/memory) are set in the
  real compose files. Note that plain `docker compose up` under Compose v1
  only *enforces* `deploy.resources` with `--compatibility`; Compose v2
  enforces them natively. Don't assume the limits apply without checking which
  Compose version is in play.
- **NFSv3 caveat:** production hosts mount data directories over NFSv3, which
  doesn't support `chmod` the way local filesystems do. Don't add `chmod` calls
  in the Dockerfile/entrypoint that target volume-mounted directories
  (`/app/data`, `/captures`, `/timelapses`) — `entrypoint.sh`'s `chown` calls
  already tolerate failures there (`|| true`) for this reason.
- **Volumes:** `/app/data` (SQLite DB + settings), `/captures`, `/timelapses`
  are separate top-level mounts, not nested under one directory.
- **Fixed test port:** any Docker container/compose setup used for manual or
  automated testing (not real dev use) must publish on `127.0.0.1:28080`,
  consistently — see the root `copilot-instructions.md` Testing section and
  `.github/instructions/testing.instructions.md`. Never invent a new port per
  test run.

See `.github/instructions/github-automation.instructions.md` for CI workflows
and Dependabot policy, and `.github/instructions/testing.instructions.md` for
the `tests/e2e/docker-compose.test.yml` test harness specifically.

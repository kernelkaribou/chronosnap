---
applyTo: ".github/workflows/**,.github/dependabot.yml"
---

# CI/CD & Dependabot Conventions

## Workflows

- `build.yml` — build + smoke-test on push/PR to `main` and `dev` (multi-arch:
  amd64, arm64). The smoke test runs the image with `-p 8080:8080` and curls
  `/health` — this is a CI-internal check on the app's real port, separate from
  the `tests/e2e/` suite and its fixed `28080` test port.
- `dev-release.yml` — pushes the `:dev` image tag to GHCR on every push to `dev`.
- `release.yml` — pushes versioned + `:latest` tags to GHCR on `v*.*.*` tags,
  reading the version from the `VERSION` file.
- `update-deps.yml` — runs `pip-compile` weekly and **pushes the result directly
  to `dev`**, no PR/review gate. This is a higher-risk automation surface than
  Dependabot (below) since there's no human review before merge — if you modify
  this workflow, keep in mind it can silently break `dev` if a transitive
  dependency bump is incompatible (this has happened before, e.g. Python
  version bumps pulling in packages without a compatible wheel).
- `auto-merge-dependabot.yml` — approves + enables auto-merge for Dependabot
  PRs, but **explicitly excludes**: `semver-major` bumps, and **all**
  `docker`-ecosystem bumps (not just major ones — this is deliberate, base-image
  bumps always get a human look first). This is a considered trade-off, not a
  bug — don't change the exclusion scope without confirming with the maintainer.

## Dependabot (`.github/dependabot.yml`)

- **`github-actions`** — weekly, PRs targeting `dev`.
- **`docker`** — weekly, PRs targeting `dev` (base image bumps). Always
  excluded from auto-merge (see above), so these need manual review/merge.
- **Python dependencies are intentionally excluded from Dependabot** — it
  doesn't support `pip-compile`-generated lockfiles well, so `update-deps.yml`
  handles Python updates instead.
- **"Allow auto-merge" must be enabled in repo settings** for
  `auto-merge-dependabot.yml` to actually merge anything — if PRs sit approved
  but unmerged, check that setting first.

## Lesson learned: PRs excluded from auto-merge still need manual triage

Because major bumps and all Docker bumps are excluded from auto-merge, they
accumulate silently if nobody looks at them periodically. In one case, several
Dependabot PRs sat open for months, some already superseded by manual changes
to `dev`, one failing to build entirely (a transitive dependency lacking a
wheel for the newer Python version). **Periodically (e.g. when doing any
dependency-adjacent work) check open Dependabot PRs against current `dev` and
against an actual trial build** — close stale/superseded/broken ones rather
than letting them sit indefinitely. There is no automated tooling for this;
it's a manual review habit.

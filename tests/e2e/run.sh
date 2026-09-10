#!/usr/bin/env bash
# Runs the ChronoSnap E2E test suite in a fully isolated, disposable Docker
# Compose project, then always tears it down (containers/networks/volumes),
# regardless of pass/fail. Exit code matches the test-runner's result.
#
# The only host-level requirement is `docker compose` itself — see
# .github/instructions/testing.instructions.md.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE=(docker compose -p chronosnap-e2e -f docker-compose.test.yml)

cleanup() {
    echo "==> Tearing down E2E test environment..."
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> Building and starting E2E test environment (fixed port 127.0.0.1:28080)..."
"${COMPOSE[@]}" up --build --abort-on-container-exit --exit-code-from test-runner
exit_code=$?

if [ "$exit_code" -eq 0 ]; then
    echo "==> E2E suite PASSED"
else
    echo "==> E2E suite FAILED (exit code $exit_code)"
fi

exit "$exit_code"

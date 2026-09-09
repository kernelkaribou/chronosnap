#!/bin/bash
# Runs the backend unit test suite inside the application image.
#
# Rebuilds the real Dockerfile (same image used in production/e2e — no
# second Dockerfile), then runs pytest in an ephemeral container with
# pytest installed transiently from requirements-dev.txt. Nothing here is
# baked into the shipped image; requirements-dev.txt is never referenced by
# the Dockerfile itself.
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGE_TAG="chronosnap:unit-test"

echo "==> Building application image (${IMAGE_TAG})..."
docker build -t "$IMAGE_TAG" -f Dockerfile . >/tmp/chronosnap-unit-build.log 2>&1 || {
    echo "Build failed, see /tmp/chronosnap-unit-build.log"
    tail -n 60 /tmp/chronosnap-unit-build.log
    exit 1
}

echo "==> Running unit tests..."
docker run --rm \
    -v "$(pwd)/tests/unit:/tests/unit:ro" \
    -v "$(pwd)/requirements-dev.txt:/tmp/requirements-dev.txt:ro" \
    -w /app \
    --entrypoint bash \
    "$IMAGE_TAG" \
    -c "pip install --no-cache-dir --quiet -r /tmp/requirements-dev.txt && python -m pytest /tests/unit -v \"\$@\"" \
    _ "$@"

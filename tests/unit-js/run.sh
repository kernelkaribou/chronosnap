#!/bin/bash
# Runs the frontend JS unit test suite inside an ephemeral, official Node
# container. No dependency is added to the shipped frontend (still plain
# vanilla JS, no build tools, no bundler) and no Dockerfile change is
# required -- this only ever runs the real frontend/static/js/app.js source
# through Node's built-in `vm` module with a minimal DOM shim.
set -euo pipefail
cd "$(dirname "$0")/../.."

NODE_IMAGE="node:20-alpine"

echo "==> Running frontend unit tests (${NODE_IMAGE})..."
docker run --rm \
    -v "$(pwd)/tests/unit-js:/tests/unit-js:ro" \
    -v "$(pwd)/frontend:/frontend:ro" \
    -w /tests/unit-js \
    "$NODE_IMAGE" \
    node test_xss_fixes.js

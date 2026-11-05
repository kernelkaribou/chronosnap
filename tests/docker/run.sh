#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGE_TAG="chronosnap:startup-test"
docker build --quiet -t "$IMAGE_TAG" .
docker run --rm \
    --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
    --security-opt no-new-privileges:true \
    -v "$(pwd)/tests/docker:/tests:ro" \
    --entrypoint bash \
    "$IMAGE_TAG" /tests/test_entrypoint.sh

container=""
cleanup() {
    if [ -n "$container" ]; then
        docker stop --timeout 10 "$container" > /dev/null
        docker rm "$container" > /dev/null
        container=""
    fi
}
trap cleanup EXIT

check_application() {
    local label="$1"
    shift
    container=$(docker run --detach \
        --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
        --security-opt no-new-privileges:true \
        --mount "type=bind,source=$(pwd)/tests/docker,target=/imports,readonly" \
        "$@" "$IMAGE_TAG")
    for ((attempt = 0; attempt < 60; attempt++)); do
        if docker exec "$container" curl --silent --fail --max-time 2 \
            http://127.0.0.1:8080/health > /dev/null; then
            echo "PASS: application health with $label and read-only imports"
            return 0
        fi
        if [ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]; then
            break
        fi
        sleep 2
    done
    docker logs "$container" >&2
    echo "FAIL: application did not become healthy with $label" >&2
    exit 1
}

check_application "PUID/PGID" -e PUID=2000 -e PGID=2000
if ! docker logs "$container" 2>&1 |
    grep 'WARNING: cannot change ownership of /imports' > /dev/null; then
    echo "FAIL: read-only imports did not produce an ownership warning" >&2
    exit 1
fi
cleanup

check_application "an explicit non-root user" --user 1000:1000 \
    --tmpfs /app/data:uid=1000,gid=1000,mode=0700 \
    --tmpfs /app/import-staging:uid=1000,gid=1000,mode=0700 \
    --tmpfs /captures:uid=1000,gid=1000,mode=0700 \
    --tmpfs /timelapses:uid=1000,gid=1000,mode=0700

#!/usr/bin/env python3
"""
E2E test suite entrypoint.

Runs an explicit, ordered list of scenarios against a live ChronoSnap
instance. Scenarios share a `ctx` dict — later scenarios rely on state
(job/video/tag IDs) created by earlier ones, so order matters and scenarios
are intentionally NOT auto-discovered (see
.github/instructions/testing.instructions.md for why).
"""
import os
import sys
import time
import traceback

from lib import ApiClient, wait_for_health, get_api_key
from scenarios import (
    health,
    auth,
    job_lifecycle,
    manual_capture,
    video_build,
    gif_download,
    tags_favorites,
    storage_and_events,
    webhook,
    scheduler_completion,
    cleanup,
)

BASE_URL = os.environ.get("CHRONOSNAP_BASE_URL", "http://chronosnap:8080")
DB_PATH = os.environ.get("CHRONOSNAP_DB_PATH", "/app/data/chronosnap.db")

SCENARIOS = [
    ("health", health.run),
    ("auth", auth.run),
    ("job_lifecycle", job_lifecycle.run),
    ("manual_capture", manual_capture.run),
    ("video_build", video_build.run),
    ("gif_download", gif_download.run),
    ("tags_and_favorites", tags_favorites.run),
    ("storage_and_events", storage_and_events.run),
    ("webhook_delivery", webhook.run),
    ("scheduler_completion", scheduler_completion.run),
    ("cleanup", cleanup.run),
]


def main():
    print(f"==> Waiting for {BASE_URL}/health ...")
    wait_for_health(BASE_URL)
    print("==> App is healthy. Reading API key from read-only DB mount...")
    api_key = get_api_key(DB_PATH)

    ctx = {
        "base_url": BASE_URL,
        "client": ApiClient(BASE_URL, api_key),
        "unauthenticated_client": ApiClient(BASE_URL),
    }

    results = []
    for name, fn in SCENARIOS:
        print(f"\n=== {name} ===")
        start = time.time()
        try:
            fn(ctx)
            print(f"PASS ({time.time() - start:.1f}s)")
            results.append((name, True, None))
        except Exception as e:  # noqa: BLE001 - want to keep running remaining scenarios
            print(f"FAIL ({time.time() - start:.1f}s): {e}")
            traceback.print_exc()
            results.append((name, False, str(e)))

    print("\n" + "=" * 60)
    print("E2E RESULTS")
    print("=" * 60)
    for name, ok, err in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
        if err:
            line += f" — {err}"
        print(line)
    print("=" * 60)

    failed = [r for r in results if not r[1]]
    if failed:
        print(f"{len(failed)}/{len(results)} scenario(s) FAILED")
        sys.exit(1)
    print(f"All {len(results)} scenarios passed")
    sys.exit(0)


if __name__ == "__main__":
    main()

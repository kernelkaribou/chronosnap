"""
Create the job used by the rest of the suite, pointed at a stable, seeded
picsum.photos URL (never a random one — the manual-capture endpoint always
re-reads the job's stored URL, so a "cache-busting" query param wouldn't
actually vary anything between calls).
"""
from datetime import datetime, timedelta, timezone

PICSUM_URL = "https://picsum.photos/seed/chronosnap-e2e/200.jpg"


def run(ctx):
    client = ctx["client"]
    # start_datetime is deliberately in the future so the scheduler treats
    # this job as "sleeping" and never captures it on its own — every
    # capture on this job comes from the manual-capture endpoint, so counts
    # stay deterministic. Manual capture works regardless of job status.
    payload = {
        "name": "e2e-test-job",
        "url": PICSUM_URL,
        "stream_type": "http",
        "start_datetime": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "interval_seconds": 10,
        "framerate": 30,
    }
    status, body = client.post("/api/jobs/", json_body=payload, expect=201)
    assert body.get("id"), f"job creation did not return an id: {body}"
    assert body.get("url") == PICSUM_URL, f"url mismatch: {body}"

    ctx["job_id"] = body["id"]
    ctx["job_name"] = body["name"]
    ctx["job_capture_path"] = body["capture_path"]

    # Fetch it back to make sure it round-trips through the DB correctly.
    status, fetched = client.get(f"/api/jobs/{ctx['job_id']}", expect=200)
    assert fetched["name"] == "e2e-test-job", f"unexpected job on GET: {fetched}"

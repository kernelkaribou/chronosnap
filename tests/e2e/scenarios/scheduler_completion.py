"""
A dedicated short-lived job (its own start/end window) to exercise the real
scheduler: it should pick up at least one grid capture, then transition to
'completed' on its own within a couple of scheduler cycles, firing both the
completion event and a webhook (fire-and-forget — not re-verified here since
webhook.py already proves delivery works; this only checks the event log and
job status transition actually happen end-to-end).
"""
import time
from datetime import datetime, timedelta, timezone

PICSUM_URL = "https://picsum.photos/seed/chronosnap-e2e/200.jpg"
POLL_TIMEOUT_SECONDS = 60


def run(ctx):
    client = ctx["client"]
    now = datetime.now(timezone.utc)
    payload = {
        "name": "e2e-scheduler-job",
        "url": PICSUM_URL,
        "stream_type": "http",
        "start_datetime": now.isoformat(),
        "end_datetime": (now + timedelta(seconds=15)).isoformat(),
        "interval_seconds": 10,
        "framerate": 30,
    }
    status, body = client.post("/api/jobs/", json_body=payload, expect=201)
    job_id = body["id"]
    ctx["scheduler_job_id"] = job_id

    final_status = None
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, job = client.get(f"/api/jobs/{job_id}", expect=200)
        if job["status"] == "completed":
            final_status = job["status"]
            break
        time.sleep(3)
    assert final_status == "completed", (
        f"job never reached 'completed' via the real scheduler within {POLL_TIMEOUT_SECONDS}s "
        f"(last status: {job['status']})"
    )

    status, events = client.get("/api/events/", expect=200)
    messages = [e["message"] for e in events]
    assert any("e2e-scheduler-job" in m and "completed" in m for m in messages), (
        f"expected a scheduler-driven job-completed event, got: {messages}"
    )

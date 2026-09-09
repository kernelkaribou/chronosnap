"""Storage dashboard should reflect the test job's captures/videos, and the
event log should show the job-created and video-build-completed events."""


def run(ctx):
    client = ctx["client"]
    job_id = ctx["job_id"]

    status, stats = client.get("/api/storage/stats", expect=200)
    job_stats = next((j for j in stats["jobs"] if j["job_id"] == job_id), None)
    assert job_stats is not None, f"job {job_id} missing from storage stats: {stats}"
    assert job_stats["capture_count"] >= 5, f"unexpected capture_count: {job_stats}"
    assert job_stats["video_count"] >= 1, f"unexpected video_count: {job_stats}"

    status, events = client.get("/api/events/", expect=200)
    messages = [e["message"] for e in events]
    assert any(ctx["job_name"] in m and "created" in m for m in messages), (
        f"expected a job-created event, got: {messages}"
    )
    assert any("build completed" in m for m in messages), (
        f"expected a video build-completed event, got: {messages}"
    )

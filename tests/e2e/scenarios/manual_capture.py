"""
Trigger deterministic captures via the manual on-demand endpoint rather than
waiting on the real scheduler (the shared job's start_datetime is in the
future, so the scheduler itself never touches it — see job_lifecycle.py).
"""
import os
import time

CAPTURE_COUNT = 5


def run(ctx):
    client = ctx["client"]
    job_id = ctx["job_id"]

    for i in range(CAPTURE_COUNT):
        status, body = client.post(f"/api/jobs/{job_id}/capture", expect=200)
        assert body.get("success") is True, f"manual capture {i} failed: {body}"
        time.sleep(0.5)

    status, body = client.get(f"/api/captures/?job_id={job_id}&page_size=100", expect=200)
    assert body["total"] >= CAPTURE_COUNT, (
        f"expected >= {CAPTURE_COUNT} captures, got total={body.get('total')}: {body}"
    )
    captures = body["captures"]
    ctx["capture_ids"] = [c["id"] for c in captures]
    assert len(ctx["capture_ids"]) >= CAPTURE_COUNT

    # Verify the files actually landed on disk (via the read-only /captures
    # mount). Captures are nested under date-based subfolders
    # (job_dir/{year}/{month}/{day}/{hour}/file.jpg), with a separate
    # sharded thumbs/ tree — walk recursively rather than listing the top
    # level, and exclude thumbs/ so we're counting source captures only.
    job_dir = os.path.join("/captures", ctx["job_capture_path"])
    assert os.path.isdir(job_dir), f"expected capture directory to exist: {job_dir}"
    on_disk = []
    for root, dirs, files in os.walk(job_dir):
        if "thumbs" in dirs:
            dirs.remove("thumbs")
        on_disk.extend(os.path.join(root, f) for f in files)
    assert len(on_disk) >= CAPTURE_COUNT, (
        f"expected >= {CAPTURE_COUNT} files on disk under {job_dir}, found {len(on_disk)}: {on_disk}"
    )

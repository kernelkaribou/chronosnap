"""
Build a real video from the manual captures, then validate the produced file
structurally with ffprobe (reusing the app's own image, since test-runner is
the same built image with its entrypoint overridden — no second ffmpeg
install, no docker exec, no Docker socket access).
"""
import os
import subprocess
import time

BUILD_TIMEOUT_SECONDS = 90


def run(ctx):
    client = ctx["client"]
    capture_ids = ctx["capture_ids"]
    assert capture_ids, "no capture_ids in ctx — manual_capture scenario must run first"

    payload = {
        "job_id": ctx["job_id"],
        "name": "e2e-test-video",
        "resolution": "320x240",
        "framerate": 5,
        "quality": "low",
        "start_capture_id": min(capture_ids),
        "end_capture_id": max(capture_ids),
    }
    status, body = client.post("/api/videos/", json_body=payload, expect=201)
    video_id = body["id"]
    ctx["video_id"] = video_id
    assert body["status"] in ("processing", "completed"), f"unexpected initial status: {body}"

    final = None
    deadline = time.time() + BUILD_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, v = client.get(f"/api/videos/{video_id}", expect=200)
        if v["status"] in ("completed", "failed"):
            final = v
            break
        time.sleep(2)
    assert final is not None, f"video build did not finish within {BUILD_TIMEOUT_SECONDS}s"
    assert final["status"] == "completed", f"video build failed: {final}"
    assert final["duration_seconds"] > 0, f"non-positive duration reported: {final}"
    ctx["video_file_relpath"] = final["file_path"]

    abs_path = os.path.join("/timelapses", final["file_path"])
    assert os.path.isfile(abs_path), f"expected video file on disk: {abs_path}"

    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            abs_path,
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"ffprobe failed on {abs_path}: {result.stderr}"
    probed_duration = float(result.stdout.strip())
    assert probed_duration > 0, f"ffprobe reports non-positive duration: {result.stdout!r}"

    # Also validate the download endpoint returns a structurally valid MP4.
    status, mp4_bytes = client.get(f"/api/videos/{video_id}/download", raw=True, expect=200)
    assert len(mp4_bytes) > 100, "downloaded video is suspiciously small"
    assert b"ftyp" in mp4_bytes[:64], f"missing MP4 'ftyp' box in downloaded file header"

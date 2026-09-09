"""
Verifies the two distinct deletion contracts:
  - DELETE /api/videos/{id} removes the video file/thumbnail and cleans up
    empty parent directories.
  - DELETE /api/jobs/{id} removes capture records/files (job deletion does
    NOT touch already-built videos — that's why the video is deleted first).
Runs last, after every other scenario, so it also acts as final cleanup of
everything this suite created (on top of the Compose teardown in run.sh,
which would remove it all anyway via named-volume teardown).
"""
import os


def run(ctx):
    client = ctx["client"]

    video_id = ctx["video_id"]
    video_abs_path = os.path.join("/timelapses", ctx["video_file_relpath"])
    assert os.path.isfile(video_abs_path), f"expected video file to still exist: {video_abs_path}"

    client.delete(f"/api/videos/{video_id}", expect=204)
    status, body = client.get(f"/api/videos/{video_id}")
    assert status == 404, f"expected 404 after video delete, got {status}: {body}"
    assert not os.path.exists(video_abs_path), f"video file should be removed: {video_abs_path}"

    job_dir = os.path.join("/captures", ctx["job_capture_path"])
    assert os.path.isdir(job_dir), f"expected job capture directory to still exist: {job_dir}"

    client.delete(f"/api/jobs/{ctx['job_id']}", expect=204)
    status, body = client.get(f"/api/jobs/{ctx['job_id']}")
    assert status == 404, f"expected 404 after job delete, got {status}: {body}"
    assert not os.path.exists(job_dir), f"job capture directory should be removed: {job_dir}"

    client.delete(f"/api/jobs/{ctx['scheduler_job_id']}", expect=204)
    client.delete(f"/api/tags/{ctx['tag_id']}", expect=204)

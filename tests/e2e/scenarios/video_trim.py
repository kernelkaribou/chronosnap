"""Whole-second trimming of real public-image builds without modifying sources."""
import hashlib
import os
from pathlib import Path

from scenarios.video_join import frame_hashes, wait_video


def run(ctx):
    client = ctx["client"]
    ctx["unauthenticated_client"].post("/api/videos/1/trim/validate", json_body={}, expect=401)
    ctx["unauthenticated_client"].post("/api/videos/1/trim", json_body={
        "name": "Unauthorized", "revision": "0" * 64, "trim_start_seconds": 1,
    }, expect=401)
    created = []
    try:
        _, video = client.post("/api/videos/", json_body={
            "job_id": ctx["job_id"], "name": "e2e-trim-source",
            "resolution": "320x240", "framerate": 1, "quality": "high",
            "start_capture_id": min(ctx["capture_ids"]), "end_capture_id": max(ctx["capture_ids"]),
        }, expect=201)
        created.append(video["id"])
        source = wait_video(client, video["id"])
        source_path = Path("/timelapses") / source["file_path"]
        original = hashlib.sha256(source_path.read_bytes()).digest()
        _, report = client.post(f"/api/videos/{source['id']}/trim/validate", json_body={}, expect=200)
        assert report["total_frames"] >= 5 and report["framerate"] == 1
        # Ignore deliberately untrusted DB metadata: validation uses the real file.
        amount = report["max_trim_seconds"]
        assert amount >= 4
        client.post(f"/api/videos/{source['id']}/trim/validate", json_body={
            "trim_start_seconds": amount + 1,
        }, expect=422)
        client.post(f"/api/videos/{source['id']}/trim", json_body={
            "name": "Stale", "trim_start_seconds": 1, "revision": "0" * 64,
        }, expect=409)
        _, trimmed = client.post(f"/api/videos/{source['id']}/trim", json_body={
            "name": "e2e-one-second", "trim_start_seconds": amount, "revision": report["revision"],
        }, expect=201)
        created.append(trimmed["id"])
        trimmed = wait_video(client, trimmed["id"])
        output = Path("/timelapses") / trimmed["file_path"]
        assert trimmed["status"] == "completed" and trimmed["build_source"] == "trimmed"
        assert trimmed["duration_seconds"] == 1 and len(frame_hashes(str(output))) == 1
        assert trimmed["job_id"] == source["job_id"] and trimmed["start_capture_id"] is None
        assert hashlib.sha256(source_path.read_bytes()).digest() == original
        _, download = client.get(f"/api/videos/{trimmed['id']}/download", raw=True, expect=200)
        assert download == output.read_bytes()
        client.get(f"/api/videos/{trimmed['id']}/thumbnail", raw=True, expect=200)
        _, gif = client.get(f"/api/videos/{trimmed['id']}/gif", raw=True, expect=200)
        assert gif[:6] in (b"GIF87a", b"GIF89a")
        # Trim a separate longer selection again, then inspect Join eligibility.
        _, longer = client.post(f"/api/videos/{source['id']}/trim", json_body={
            "name": "e2e-longer-trim", "trim_end_seconds": 1, "revision": report["revision"],
        }, expect=201)
        created.append(longer["id"])
        longer = wait_video(client, longer["id"])
        _, next_report = client.post(f"/api/videos/{longer['id']}/trim/validate", json_body={}, expect=200)
        _, again = client.post(f"/api/videos/{longer['id']}/trim", json_body={
            "name": "e2e-repeat-trim", "trim_start_seconds": 1, "revision": next_report["revision"],
        }, expect=201)
        created.append(again["id"])
        again = wait_video(client, again["id"])
        assert again["duration_seconds"] == source["duration_seconds"] - 2
        _, join_report = client.post("/api/videos/join/validate", json_body={
            "video_ids": [again["id"], again["id"]],
        }, expect=200)
        assert join_report["compatible"], join_report
        client.delete(f"/api/videos/{source['id']}", expect=204)
        created.remove(source["id"])
        assert not source_path.exists() and output.is_file()
        client.get(f"/api/videos/{trimmed['id']}/download", raw=True, expect=200)
    finally:
        for video_id in reversed(created):
            _, video = client.get(f"/api/videos/{video_id}", expect=200)
            path = os.path.join("/timelapses", video["file_path"])
            client.delete(f"/api/videos/{video_id}", expect=204)
            assert not os.path.exists(path)

"""Preserved and explicitly converted joins of real builds, including repetitions."""
import hashlib
import os
from pathlib import Path
import subprocess
import time


def wait_video(client, video_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        _, video = client.get(f"/api/videos/{video_id}", expect=200)
        if video["status"] == "failed":
            raise AssertionError(f"video processing failed: {video}")
        if video["status"] == "completed":
            return video
        time.sleep(0.5)
    raise AssertionError(f"video {video_id} did not complete")


def frame_hashes(path):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
        "-fps_mode", "passthrough", "-f", "framemd5", "-",
    ], check=True, capture_output=True, text=True, timeout=30)
    return [line.rsplit(",", 1)[1].strip() for line in result.stdout.splitlines()
            if line and not line.startswith("#")]


def run(ctx):
    client = ctx["client"]
    ctx["unauthenticated_client"].post(
        "/api/videos/join/validate", json_body={"video_ids": [1, 2]}, expect=401,
    )
    created = []
    try:
        sources = []
        for label in ("A", "B"):
            _, video = client.post("/api/videos/", json_body={
                "job_id": ctx["job_id"], "name": f"e2e-join-source-{label}",
                "resolution": "320x240", "framerate": 5, "quality": "high",
                "start_capture_id": min(ctx["capture_ids"]),
                "end_capture_id": max(ctx["capture_ids"]),
                "text_overlay": {"enabled": True, "text": f"SOURCE {label}", "font_size": 12},
            }, expect=201)
            created.append(video["id"])
            sources.append(wait_video(client, video["id"]))
        paths = [os.path.join("/timelapses", v["file_path"]) for v in sources]
        original_bytes = [hashlib.sha256(Path(p).read_bytes()).digest() for p in paths]
        source_hashes = [frame_hashes(p) for p in paths]
        assert source_hashes[0] != source_hashes[1], "sources must distinguish playback order"
        ordered = [sources[1]["id"], sources[0]["id"], sources[1]["id"]]
        _, report = client.post("/api/videos/join/validate", json_body={"video_ids": ordered}, expect=200)
        assert report["compatible"], report
        _, video = client.post("/api/videos/join", json_body={
            "name": "e2e-joined", "video_ids": ordered,
            "revisions": [clip["revision"] for clip in report["clips"]],
        }, expect=201)
        created.append(video["id"])
        result = wait_video(client, video["id"])
        output = os.path.join("/timelapses", result["file_path"])
        assert frame_hashes(output) == source_hashes[1] + source_hashes[0] + source_hashes[1]
        assert result["total_frames"] == len(source_hashes[1]) * 2 + len(source_hashes[0])
        assert result["build_source"] == "joined" and result["quality"] == "preserved"
        assert result["start_time"] is None and result["job_id"] is None
        assert "join_sources" not in result
        assert [hashlib.sha256(Path(p).read_bytes()).digest() for p in paths] == original_bytes
        _, downloaded = client.get(f"/api/videos/{video['id']}/download", raw=True, expect=200)
        assert downloaded == Path(output).read_bytes()
        client.get(f"/api/videos/{video['id']}/thumbnail", raw=True, expect=200)
        _, gif = client.get(f"/api/videos/{video['id']}/gif", raw=True, expect=200)
        assert gif[:6] in (b"GIF87a", b"GIF89a")
        client.post("/api/videos/favorite", json_body={"ids": [video["id"]], "is_favorite": True}, expect=200)
        _, matches = client.get("/api/videos/?status=completed&search=e2e-joined&limit=1&offset=0", expect=200)
        assert [v["id"] for v in matches] == [video["id"]]
        _, stats = client.get("/api/storage/stats", expect=200)
        assert stats["videos_total_count"] >= 4
        _, fast = client.post("/api/videos/", json_body={
            "job_id": ctx["job_id"], "name": "e2e-join-fast",
            "resolution": "320x240", "framerate": 10, "quality": "high",
            "start_capture_id": min(ctx["capture_ids"]),
            "end_capture_id": max(ctx["capture_ids"]),
            "text_overlay": {"enabled": True, "text": "FAST SOURCE", "font_size": 12},
        }, expect=201)
        created.append(fast["id"])
        fast = wait_video(client, fast["id"])
        fast_path = os.path.join("/timelapses", fast["file_path"])
        fast_bytes = Path(fast_path).read_bytes()
        mixed_ids = [fast["id"], sources[0]["id"], fast["id"]]
        _, report = client.post("/api/videos/join/validate", json_body={
            "video_ids": mixed_ids, "output_fps": 5,
        }, expect=200)
        assert report["compatible"], report
        assert report["clips"][0]["speed_ratio"] == 0.5
        _, mixed = client.post("/api/videos/join", json_body={
            "name": "e2e-shared-fps", "video_ids": mixed_ids, "output_fps": 5,
            "revisions": [clip["revision"] for clip in report["clips"]],
        }, expect=201)
        created.append(mixed["id"])
        mixed = wait_video(client, mixed["id"])
        mixed_path = os.path.join("/timelapses", mixed["file_path"])
        assert frame_hashes(mixed_path) == frame_hashes(fast_path) + source_hashes[0] + frame_hashes(fast_path)
        assert mixed["framerate"] == 5
        assert abs(mixed["duration_seconds"] - report["duration_seconds"]) < 0.001
        assert "join_sources" not in mixed
        assert Path(fast_path).read_bytes() == fast_bytes
        _, small = client.post("/api/videos/", json_body={
            "job_id": ctx["job_id"], "name": "e2e-join-small",
            "resolution": "160x120", "framerate": 5, "quality": "high",
            "start_capture_id": min(ctx["capture_ids"]), "end_capture_id": max(ctx["capture_ids"]),
        }, expect=201)
        created.append(small["id"])
        small = wait_video(client, small["id"])
        selective_ids = [sources[0]["id"], small["id"], sources[0]["id"], small["id"]]
        selection = {"video_ids": selective_ids, "output_fps": 5, "output_resolution": "320x240"}
        _, report = client.post("/api/videos/join/validate", json_body=selection, expect=200)
        assert report["compatible"] and report["mode"] == "selective", report
        creation = {
            **selection, "name": "e2e-selective-join",
            "revisions": [clip["revision"] for clip in report["clips"]],
        }
        client.post("/api/videos/join", json_body=creation, expect=409)
        _, converted = client.post("/api/videos/join", json_body={
            **creation, "mode": "selective", "plan_token": report["plan_token"],
        }, expect=201)
        created.append(converted["id"])
        converted = wait_video(client, converted["id"])
        converted_hashes = frame_hashes(os.path.join("/timelapses", converted["file_path"]))
        original_count = len(source_hashes[0])
        assert converted_hashes[:original_count] == source_hashes[0]
        assert converted_hashes[original_count * 2:original_count * 3] == source_hashes[0]
        assert converted_hashes[original_count:original_count * 2] == converted_hashes[original_count * 3:]
        assert "join_sources" not in converted
        assert converted["quality"] == "partly_converted" and converted["resolution"] == "320x240"
        assert [hashlib.sha256(Path(p).read_bytes()).digest() for p in paths] == original_bytes
        client.delete(f"/api/videos/{sources[0]['id']}", expect=204)
        created.remove(sources[0]["id"])
        _, after = client.get(f"/api/videos/{video['id']}", expect=200)
        assert "join_sources" not in after
        assert after["quality"] == "preserved"
        _, converted_after = client.get(f"/api/videos/{converted['id']}", expect=200)
        assert converted_after["quality"] == "partly_converted"
        assert os.path.isfile(output)
    finally:
        for video_id in reversed(created):
            client.delete(f"/api/videos/{video_id}", expect=204)

"""Real-codec and lifecycle coverage; all media and state are disposable."""
import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from backend.database import get_db, init_db
from backend.models import VideoJoinCreate, VideoJoinRequest
from backend.routers import videos
from backend.services import video_joiner as joiner


def encode(path, *, crf=18, fps="30", frames=24, size="160x96", hue=0, audio=False, extra=()):
    args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate={fps},hue=h={hue}"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440", "-c:a", "aac"]
    subprocess.run([
        *args, "-frames:v", str(frames), "-c:v", "libx264", "-preset", "medium",
        "-crf", str(crf), "-pix_fmt", "yuv420p", *extra, "-y", str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


def hashes(path):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-fps_mode", "passthrough", "-f", "framemd5", "-",
    ], check=True, capture_output=True, text=True, timeout=30)
    return [line.rsplit(",", 1)[1].strip() for line in result.stdout.splitlines()
            if line and not line.startswith("#")]


def insert_video(path, *, name=None, status="completed", build_source="imported"):
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO processed_videos
            (name, file_path, file_size, resolution, framerate, quality, total_frames,
             duration_seconds, status, build_source, created_at, completed_at)
            VALUES (?, ?, ?, '1x1', 1, 'high', 1, 1, ?, ?, '2026-01-01', '2026-01-01')
        """, (name or path.stem, str(path), path.stat().st_size, status, build_source))
        return cursor.lastrowid


@pytest.fixture
def clips(isolated_db, videos_base):
    paths = [encode(Path(videos_base) / f"clip-{i}.mp4", hue=i * 90) for i in range(2)]
    return [(insert_video(path), path) for path in paths]


@pytest.fixture(autouse=True)
def clean_operations():
    yield
    with joiner._operations_lock:
        assert not any(op.process and op.process.poll() is None for op in joiner._operations.values())
        joiner._operations.clear()


def request_for(ids, name="Joined", output_fps=None):
    report, _ = joiner.analyze_join(ids, output_fps)
    assert report["compatible"], report
    return VideoJoinCreate(name=name, video_ids=ids, output_fps=output_fps,
                           revisions=[clip["revision"] for clip in report["clips"]])


def fetch(video_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone()
        return dict(row) if row else None


@pytest.mark.parametrize("crf", [28, 23, 18, 0])
def test_real_join_preserves_every_frame_including_repetitions(isolated_db, videos_base, crf):
    a = encode(Path(videos_base) / "a.mp4", crf=crf, hue=0)
    b = encode(Path(videos_base) / "b.mp4", crf=crf, hue=90)
    ids = [insert_video(a), insert_video(b)]
    original_bytes = [hashlib.sha256(p.read_bytes()).digest() for p in (a, b)]
    ordered = [ids[1], ids[0], ids[1]]
    video = joiner.create_join(request_for(ordered, "Bob's Joined Video"))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    output = Path(joiner.resolve_video_path(result["file_path"]))
    assert hashes(output) == hashes(b) + hashes(a) + hashes(b)
    assert result["total_frames"] == 72
    assert result["duration_seconds"] == pytest.approx(2.4)
    assert result["framerate"] == 30  # Not the deliberately inaccurate imported DB metadata.
    assert result["quality"] == "preserved"
    assert result["job_id"] is None and result["start_time"] is None
    assert Path(joiner.resolve_video_path(result["thumbnail_path"])).is_file()
    assert [hashlib.sha256(p.read_bytes()).digest() for p in (a, b)] == original_bytes
    detail = asyncio.run(videos.get_video(video["id"]))
    assert "join_sources" not in detail
    assert not joiner._operations
    # A completed joined video is eligible as a source and remains independent.
    second = joiner.create_join(request_for([video["id"], video["id"]], "Again"))
    joiner.process_join(second["id"])
    assert fetch(second["id"])["status"] == "completed"
    videos.delete_video(ids[0])
    detail = asyncio.run(videos.get_video(video["id"]))
    assert "join_sources" not in detail
    assert detail["quality"] == "preserved"
    assert output.is_file()


@pytest.mark.parametrize("change", [
    {"crf": 0}, {"crf": 28}, {"fps": "25"}, {"size": "192x96"},
    {"audio": True}, {"fps": "30000/1001"}, {"extra": ("-output_ts_offset", "1")},
])
def test_rejects_unsupported_or_mismatching_streams(clips, videos_base, change):
    path = encode(Path(videos_base) / "different.mp4", **change)
    report, _ = joiner.analyze_join([clips[0][0], insert_video(path)])
    assert not report["compatible"]
    assert report["clips"][1]["error"]


def test_missing_processing_and_invalid_files(clips, videos_base):
    bad = Path(videos_base) / "broken.mp4"
    bad.write_bytes(b"not a video")
    processing = insert_video(clips[0][1], status="processing")
    for source_id in [999999, processing, insert_video(bad)]:
        report, _ = joiner.analyze_join([clips[0][0], source_id])
        assert not report["compatible"]
        assert report["clips"][1]["error"]


def test_path_escape_and_control_characters_rejected(clips, tmp_path):
    outside = encode(tmp_path / "private.mp4")
    report, _ = joiner.analyze_join([clips[0][0], insert_video(outside)])
    assert not report["compatible"]
    assert "outside" in report["clips"][1]["error"]


def test_stale_preflight(clips):
    request = request_for([c[0] for c in clips])
    with get_db() as conn:
        conn.execute("UPDATE processed_videos SET name='Renamed' WHERE id=?", (clips[0][0],))
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request)
    assert error.value.status_code == 409


def test_one_active_join_and_atomic_mutation_guards(clips):
    ids = [c[0] for c in clips]
    request = request_for(ids)
    video = joiner.create_join(request)
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request)
    assert error.value.status_code == 409
    for action in [
        lambda: videos.rename_video(ids[0], videos.VideoRenameRequest(name="Changed")),
        lambda: videos.delete_video(ids[0]),
        lambda: videos.delete_multiple_videos(videos.BulkDeleteRequest(video_ids=ids)),
        lambda: videos.delete_video(video["id"]),
    ]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    assert all(path.is_file() for _, path in clips)
    assert joiner.cancel_join(video["id"])
    assert fetch(video["id"]) is None
    joiner.process_join(video["id"])  # Cancelled before background launch.
    assert all(path.is_file() for _, path in clips)


def test_concurrent_join_admission_is_atomic(clips, monkeypatch):
    ids = [c[0] for c in clips]
    request = request_for(ids)
    analysis = joiner._analyze_join(ids)
    ready = threading.Barrier(2)
    results = []
    errors = []

    def analyze(*args):
        ready.wait(timeout=10)
        return analysis

    def create():
        try:
            results.append(joiner.create_join(request))
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(joiner, "_analyze_join", analyze)
    workers = [threading.Thread(target=create) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 1 and len(errors) == 1, (results, errors)
    assert isinstance(errors[0], HTTPException) and errors[0].status_code == 409
    assert len(joiner._operations) == 1
    assert joiner.cancel_join(results[0]["id"])


def test_memory_claim_precedes_commit_and_is_released_on_rollback(clips, monkeypatch):
    request = request_for([c[0] for c in clips])

    @contextmanager
    def fail_commit():
        with get_db() as conn:
            yield conn
            if conn.in_transaction:
                row = conn.execute(
                    "SELECT id FROM processed_videos WHERE build_source='joined' AND status='processing'"
                ).fetchone()
                assert row
                with joiner._operations_lock:
                    operation = joiner._operations[row["id"]]
                    assert operation.video_ids == tuple(request.video_ids)
                raise sqlite3.OperationalError("Deliberate commit failure")

    monkeypatch.setattr(joiner, "get_db", fail_commit)
    with pytest.raises(sqlite3.OperationalError, match="Deliberate commit failure"):
        joiner.create_join(request)
    assert not joiner._operations
    with get_db() as conn:
        assert not conn.execute("SELECT id FROM processed_videos WHERE build_source='joined'").fetchall()
    assert all(path.is_file() for _, path in clips)


def test_join_keeps_schema_unchanged_and_discards_source_history(clips):
    with get_db() as conn:
        schema = [tuple(row) for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        )]
    assert not any("join" in row[1] for row in schema)
    request = request_for([clips[1][0], clips[0][0], clips[0][0]])
    video = joiner.create_join(request)
    request.video_ids.reverse()  # The operation owns an immutable snapshot.
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    assert hashes(joiner.resolve_video_path(result["file_path"])) == (
        hashes(clips[1][1]) + hashes(clips[0][1]) + hashes(clips[0][1])
    )
    assert not joiner._operations
    assert "join_sources" not in asyncio.run(videos.get_video(video["id"]))
    init_db()
    with get_db() as conn:
        assert [tuple(row) for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        )] == schema


def test_rollback_cannot_release_another_join_that_reuses_its_id(clips, monkeypatch):
    request = request_for([c[0] for c in clips])
    replacement = []
    rolled_back = []

    @contextmanager
    def fail_commit_and_retry():
        try:
            with get_db() as conn:
                yield conn
                if conn.in_transaction:
                    rolled_back.append(conn.execute(
                        "SELECT id FROM processed_videos WHERE build_source='joined' AND status='processing'"
                    ).fetchone()["id"])
                    raise sqlite3.OperationalError("Deliberate commit failure")
        except sqlite3.OperationalError:
            monkeypatch.setattr(joiner, "get_db", get_db)
            replacement.append(joiner.create_join(request))
            raise

    monkeypatch.setattr(joiner, "get_db", fail_commit_and_retry)
    with pytest.raises(sqlite3.OperationalError, match="Deliberate commit failure"):
        joiner.create_join(request)
    assert replacement[0]["id"] == rolled_back[0]
    assert replacement[0]["id"] in joiner._operations
    with pytest.raises(HTTPException) as error:
        videos.delete_video(clips[0][0])
    assert error.value.status_code == 409
    assert joiner.cancel_join(replacement[0]["id"])


def test_missing_source_after_admission_fails_instead_of_shortening_output(clips):
    video = joiner.create_join(request_for([c[0] for c in clips]))
    with get_db() as conn:
        conn.execute("DELETE FROM processed_videos WHERE id=?", (clips[0][0],))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "failed"
    assert "source" in result["error_message"].lower()
    assert not joiner._operations
    assert not Path(joiner.resolve_video_path(result["file_path"])).exists()


@pytest.mark.parametrize("kind", ["failed", "cancelled"])
def test_midprocess_failure_cleans_partial_and_releases_sources(clips, monkeypatch, kind):
    video = joiner.create_join(request_for([c[0] for c in clips]))

    def fail(args, *unused):
        Path(args[-1]).write_bytes(b"partial")
        if kind == "cancelled":
            raise joiner.JoinCancelled()
        raise joiner.JoinError("Deliberate copy failure")

    monkeypatch.setattr(joiner, "_media", fail)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result is None if kind == "cancelled" else result["status"] == "failed"
    assert not Path(joiner.resolve_video_path(video["file_path"])).parent.exists()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        joiner.assert_videos_mutable([c[0] for c in clips])


def test_source_modified_after_admission_not_accepted(clips):
    video = joiner.create_join(request_for([c[0] for c in clips]))
    os.utime(clips[0][1], ns=(1000000000, 1000000000))
    joiner.process_join(video["id"])
    assert fetch(video["id"])["status"] == "failed"
    assert "changed" in fetch(video["id"])["error_message"]


def test_restart_cleanup_and_schema_reinitialization(clips):
    video = joiner.create_join(request_for([c[0] for c in clips]))
    paths = joiner._owned_paths(video)
    Path(paths[0]).parent.mkdir(parents=True)
    for path in paths:
        Path(path).write_bytes(b"interrupted")
    working = Path(paths[0]).parent / ".join-timing" / "1.mp4"
    working.parent.mkdir()
    working.write_bytes(b"interrupted timing copy")
    with joiner._operations_lock:
        joiner._operations.clear()  # A restarted process has no active plans.
    init_db()
    joiner.recover_joins()
    assert fetch(video["id"])["status"] == "failed"
    assert all(not Path(path).exists() for path in paths)
    assert not working.exists()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        joiner.assert_videos_mutable([c[0] for c in clips])


def test_space_failure_does_not_claim_inputs(clips, monkeypatch):
    request = request_for([c[0] for c in clips])
    usage = type("Usage", (), {"free": 0})()
    monkeypatch.setattr(joiner.shutil, "disk_usage", lambda path: usage)
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request)
    assert error.value.status_code == 507
    assert not joiner._operations


@pytest.mark.parametrize("ids", [[1], [1] * 101, [True, 1], [1.5, 2], [-1, 2]])
def test_request_bounds(ids):
    with pytest.raises(ValidationError):
        VideoJoinRequest(video_ids=ids)


def test_hundred_short_clips_do_not_accumulate_timestamp_rounding(isolated_db, videos_base):
    path = encode(Path(videos_base) / "short.mp4", frames=5)
    video_id = insert_video(path)
    joined = joiner.create_join(request_for([video_id] * 100))
    joiner.process_join(joined["id"])
    result = fetch(joined["id"])
    assert result["status"] == "completed", result
    assert hashes(joiner.resolve_video_path(result["file_path"])) == hashes(path) * 100


def test_hundred_entries_probe_each_distinct_source_only_once(clips, monkeypatch):
    calls = []
    probe = joiner.probe_video

    def inspect(path, operation=None):
        calls.append(path)
        return probe(path, operation)

    monkeypatch.setattr(joiner, "probe_video", inspect)
    report, sources = joiner.analyze_join([clips[0][0], clips[1][0]] * 50)
    assert report["compatible"], report
    assert len(report["clips"]) == len(sources) == 100
    assert calls == [str(clips[0][1]), str(clips[1][1])]
    assert report["duration_seconds"] == pytest.approx(80)


@pytest.mark.parametrize("phase", ["copy", "validation", "thumbnail"])
def test_cancellation_kills_and_waits_in_each_media_phase(clips, monkeypatch, phase):
    video = joiner.create_join(request_for([c[0] for c in clips]))
    ready = threading.Event()
    original_media = joiner._media
    processes = []

    def media(args, operation, *rest):
        current = "validation" if args[-1] == "-" else "thumbnail" if args[-1].endswith(".jpg") else "copy"
        if current != phase:
            return original_media(args, operation, *rest)
        for line in joiner._lines([
            sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)",
        ], operation):
            processes.append(operation.process)
            ready.set()

    monkeypatch.setattr(joiner, "_media", media)
    worker = threading.Thread(target=joiner.process_join, args=(video["id"],))
    worker.start()
    try:
        assert ready.wait(10)
        assert joiner.cancel_join(video["id"])
    finally:
        worker.join(timeout=15)
    assert not worker.is_alive()
    assert all(process.poll() is not None for process in processes)
    assert fetch(video["id"]) is None
    assert all(path.is_file() for _, path in clips)


def test_cancel_after_thumbnail_cannot_publish_completed_video(clips, monkeypatch):
    video = joiner.create_join(request_for([c[0] for c in clips]))
    original_media = joiner._media

    def media(args, operation, *rest):
        count = original_media(args, operation, *rest)
        if args[-1].endswith(".jpg"):
            operation.cancelled.set()
        return count

    monkeypatch.setattr(joiner, "_media", media)
    joiner.process_join(video["id"])
    assert fetch(video["id"]) is None
    assert not Path(joiner.resolve_video_path(video["file_path"])).exists()


def test_output_symlink_cannot_escape_timelapse_root(clips, videos_base, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(videos_base) / "joined").symlink_to(outside, target_is_directory=True)
    video = joiner.create_join(request_for([c[0] for c in clips]))
    joiner.process_join(video["id"])
    assert fetch(video["id"])["status"] == "failed"
    assert list(outside.iterdir()) == []


def test_stalled_subprocess_is_killed_and_waited(monkeypatch):
    monkeypatch.setattr(joiner.config, "VIDEO_BUILD_STALL_TIMEOUT", 0.1)
    processes = []
    original_popen = joiner.subprocess.Popen

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(joiner.subprocess, "Popen", popen)
    with pytest.raises(joiner.JoinError, match="stalled"):
        list(joiner._lines([sys.executable, "-c", "import time; time.sleep(60)"]))
    assert all(process.poll() is not None for process in processes)


def test_mutation_write_lock_spans_filesystem_rename(clips, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    errors = []
    original_rename = videos.os.rename

    def rename(*args):
        entered.set()
        assert release.wait(10)
        original_rename(*args)

    def mutate():
        try:
            videos.rename_video(clips[0][0], videos.VideoRenameRequest(name="Renamed"))
        except Exception as exc:
            errors.append(exc)

    # Use the normal nested layout expected by rename_video.
    with get_db() as conn:
        path = clips[0][1]
        directory = path.parent / "imported" / f"{clips[0][0]}_Old"
        directory.mkdir(parents=True)
        destination = directory / path.name
        path.replace(destination)
        conn.execute("UPDATE processed_videos SET file_path=? WHERE id=?", (str(destination), clips[0][0]))
    monkeypatch.setattr(videos.os, "rename", rename)
    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        assert entered.wait(10)
        with get_db() as conn:
            conn.execute("PRAGMA busy_timeout=0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
    finally:
        release.set()
        worker.join(timeout=15)
    assert not worker.is_alive() and not errors


def test_real_image_concat_builds_preserve_full_color_range(isolated_db, videos_base):
    root = Path(videos_base)
    image = root / "frame.jpg"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=200x200",
        "-frames:v", "1", str(image),
    ], check=True, capture_output=True, timeout=15)
    listing = root / "images.txt"
    listing.write_text("".join(f"file '{image}'\nduration 0.2\n" for _ in range(5)))
    source = root / "native.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=320x240", "-r", "5", "-c:v", "libx264", "-crf", "18",
        "-preset", "medium", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, timeout=15)
    assert joiner._metadata(str(source), None)["streams"][0]["pix_fmt"] == "yuvj420p"
    source_id = insert_video(source)
    video = joiner.create_join(request_for([source_id, source_id]))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    output = joiner.resolve_video_path(result["file_path"])
    assert joiner._metadata(output, None)["streams"][0]["pix_fmt"] == "yuvj420p"
    assert hashes(output) == hashes(source) * 2
    small = root / "small-native.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=160x120", "-r", "5", "-c:v", "libx264", "-crf", "18",
        "-preset", "medium", "-pix_fmt", "yuv420p", str(small),
    ], check=True, capture_output=True, timeout=15)
    ids = [source_id, insert_video(small), source_id]
    request, report = conversion_request(ids, "320x240", fps=5)
    assert report["mode"] == "selective", report
    video = joiner.create_join(request)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result["error_message"]
    joined_hashes = hashes(joiner.resolve_video_path(result["file_path"]))
    assert joined_hashes[:5] == hashes(source) == joined_hashes[-5:]
    assert len(joined_hashes) == 15


def test_conversion_without_explicit_fps_reports_actual_target_duration(isolated_db, videos_base):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps), size=size, frames=15)
             for fps, size in [(5, "192x96"), (10, "160x96")]]
    ids = [insert_video(path) for path in paths]
    report, _ = joiner.analyze_join(ids, output_resolution="192x96")
    assert report["compatible"], report
    assert report["output_fps"] == 5
    assert report["duration_seconds"] == 6
    assert report["clips"][1]["output_duration_seconds"] == 3
    assert report["clips"][1]["speed_ratio"] == 0.5


def test_shutdown_timeout_is_logged_not_swallowed(clips, monkeypatch, caplog):
    video = joiner.create_join(request_for([c[0] for c in clips]))

    def slow_cancel(video_id):
        raise HTTPException(503, "Cancellation requested; the join is still stopping")

    monkeypatch.setattr(joiner, "cancel_join", slow_cancel)
    joiner.shutdown_joins()
    assert f"Join {video['id']} is still stopping during shutdown" in caplog.text


def test_timestamp_warning_is_not_hidden_by_a_large_stderr_tail():
    with pytest.raises(joiner.JoinError, match="timestamps"):
        list(joiner._lines([
            sys.executable, "-c",
            "import sys; sys.stderr.write('Non-monotonic DTS\\n' + 'x' * 100000); print('done')",
        ]))


@pytest.mark.parametrize("crf", [28, 23, 18, 0])
@pytest.mark.parametrize("output_fps", [5, 10])
def test_mixed_fps_preserves_every_frame_and_changes_only_speed(isolated_db, videos_base, crf, output_fps):
    a = encode(Path(videos_base) / "a.mp4", fps="5", crf=crf, frames=17)
    b = encode(Path(videos_base) / "b.mp4", fps="10", crf=crf, frames=23, hue=90)
    ids = [insert_video(a), insert_video(b)]
    originals = [hashlib.sha256(path.read_bytes()).digest() for path in (a, b)]
    ordered = [ids[1], ids[0], ids[1]]
    report, _ = joiner.analyze_join(ordered, output_fps)
    assert report["compatible"], report
    assert report["available_fps"] == [5, 10]
    assert report["duration_seconds"] == pytest.approx(63 / output_fps)
    assert report["clips"][0]["source_fps"] == 10
    assert report["clips"][0]["output_duration_seconds"] == pytest.approx(23 / output_fps)
    assert report["clips"][0]["speed_ratio"] == pytest.approx(output_fps / 10)
    assert report["temporary_bytes"] == a.stat().st_size + b.stat().st_size
    joined = joiner.create_join(request_for(ordered, output_fps=output_fps))
    joiner.process_join(joined["id"])
    result = fetch(joined["id"])
    assert result["status"] == "completed", result["error_message"]
    output = Path(joiner.resolve_video_path(result["file_path"]))
    assert hashes(output) == hashes(b) + hashes(a) + hashes(b)
    assert result["framerate"] == output_fps and result["total_frames"] == 63
    assert result["duration_seconds"] == pytest.approx(63 / output_fps)
    assert not (output.parent / ".join-timing").exists()
    assert [hashlib.sha256(path.read_bytes()).digest() for path in (a, b)] == originals
    again = joiner.create_join(request_for([joined["id"], joined["id"]], output_fps=output_fps))
    joiner.process_join(again["id"])
    assert fetch(again["id"])["status"] == "completed"


@pytest.mark.parametrize("fps_pair", [(24, 30), (25, 30)])
def test_retiming_uses_exact_frame_grid_for_nondivisible_fps(isolated_db, videos_base, fps_pair):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps), frames=7,
                    extra=("-level:v", "1.1")) for fps in fps_pair]
    ids = [insert_video(path) for path in paths]
    video = joiner.create_join(request_for([ids[0], ids[1], ids[0]], output_fps=fps_pair[1]))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    assert hashes(joiner.resolve_video_path(result["file_path"])) == hashes(paths[0]) + hashes(paths[1]) + hashes(paths[0])


def test_shared_fps_does_not_override_incompatible_encoding(clips, videos_base):
    other = encode(Path(videos_base) / "maximum.mp4", crf=0, fps="25")
    report, _ = joiner.analyze_join([clips[0][0], insert_video(other)], 30)
    assert not report["compatible"]
    assert "Codec configuration" in report["clips"][1]["error"]


def test_output_fps_must_be_an_actual_source_rate(clips):
    with pytest.raises(HTTPException) as error:
        joiner.analyze_join([clip[0] for clip in clips], 5)
    assert error.value.status_code == 422
    assert error.value.detail["available_fps"] == [30]
    for invalid in (True, 0, -1, 5.5):
        with pytest.raises(ValidationError):
            VideoJoinRequest(video_ids=[1, 2], output_fps=invalid)


def test_cancel_during_retiming_cleans_working_copies(isolated_db, videos_base, monkeypatch):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps)) for fps in (5, 10)]
    ids = [insert_video(path) for path in paths]
    video = joiner.create_join(request_for(ids, output_fps=5))

    def cancel(args, *unused):
        assert "-bsf:v" in args and args[args.index("-c") + 1] == "copy"
        Path(args[-1]).write_bytes(b"partial timing copy")
        raise joiner.JoinCancelled()

    monkeypatch.setattr(joiner, "_media", cancel)
    joiner.process_join(video["id"])
    assert fetch(video["id"]) is None
    assert not Path(joiner.resolve_video_path(video["file_path"])).parent.exists()
    assert all(path.is_file() for path in paths)


def test_retiming_space_admission_includes_unique_working_copies(isolated_db, videos_base, monkeypatch):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps)) for fps in (5, 10)]
    ids = [insert_video(path) for path in paths]
    request = request_for([ids[0], ids[1], ids[0]], output_fps=5)
    report, _ = joiner.analyze_join(request.video_ids, request.output_fps)
    usage = type("Usage", (), {"free": report["estimated_bytes"] + 16 * 1024 * 1024})()
    monkeypatch.setattr(joiner.shutil, "disk_usage", lambda path: usage)
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request)
    assert error.value.status_code == 507
    assert not joiner._operations


def test_hundred_mixed_fps_entries_keep_exact_boundaries(isolated_db, videos_base):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps), frames=5,
                    extra=("-level:v", "1.1")) for fps in (25, 30)]
    ids = [insert_video(path) for path in paths]
    video = joiner.create_join(request_for(ids * 50, output_fps=30))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result["error_message"]
    assert hashes(joiner.resolve_video_path(result["file_path"])) == (hashes(paths[0]) + hashes(paths[1])) * 50


@pytest.mark.parametrize("name", ["join-source-1", "join-source-1.partial"])
def test_output_names_cannot_collide_with_timing_copies(isolated_db, videos_base, name):
    paths = [encode(Path(videos_base) / f"{fps}.mp4", fps=str(fps)) for fps in (5, 10)]
    ids = [insert_video(path) for path in paths]
    video = joiner.create_join(request_for(ids, name=name, output_fps=5))
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result["error_message"]
    assert hashes(joiner.resolve_video_path(result["file_path"])) == hashes(paths[0]) + hashes(paths[1])


def conversion_request(ids, resolution, fps=30, name="Converted join"):
    report, _ = joiner.analyze_join(ids, fps, resolution)
    assert report["compatible"], report
    return VideoJoinCreate(
        name=name, video_ids=ids, output_fps=fps, output_resolution=resolution,
        revisions=[clip["revision"] for clip in report["clips"]],
        mode=report["mode"], plan_token=report.get("plan_token"),
    ), report


@pytest.mark.parametrize("codec", ["h264", "mpeg4"])
def test_selective_conversion_preserves_reference_and_reuses_repeated_outlier(isolated_db, videos_base, codec, monkeypatch):
    reference = encode(Path(videos_base) / "reference.mp4", size="192x96", frames=17)
    other = Path(videos_base) / "other.mp4"
    if codec == "h264":
        encode(other, size="96x64", fps="5", frames=11, hue=90)
    else:
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=5",
            "-frames:v", "11", "-c:v", "mpeg4", "-q:v", "3", str(other),
        ], check=True, capture_output=True, timeout=30)
    ids = [insert_video(reference), insert_video(other)]
    originals = [path.read_bytes() for path in (reference, other)]
    request, report = conversion_request([ids[0], ids[1], ids[0], ids[1]], "192x96")
    assert report["mode"] == "selective", report
    assert [clip["action"] for clip in report["clips"]] == ["retime", "reencode", "retime", "reencode"]
    assert report["clips"][1]["padding"]
    encoded_inputs = []
    media = joiner._media

    def record_encoding(args, *rest):
        if "-c:v" in args:
            encoded_inputs.append(args[args.index("-i") + 1])
        return media(args, *rest)

    monkeypatch.setattr(joiner, "_media", record_encoding)
    result = joiner.create_join(request)
    joiner.process_join(result["id"])
    result = fetch(result["id"])
    assert result["status"] == "completed", result["error_message"]
    output = Path(joiner.resolve_video_path(result["file_path"]))
    frames = hashes(output)
    assert len(frames) == 56
    assert frames[:17] == hashes(reference) == frames[28:45]
    assert frames[17:28] == frames[45:]
    assert encoded_inputs == [str(other)]
    assert [path.read_bytes() for path in (reference, other)] == originals
    assert result["quality"] == "partly_converted"
    detail = asyncio.run(videos.get_video(result["id"]))
    assert "join_sources" not in detail
    assert not joiner._operations
    assert not (output.parent / ".join-timing").exists()
    pixels = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(output), "-vf", "select=eq(n\\,17)",
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], check=True, capture_output=True, timeout=30).stdout
    assert len(pixels) == 192 * 96 * 3
    assert max(pixels[(row * 192 + 4) * 3 + channel] for row in range(96) for channel in range(3)) <= 4
    assert max(pixels[(48 * 192 + 48) * 3:(48 * 192 + 144) * 3]) > 30
    videos.delete_video(ids[1])
    assert asyncio.run(videos.get_video(result["id"]))["quality"] == "partly_converted"
    assert output.is_file()


def test_resolution_selection_does_not_force_unnecessary_encoding(clips):
    request, report = conversion_request([clip[0] for clip in clips], "160x96")
    assert report["mode"] == "copy"
    assert request.plan_token is None
    video = joiner.create_join(request)
    joiner.process_join(video["id"])
    assert hashes(joiner.resolve_video_path(fetch(video["id"])["file_path"])) == hashes(clips[0][1]) + hashes(clips[1][1])


def test_conversion_requires_current_explicit_plan_consent(isolated_db, videos_base):
    paths = [encode(Path(videos_base) / f"{width}.mp4", size=f"{width}x96") for width in (160, 192)]
    ids = [insert_video(path) for path in paths]
    request, report = conversion_request(ids, "192x96")
    assert report["mode"] == "selective"
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request.model_copy(update={"mode": "copy", "plan_token": None}))
    assert error.value.status_code == 409
    for changes in ({"plan_token": "0" * 64}, {"output_resolution": "160x96"}, {"video_ids": ids[::-1]}):
        with pytest.raises(HTTPException) as error:
            joiner.create_join(request.model_copy(update=changes))
        assert error.value.status_code == 409
    assert not joiner._operations


def test_unreproducible_reference_proposes_explicit_all_conversion(isolated_db, videos_base):
    a = encode(Path(videos_base) / "maximum.mp4", size="192x96", crf=0, frames=30)
    b = encode(Path(videos_base) / "smaller.mp4", frames=11)
    ids = [insert_video(a), insert_video(b)]
    request, report = conversion_request(ids, "192x96")
    assert report["mode"] == "all"
    assert all(clip["action"] == "reencode" for clip in report["clips"])
    with pytest.raises(HTTPException):
        joiner.create_join(request.model_copy(update={"mode": "selective"}))
    video = joiner.create_join(request)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result["error_message"]
    assert result["total_frames"] == 41
    assert result["quality"] == "high"
    assert "join_sources" not in asyncio.run(videos.get_video(result["id"]))


@pytest.mark.parametrize("failure", ["cancel", "configuration", "disk"])
def test_conversion_failure_cleans_owned_files_and_never_falls_back_silently(isolated_db, videos_base, monkeypatch, failure):
    paths = [encode(Path(videos_base) / f"{width}.mp4", size=f"{width}x96") for width in (160, 192)]
    ids = [insert_video(path) for path in paths]
    request, _ = conversion_request(ids, "192x96")
    video = joiner.create_join(request)
    original_media = joiner._media

    def media(args, operation, *rest):
        if "-c:v" in args:
            if failure == "cancel":
                Path(args[-1]).write_bytes(b"partial")
                raise joiner.JoinCancelled()
            if failure == "configuration":
                raise joiner.JoinError("Converted stream does not match the approved plan")
            usage = type("Usage", (), {"free": 0})()
            monkeypatch.setattr(joiner.shutil, "disk_usage", lambda path: usage)
        return original_media(args, operation, *rest)

    monkeypatch.setattr(joiner, "_media", media)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result is None if failure == "cancel" else result["status"] == "failed"
    assert not Path(joiner.resolve_video_path(video["file_path"])).parent.exists()
    assert all(path.is_file() for path in paths)


def test_conversion_storage_admission_accounts_for_encoded_bounds(isolated_db, videos_base, monkeypatch):
    paths = [encode(Path(videos_base) / f"{width}.mp4", size=f"{width}x96") for width in (160, 192)]
    ids = [insert_video(path) for path in paths]
    request, report = conversion_request(ids, "192x96")
    assert report["estimated_bytes"] > sum(path.stat().st_size for path in paths)
    usage = type("Usage", (), {"free": report["estimated_bytes"] + 16 * 1024 * 1024})()
    monkeypatch.setattr(joiner.shutil, "disk_usage", lambda path: usage)
    with pytest.raises(HTTPException) as error:
        joiner.create_join(request)
    assert error.value.status_code == 507
    assert not joiner._operations


def test_unsupported_audio_is_not_silently_dropped_in_conversion(clips, videos_base):
    audio = encode(Path(videos_base) / "audio.mp4", size="192x96", audio=True)
    report, _ = joiner.analyze_join([clips[0][0], insert_video(audio)], 30, "160x96")
    assert not report["compatible"]
    assert "silent" in report["clips"][1]["error"]


def test_display_aspect_is_used_instead_of_coded_pixel_ratio():
    square_display = {"width": 1280, "height": 720, "sample_aspect_ratio": "9:16"}
    assert joiner.fit_dimensions(square_display, {"sample_aspect_ratio": "1:1"}, 1920, 1080) == (1080, 1080)
    assert joiner.fit_dimensions(square_display, square_display, 1280, 720) == (1280, 720)


def test_selective_conversion_matches_absent_reference_pixel_aspect_tag(isolated_db, videos_base):
    reference = encode(Path(videos_base) / "untagged-sar.mp4", extra=("-vf", "setsar=0"), frames=11)
    outlier = encode(Path(videos_base) / "non-square-sar.mp4", size="192x96",
                     extra=("-vf", "setsar=1/2"), frames=17)
    assert joiner.probe_video(str(reference)).stream.get("sample_aspect_ratio") is None
    ids = [insert_video(reference), insert_video(outlier)]
    request, report = conversion_request(ids, "160x96")
    assert report["mode"] == "selective", report
    assert report["clips"][1]["scaling"] == "downscale"
    assert report["clips"][1]["padding"]
    video = joiner.create_join(request)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result["error_message"]
    assert hashes(joiner.resolve_video_path(result["file_path"]))[:11] == hashes(reference)


@pytest.mark.parametrize("target_range", ["pc", "tv"])
def test_full_range_conversion_preserves_black_and_white_levels(isolated_db, videos_base, target_range):
    root = Path(videos_base)
    extra = ("-vf", f"scale=in_range=tv:out_range={target_range}",
             "-pix_fmt", "yuvj420p" if target_range == "pc" else "yuv420p",
             "-color_range", target_range)
    reference = encode(root / "reference.mp4", size="192x96", fps="5", frames=10, extra=extra)
    source = root / "full-range.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=black:size=160x96:rate=5,drawbox=x=80:y=0:w=80:h=96:color=white:t=fill",
        "-vf", "scale=in_range=tv:out_range=pc", "-frames:v", "5",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuvj420p", "-color_range", "pc", str(source),
    ], check=True, capture_output=True, timeout=30)
    ids = [insert_video(reference), insert_video(source)]
    request, report = conversion_request(ids, "192x96", fps=5)
    assert report["mode"] == "selective", report
    video = joiner.create_join(request)
    joiner.process_join(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    pixels = subprocess.run([
        "ffmpeg", "-v", "error", "-i", joiner.resolve_video_path(result["file_path"]),
        "-vf", "select=eq(n\\,10)", "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], check=True, capture_output=True, timeout=30).stdout
    assert len(pixels) == 192 * 96 * 3
    black = pixels[(48 * 192 + 32) * 3:(48 * 192 + 32) * 3 + 3]
    white = pixels[(48 * 192 + 144) * 3:(48 * 192 + 144) * 3 + 3]
    assert max(black) <= 8, f"Black became {tuple(black)} after {target_range} conversion"
    assert min(white) >= 247, f"White became {tuple(white)} after {target_range} conversion"

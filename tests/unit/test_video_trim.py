"""Disposable real-codec, exact-range, resource and lifecycle trim coverage."""
import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import sqlite3
import threading

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from backend.database import get_db
from backend.models import VideoTrimCreate, VideoTrimRequest
from backend.routers import videos
from backend.services import video_joiner as media
from backend.services import video_trimmer as trim
from test_video_join import encode, fetch, hashes, insert_video, request_for


@pytest.fixture(autouse=True)
def clear_trim_operations(isolated_db, videos_base):
    yield
    trim.shutdown_trims()
    media.shutdown_joins()
    with media._operations_lock:
        assert not any(op.process and op.process.poll() is None for op in media._operations.values())
        media._operations.clear()


@pytest.fixture
def clip(isolated_db, videos_base):
    path = encode(Path(videos_base) / "source.mp4", fps="5", frames=50)
    return insert_video(path), path


def request(video_id, *, start=1, end=0, name="Trimmed"):
    report = trim.analyze_trim(video_id, VideoTrimRequest())
    return VideoTrimCreate(
        name=name, trim_start_seconds=start, trim_end_seconds=end, revision=report["revision"],
    )


def finish(video_id, **kwargs):
    video = trim.create_trim(video_id, request(video_id, **kwargs))
    trim.process_trim(video["id"])
    result = fetch(video["id"])
    assert result and result["status"] == "completed", result
    return result, Path(media.resolve_video_path(result["file_path"]))


@pytest.mark.parametrize("value", [-1, 0.1, "1", True, None, float("inf")])
@pytest.mark.parametrize("field", ["trim_start_seconds", "trim_end_seconds"])
def test_strict_whole_second_validation(field, value):
    with pytest.raises(ValidationError):
        VideoTrimRequest(**{field: value})


@pytest.mark.parametrize("start,end", [(10, 0), (0, 10), (5, 5), (9, 1), (1000000000, 0)])
def test_minimum_one_second_is_enforced(clip, start, end):
    with pytest.raises(HTTPException) as error:
        trim.analyze_trim(clip[0], VideoTrimRequest(trim_start_seconds=start, trim_end_seconds=end))
    assert error.value.status_code == 422
    assert not media._operations


def test_empty_trim_and_invalid_names_are_rejected(clip):
    for args in [dict(start=0), dict(name=".../<>"), dict(name="   ")]:
        with pytest.raises(HTTPException) as error:
            trim.create_trim(clip[0], request(clip[0], **args))
        assert error.value.status_code == 422


@pytest.mark.parametrize("fps,extra_frames", [(1, 0), (30, 0), (30, 1)])
def test_exact_seconds_at_other_rates_preserve_fractional_remainder(isolated_db, videos_base, fps, extra_frames):
    source = encode(Path(videos_base) / "rate.mp4", fps=str(fps), frames=fps * 10 + extra_frames)
    result, output = finish(insert_video(source), start=9)
    assert result["total_frames"] == len(hashes(output)) == fps + extra_frames
    assert result["duration_seconds"] == pytest.approx((fps + extra_frames) / fps)


def test_non_h264_input_does_not_inherit_an_invalid_avc_level(isolated_db, videos_base):
    source = encode(Path(videos_base) / "mpeg4.mp4", fps="5", frames=50,
                    extra=("-c:v", "mpeg4", "-q:v", "2"))
    result, output = finish(insert_video(source), start=2, end=3)
    info = media.probe_video(str(output))
    assert result["quality"] == "high"
    assert result["duration_seconds"] == 5 and info.frames == 25
    assert info.stream["codec_name"] == "h264"


@pytest.mark.parametrize("start,end", [(9, 0), (0, 9), (4, 5)])
def test_ten_seconds_can_leave_exactly_one_second(clip, start, end):
    original = clip[1].read_bytes()
    result, output = finish(clip[0], start=start, end=end)
    info = media.probe_video(str(output))
    assert result["total_frames"] == info.frames == 5
    assert result["duration_seconds"] == float(info.duration) == 1
    assert result["framerate"] == 5
    assert result["build_source"] == "trimmed"
    assert result["start_time"] is None and result["start_capture_id"] is None
    assert Path(media.resolve_video_path(result["thumbnail_path"])).is_file()
    assert clip[1].read_bytes() == original
    # Independently compare selected decoded pixels against adjacent frames.
    def raw(path):
        return subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(path),
            "-pix_fmt", "yuv420p", "-f", "rawvideo", "-",
        ], check=True, capture_output=True, timeout=30).stdout
    size = 160 * 96 * 3 // 2
    expected = raw(clip[1])[start * 5 * size:(start * 5 + 5) * size]
    actual = raw(output)
    assert len(actual) == len(expected) == 5 * size
    assert sum(abs(a - b) for a, b in zip(expected, actual)) / len(actual) < 3


def test_safe_copy_has_identical_frames_and_no_encoding(isolated_db, videos_base, monkeypatch):
    path = encode(Path(videos_base) / "keys.mp4", fps="5", frames=50, extra=("-g", "5", "-sc_threshold", "0"))
    video_id = insert_video(path)
    monkeypatch.setattr(trim, "_encode_args", lambda *args: pytest.fail("A safe copy must not encode"))
    result, output = finish(video_id, start=2, end=3)
    assert result["quality"] == "preserved"
    assert hashes(output) == hashes(path)[10:35]
    assert result["duration_seconds"] == 5
    assert not list(output.parent.glob(".trim-work"))
    assert media.analyze_join([result["id"], result["id"]])[0]["compatible"]


@pytest.mark.parametrize("fps,native", [(25, False), (30, True)])
def test_smart_trim_preserves_every_copied_frame(isolated_db, videos_base, fps, native):
    extra = ["-g", "250", "-sc_threshold", "0"]
    if native:
        extra += ["-vf", "setsar=9/10", "-pix_fmt", "yuvj420p", "-color_range", "pc",
                  "-colorspace", "bt470bg", "-x264-params", "chromaloc=1"]
    path = encode(Path(videos_base) / "long.mp4", fps=str(fps), frames=fps * 40, extra=extra)
    source_id = insert_video(path)
    creation = request(source_id, start=3, end=7)
    video = trim.create_trim(source_id, creation)
    operation = trim._operation(video["id"])
    plan = operation.trim
    assert plan.mode == "smart" and plan.copied_frames >= fps * 20
    original_bytes = hashlib.sha256(path.read_bytes()).digest()
    original = hashes(path)
    trim.process_trim(video["id"])
    result = fetch(video["id"])
    assert result["status"] == "completed", result
    assert result["quality"] == "partly_converted", "The controlled fixture must exercise actual smart copying, not fallback"
    output = Path(media.resolve_video_path(result["file_path"]))
    output_hashes = hashes(output)
    offset = plan.copy_start - plan.start
    assert output_hashes[offset:offset + plan.copied_frames] == original[plan.copy_start:plan.copy_end]
    assert len(output_hashes) == result["total_frames"] == fps * 30
    assert result["duration_seconds"] == 30
    info = media.probe_video(str(output))
    source_info = media.probe_video(str(path))
    for key in ["sample_aspect_ratio", "color_range", "color_space", "chroma_location"]:
        assert info.stream.get(key) == source_info.stream.get(key)
    assert hashlib.sha256(path.read_bytes()).digest() == original_bytes


def test_incompatible_boundary_configuration_uses_high_quality_fallback(clip, monkeypatch):
    original_inspect = trim._inspect
    def inspect(*args):
        plan, row = original_inspect(*args)
        # Force an attempted smart segment that begins between independent keys.
        return trim.TrimPlan(plan.source, plan.start, plan.end, plan.start, plan.end), row
    monkeypatch.setattr(trim, "_inspect", inspect)
    result, output = finish(clip[0], start=2, end=2)
    assert result["quality"] == "high"
    assert result["duration_seconds"] == 6
    assert len(hashes(output)) == 30
    assert not list(output.parent.glob(".trim-work"))


def test_source_and_output_mutations_are_blocked_until_cancelled(clip):
    video = trim.create_trim(clip[0], request(clip[0]))
    for action in [
        lambda: videos.rename_video(clip[0], videos.VideoRenameRequest(name="Changed")),
        lambda: videos.delete_video(clip[0]),
        lambda: videos.delete_multiple_videos(videos.BulkDeleteRequest(video_ids=[clip[0]])),
        lambda: videos.delete_video(video["id"]),
        lambda: media.create_join(request_for([clip[0], clip[0]])),
    ]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    assert not media.cancel_join(video["id"])
    assert videos.cancel_video_build(video["id"]) == {"status": "cancelled"}
    trim.process_trim(video["id"])
    assert fetch(video["id"]) is None
    assert clip[1].is_file()


def test_join_blocks_trim_and_trim_blocks_another_trim(clip):
    joined = media.create_join(request_for([clip[0], clip[0]]))
    with pytest.raises(HTTPException) as error:
        trim.create_trim(clip[0], request(clip[0]))
    assert error.value.status_code == 409
    assert media.cancel_join(joined["id"])
    video = trim.create_trim(clip[0], request(clip[0]))
    with pytest.raises(HTTPException) as error:
        trim.create_trim(clip[0], request(clip[0]))
    assert error.value.status_code == 409
    assert trim.cancel_trim(video["id"])


def test_stale_inspection_and_source_changes_are_rejected(clip):
    creation = request(clip[0])
    with get_db() as conn:
        conn.execute("UPDATE processed_videos SET name='Renamed' WHERE id=?", (clip[0],))
    with pytest.raises(HTTPException) as error:
        trim.create_trim(clip[0], creation)
    assert error.value.status_code == 409


def test_missing_incomplete_unsupported_and_outside_sources(clip, tmp_path):
    for video_id in [999999, insert_video(clip[1], status="processing")]:
        with pytest.raises(HTTPException):
            trim.analyze_trim(video_id, VideoTrimRequest())
    outside = encode(tmp_path / "outside.mp4", fps="5", frames=10)
    with pytest.raises(HTTPException) as error:
        trim.analyze_trim(insert_video(outside), VideoTrimRequest())
    assert error.value.status_code == 422
    broken = Path(trim.get_timelapses_path()) / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(HTTPException) as error:
        trim.analyze_trim(insert_video(broken), VideoTrimRequest())
    assert error.value.status_code == 422
    with get_db() as conn:
        conn.execute("UPDATE processed_videos SET file_path='../outside.mp4' WHERE id=?", (clip[0],))
    with pytest.raises(HTTPException):
        trim.analyze_trim(clip[0], VideoTrimRequest())


def test_disk_rejection_before_admission(clip, monkeypatch):
    creation = request(clip[0])
    monkeypatch.setattr(trim.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 1})())
    with pytest.raises(HTTPException) as error:
        trim.create_trim(clip[0], creation)
    assert error.value.status_code == 507
    assert not media._operations


@pytest.mark.parametrize("failure", [media.MediaStorageError, media.MediaStalledError, media.MediaCommandError])
def test_running_resource_failures_are_not_retried_as_encoding(clip, monkeypatch, failure):
    video = trim.create_trim(clip[0], request(clip[0]))
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise failure("Processing stopped")
    monkeypatch.setattr(trim, "_render", fail)
    trim.process_trim(video["id"])
    assert len(calls) == 1
    assert fetch(video["id"])["status"] == "failed"
    assert not media._operations


def test_fast_final_write_cannot_skip_its_storage_bound(clip):
    video = trim.create_trim(clip[0], request(clip[0]))
    operation = trim._operation(video["id"])
    output = Path(trim.get_timelapses_path()) / "too-big.bin"
    operation.write_limit = 1
    with pytest.raises(media.MediaStorageError):
        media._media([
            "-f", "lavfi", "-i", "color=size=16x16", "-frames:v", "1",
            "-f", "rawvideo", str(output),
        ], operation, video["id"], 1, 0, 1)
    assert operation.process is None


def test_thread_start_failure_does_not_leave_a_processing_row(clip, monkeypatch):
    video = trim.create_trim(clip[0], request(clip[0]))
    def fail(_):
        raise RuntimeError("Cannot create worker")
    monkeypatch.setattr(trim.threading.Thread, "start", fail)
    with pytest.raises(HTTPException) as error:
        trim.start_trim(video["id"])
    assert error.value.status_code == 503
    assert fetch(video["id"]) is None
    assert not media._operations


def test_two_concurrent_trim_admissions_are_serialized(clip, monkeypatch):
    creation = request(clip[0])
    analysis = trim._inspect(clip[0], creation)
    ready = threading.Barrier(2)
    def inspect(*args):
        ready.wait(timeout=5)
        return analysis
    monkeypatch.setattr(trim, "_inspect", inspect)
    results = []
    def create():
        try:
            results.append(trim.create_trim(clip[0], creation))
        except HTTPException as error:
            results.append(error.status_code)
    workers = [threading.Thread(target=create) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert results.count(409) == 1
    assert len(media._operations) == 1


def test_rollback_cannot_release_another_trim_that_reuses_its_id(clip, monkeypatch):
    creation = request(clip[0])
    replacement = []
    rolled_back = []
    @contextmanager
    def fail_commit():
        try:
            with get_db() as conn:
                yield conn
                if conn.in_transaction:
                    rolled_back.append(conn.execute(
                        "SELECT id FROM processed_videos WHERE build_source='trimmed' AND status='processing'"
                    ).fetchone()["id"])
                    raise sqlite3.OperationalError("Deliberate commit failure")
        except sqlite3.OperationalError:
            monkeypatch.setattr(trim, "get_db", get_db)
            replacement.append(trim.create_trim(clip[0], creation))
            raise
    monkeypatch.setattr(trim, "get_db", fail_commit)
    with pytest.raises(sqlite3.OperationalError, match="Deliberate commit failure"):
        trim.create_trim(clip[0], creation)
    assert replacement[0]["id"] == rolled_back[0]
    assert replacement[0]["id"] in media._operations
    assert trim.cancel_trim(replacement[0]["id"])


def test_restart_removes_partial_output_without_touching_original(clip):
    before = clip[1].read_bytes()
    video = trim.create_trim(clip[0], request(clip[0]))
    output, partial, thumbnail, working = trim._paths(video, trim.get_timelapses_path())
    os.makedirs(working)
    for path in [output, partial, thumbnail, os.path.join(working, "middle.mp4")]:
        Path(path).write_bytes(b"partial")
    with media._operations_lock:
        media._operations.clear()
    trim.recover_trims()
    assert fetch(video["id"])["status"] == "failed"
    assert "restart" in fetch(video["id"])["error_message"]
    assert not os.path.exists(os.path.dirname(output))
    assert clip[1].read_bytes() == before


def test_worker_survives_request_and_shutdown_waits_for_cancellation(clip, monkeypatch):
    entered = threading.Event()
    def render(plan, operation, *args, **kwargs):
        entered.set()
        assert operation.cancelled.wait(timeout=10)
        media._check_cancel(operation)
    monkeypatch.setattr(trim, "_render", render)
    video = trim.create_trim(clip[0], request(clip[0]))
    trim.start_trim(video["id"])
    operation = trim._operation(video["id"])
    assert entered.wait(timeout=5)
    assert operation.worker.is_alive()
    trim.shutdown_trims()
    operation.worker.join(timeout=5)
    assert not operation.worker.is_alive()
    assert fetch(video["id"]) is None
    assert not media._operations
    assert clip[1].is_file()


def test_completed_trim_remains_independent_and_can_be_trimmed_again(clip):
    with get_db() as conn:
        schema_before = list(conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name"))
    first, first_path = finish(clip[0], start=1)
    second, second_path = finish(first["id"], start=1)
    videos.delete_video(clip[0])
    videos.delete_video(first["id"])
    assert not first_path.exists() and second_path.is_file()
    assert asyncio.run(videos.get_video(second["id"]))["duration_seconds"] == 8
    with get_db() as conn:
        assert list(conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name")) == schema_before

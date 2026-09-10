"""
Regression test for: no watchdog on hung ffmpeg video builds.

Before this fix, the progress-monitoring loop in process_video() called
process.stderr.readline() with no timeout at all. A hung ffmpeg process
(e.g. deadlocked on a corrupted frame, producing no more output) would
block the worker thread running process_video() forever -- and since video
builds run via FastAPI's BackgroundTasks (which offloads sync callables to
a bounded thread pool), enough hung builds could eventually exhaust that
pool.

The fix uses a selector with a timeout (VIDEO_BUILD_STALL_TIMEOUT, default
120s) around the stderr read loop: if ffmpeg produces absolutely no output
for that long, it's considered stalled, the process is killed, and the
video is marked 'failed' with a clear message -- instead of hanging
indefinitely.

This test drives the real selector/kill logic in process_video() against a
*real* OS subprocess (not a mock) that never writes anything to stderr, so
it's an authentic proof of the watchdog rather than a purely mocked one.
The stall timeout is monkeypatched down to a tiny value so the test
completes in well under a second instead of waiting the real 120s default.
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, "/app")

from backend import config, database  # noqa: E402
from backend.services import video_processor  # noqa: E402

from conftest import insert_job  # noqa: E402


def _insert_processed_video_row(conn, job_id, video_id_hint_name="stall_test_video"):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO processed_videos (
            job_id, job_name, name, file_path, file_size, resolution,
            framerate, quality, total_frames, duration_seconds, status,
            created_at
        ) VALUES (?, 'Test Job', ?, 'placeholder.mp4', 0, '1920x1080', 30,
                  'medium', 1, 1.0, 'processing', '2026-01-01T00:00:00+00:00')
    """, (job_id, video_id_hint_name))
    conn.commit()
    return cursor.lastrowid


def _insert_capture(conn, job_id, file_path="frame_0001.jpg"):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO captures (job_id, file_path, file_size, captured_at)
        VALUES (?, ?, 100, '2026-01-01T00:00:00+00:00')
    """, (job_id, file_path))
    conn.commit()


@pytest.fixture
def video_setup(isolated_db, captures_base, videos_base):
    with database.get_db() as conn:
        job_id = insert_job(conn)
        _insert_capture(conn, job_id)
        video_id = _insert_processed_video_row(conn, job_id)
    return job_id, video_id


def test_process_video_kills_and_fails_on_stall(monkeypatch, video_setup, caplog):
    job_id, video_id = video_setup
    monkeypatch.setattr(config, "VIDEO_BUILD_STALL_TIMEOUT", 0.15)

    # Capture the *real* Popen before patching -- video_processor.subprocess
    # is the actual stdlib subprocess module object (not a copy), so
    # patching its Popen attribute would also affect any later reference
    # via `subprocess.Popen` / `real_subprocess.Popen`, causing infinite
    # self-recursion if fake_popen called through the module instead of
    # this captured original.
    original_popen = video_processor.subprocess.Popen

    # Replace the ffmpeg Popen call with a real subprocess that never writes
    # anything to stderr and sleeps far longer than the (patched) stall
    # timeout -- simulating a genuinely hung ffmpeg process.
    def fake_popen(cmd, **kwargs):
        return original_popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            universal_newlines=kwargs.get("universal_newlines"),
        )

    monkeypatch.setattr(video_processor.subprocess, "Popen", fake_popen)

    job_dict = {"id": job_id, "name": "Test Job"}
    start = time.monotonic()
    with caplog.at_level("ERROR"):
        video_processor.process_video(
            video_id=video_id,
            job_dict=job_dict,
            resolution="1920x1080",
            framerate=30,
            quality="medium",
            start_capture_id=None,
            end_capture_id=None,
            start_time=None,
            end_time=None,
            output_path=str(Path(config.DEFAULT_VIDEOS_PATH) / "out.mp4"),
        )
    elapsed = time.monotonic() - start

    # Must return promptly (stall timeout + small overhead), never wait
    # anywhere near the real subprocess's 5s sleep.
    assert elapsed < 3.0, f"process_video() took {elapsed:.2f}s -- watchdog did not kill the stalled process promptly"
    assert "stalled" in caplog.text.lower()

    with database.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, error_message FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()

    assert row["status"] == "failed"
    assert "stalled" in (row["error_message"] or "").lower()
    assert video_id not in video_processor._active_processes


def test_process_video_completes_normally_when_ffmpeg_produces_steady_output(monkeypatch, video_setup):
    """Control case: a process that *does* produce output frequently must
    not be killed by the watchdog."""
    job_id, video_id = video_setup
    monkeypatch.setattr(config, "VIDEO_BUILD_STALL_TIMEOUT", 5)

    output_path = str(Path(config.DEFAULT_VIDEOS_PATH) / "out.mp4")

    original_popen = video_processor.subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Writes a frame= progress line quickly, then exits 0, and also
        # creates the expected output file so the completion branch is hit.
        script = (
            "import sys, time\n"
            f"open({output_path!r}, 'wb').write(b'x')\n"
            "sys.stderr.write('frame=1 fps=1 q=1 size=1kB time=00:00:01 bitrate=1kbit/s\\n')\n"
            "sys.stderr.flush()\n"
        )
        return original_popen(
            [sys.executable, "-c", script],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            universal_newlines=kwargs.get("universal_newlines"),
        )

    monkeypatch.setattr(video_processor.subprocess, "Popen", fake_popen)

    job_dict = {"id": job_id, "name": "Test Job"}
    video_processor.process_video(
        video_id=video_id,
        job_dict=job_dict,
        resolution="1920x1080",
        framerate=30,
        quality="medium",
        start_capture_id=None,
        end_capture_id=None,
        start_time=None,
        end_time=None,
        output_path=output_path,
    )

    with database.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()

    assert row["status"] == "completed"

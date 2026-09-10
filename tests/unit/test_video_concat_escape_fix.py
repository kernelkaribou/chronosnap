"""
Unit tests for Fix #3: ffmpeg concat demuxer file-list quote escaping.

video_processor.py builds a `file '<path>'` concat-list entry per capture.
Without escaping, a path containing a literal single quote (e.g. a job
named "Bob's Garden") truncates at the embedded quote and ffmpeg fails to
find the file — a real correctness bug, not just a theoretical injection.
"""
import os
import subprocess
import shutil

import pytest

from backend.services.video_processor import _ffmpeg_concat_escape


def test_job_name_with_apostrophe_survives_image_capture_filename_sanitization():
    """Confirms this bug is reachable through completely ordinary usage:
    image_capture.py's own filename sanitizer strips <>:"/\\|?* and control
    chars, but NOT a literal single quote — so "Bob's Garden" as a job name
    produces a capture filename that still contains an apostrophe."""
    import re
    filename = "Bob's Garden_000001_20260101_120000"
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)  # mirrors image_capture.py
    assert "'" in sanitized


# ---------------------------------------------------------------------------
# Pure escaping-function tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("/captures/1_myjob/img.jpg", "/captures/1_myjob/img.jpg"),
    ("/captures/1_Bob's Garden/img.jpg", "/captures/1_Bob'\\''s Garden/img.jpg"),
    ("it's a 'quoted' name", "it'\\''s a '\\''quoted'\\'' name"),
    ("", ""),
])
def test_ffmpeg_concat_escape(raw, expected):
    assert _ffmpeg_concat_escape(raw) == expected


def test_ffmpeg_concat_escape_output_round_trips_through_single_quote_wrapping():
    # The escaped value, when wrapped in outer single quotes as
    # video_processor.py does (f"file '{escaped}'"), must reconstruct to
    # exactly the original path if you apply POSIX single-quote parsing
    # rules: '...'\''...' segments concatenate back to the literal string.
    raw = "path/with/a/'/quote.jpg"
    escaped = _ffmpeg_concat_escape(raw)
    wrapped = f"'{escaped}'"
    # Reconstruct using the same rule ffmpeg/shell single-quoting uses:
    # a run of `'\''` closes-escapes-reopens; strip the outer quotes and
    # collapse `'\''` back to a literal quote.
    reconstructed = wrapped[1:-1].replace("'\\''", "'")
    assert reconstructed == raw


# ---------------------------------------------------------------------------
# Empirical test against the real ffmpeg binary (present in this image)
# ---------------------------------------------------------------------------

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed in this environment")
def test_ffmpeg_actually_builds_video_from_quoted_job_name_path(tmp_path):
    """End-to-end proof this is reachable via ordinary usage, not just a
    theoretical concern: while Fix #1 sanitizes job *directory* names (and
    strips apostrophes), image_capture.py's own filename sanitizer
    (`re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)`) does NOT strip a
    literal single quote — so a job named "Bob's Garden" still produces
    capture filenames containing a literal `'`. This test uses a raw path
    containing a quote to prove the concat-escaping fix independently
    handles that case."""
    quote_dir = tmp_path / "dir with ' quote"
    quote_dir.mkdir()

    frame_paths = []
    for i in range(2):
        frame = quote_dir / f"frame_{i}.png"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi",
             "-i", f"color=c={'red' if i == 0 else 'blue'}:s=32x32:d=1",
             "-frames:v", "1", "-y", str(frame)],
            check=True, capture_output=True,
        )
        frame_paths.append(str(frame))

    list_file = tmp_path / "list.txt"
    with open(list_file, "w") as f:
        for path in frame_paths:
            f.write(f"file '{_ffmpeg_concat_escape(path)}'\n")
            f.write("duration 1\n")

    output = tmp_path / "out.mp4"
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-r", "1", "-y", str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"
    assert output.exists() and output.stat().st_size > 0


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed in this environment")
def test_unescaped_quote_path_actually_breaks_ffmpeg_without_the_fix(tmp_path):
    """Negative control proving this is a real, not theoretical, bug: the
    OLD unescaped code path genuinely fails against real ffmpeg."""
    quote_dir = tmp_path / "dir with ' quote"
    quote_dir.mkdir()
    frame = quote_dir / "frame.png"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1",
         "-frames:v", "1", "-y", str(frame)],
        check=True, capture_output=True,
    )

    list_file = tmp_path / "list_naive.txt"
    with open(list_file, "w") as f:
        f.write(f"file '{frame}'\n")  # deliberately unescaped, mirrors pre-fix code
        f.write("duration 1\n")

    output = tmp_path / "out_naive.mp4"
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-r", "1", "-y", str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0

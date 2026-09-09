"""
Unit tests for Fix #1: path traversal via unsanitized job names.

Covers:
  - sanitize_directory_name(): strips traversal/separator/quote characters
  - validate_path_within(): boundary enforcement helper
  - cleanup_empty_parents(): real path-boundary check (not string prefix)
  - create_job(): end-to-end — a malicious job name must never place the
    job directory outside the captures root
  - delete_job(): end-to-end — deleting a job must never rmtree a directory
    outside the captures root, even if an unsafe capture_path exists in the DB
"""
import os
import asyncio
import pytest

from backend.helpers.file_helpers import (
    sanitize_directory_name, validate_path_within, cleanup_empty_parents,
)
from conftest import insert_job


# ---------------------------------------------------------------------------
# sanitize_directory_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("My Job", "My Job"),
    ("../../../etc_pwn", "etc_pwn"),  # dots/slashes stripped; '_' is a word char so it survives
    ("Bob's Garden", "Bobs Garden"),
    ("weird/../name", "weirdname"),
    ("name\x00withnull", "namewithnull"),
    ("   leading and trailing   ", "leading and trailing"),
    ("", ""),
    ("////", ""),
])
def test_sanitize_directory_name_strips_unsafe_chars(raw, expected):
    assert sanitize_directory_name(raw) == expected


def test_sanitize_directory_name_result_has_no_path_separators():
    # Property-based-ish check: for a battery of adversarial inputs, the
    # sanitized result must never contain a path separator or '..' sequence,
    # which is the actual security property we depend on.
    adversarial = [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32",
        "a/b/c",
        "....//....//etc",
        "/absolute/path",
    ]
    for raw in adversarial:
        sanitized = sanitize_directory_name(raw)
        assert os.sep not in sanitized
        assert "/" not in sanitized
        assert "\\" not in sanitized
        assert ".." not in sanitized


# ---------------------------------------------------------------------------
# validate_path_within
# ---------------------------------------------------------------------------

def test_validate_path_within_accepts_child_path(tmp_path):
    base = tmp_path / "captures"
    base.mkdir()
    child = base / "5_myjob"
    child.mkdir()
    result = validate_path_within(str(child), str(base))
    assert result == os.path.realpath(str(child))


def test_validate_path_within_accepts_base_itself(tmp_path):
    base = tmp_path / "captures"
    base.mkdir()
    result = validate_path_within(str(base), str(base))
    assert result == os.path.realpath(str(base))


def test_validate_path_within_rejects_sibling_path(tmp_path):
    base = tmp_path / "captures"
    base.mkdir()
    sibling = tmp_path / "captures-old"
    sibling.mkdir()
    with pytest.raises(ValueError):
        validate_path_within(str(sibling), str(base))


def test_validate_path_within_rejects_traversal_outside_base(tmp_path):
    base = tmp_path / "captures"
    base.mkdir()
    outside = tmp_path / "etc_pwn"
    outside.mkdir()
    traversal_path = str(base / ".." / "etc_pwn")
    with pytest.raises(ValueError):
        validate_path_within(traversal_path, str(base))


# ---------------------------------------------------------------------------
# cleanup_empty_parents — real path-boundary check
# ---------------------------------------------------------------------------

def test_cleanup_empty_parents_does_not_touch_string_prefix_sibling(tmp_path):
    base = tmp_path / "timelapses"
    base.mkdir()
    sibling = tmp_path / "timelapses-important"  # shares a string prefix with `base`
    sibling.mkdir()
    nested_empty = sibling / "should_not_be_removed"
    nested_empty.mkdir()

    fake_file_path = str(nested_empty / "gone.mp4")
    cleanup_empty_parents(fake_file_path, str(base))

    # The old `folder.startswith(base)` check would have removed this
    # directory because "timelapses-important" starts with "timelapses".
    assert nested_empty.exists()


def test_cleanup_empty_parents_removes_real_empty_parents(tmp_path):
    base = tmp_path / "timelapses"
    nested = base / "1_job" / "2_video"
    nested.mkdir(parents=True)

    fake_file_path = str(nested / "gone.mp4")
    cleanup_empty_parents(fake_file_path, str(base))

    assert not nested.exists()
    assert not (base / "1_job").exists()
    assert base.exists()  # base itself is never removed


# ---------------------------------------------------------------------------
# create_job — end-to-end traversal containment
# ---------------------------------------------------------------------------

def test_create_job_with_traversal_name_stays_within_captures_base(isolated_db, captures_base):
    from backend.models import JobCreate, StreamType
    from backend.routers.jobs import create_job

    job = JobCreate(
        name="../../../etc_pwn",
        url="http://example.invalid/stream.jpg",
        stream_type=StreamType.HTTP,
        start_datetime="2026-01-01T00:00:00+00:00",
        interval_seconds=60,
        framerate=1,
    )

    result = asyncio.run(create_job(job))

    # The directory actually created on disk must be a child of captures_base.
    created_dirs = [
        os.path.join(captures_base, d) for d in os.listdir(captures_base)
    ]
    assert len(created_dirs) == 1
    assert os.path.realpath(created_dirs[0]).startswith(os.path.realpath(captures_base) + os.sep)

    # Reproduce exactly what the pre-fix vulnerable code would have built
    # (f"{job_id}_{job.name}" with the raw, unsanitized name) and assert
    # that path was NOT created outside captures_base.
    job_id = result["id"]
    vulnerable_rel_dir = f"{job_id}_{job.name}"
    vulnerable_abs_dir = os.path.join(captures_base, vulnerable_rel_dir)
    assert not os.path.exists(os.path.realpath(vulnerable_abs_dir)), (
        "The pre-fix traversal path was created — sanitization regressed"
    )
    outside_target = os.path.join(os.path.dirname(os.path.realpath(captures_base)), "etc_pwn")
    assert not os.path.exists(outside_target)


def test_create_job_rejects_name_that_sanitizes_to_empty(isolated_db, captures_base):
    from fastapi import HTTPException
    from backend.models import JobCreate, StreamType

    job = JobCreate(
        name="////",
        url="http://example.invalid/stream.jpg",
        stream_type=StreamType.HTTP,
        start_datetime="2026-01-01T00:00:00+00:00",
        interval_seconds=60,
        framerate=1,
    )
    from backend.routers.jobs import create_job
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_job(job))
    assert exc_info.value.status_code == 400
    # No job row should remain, and no directory should have been created.
    assert os.listdir(captures_base) == []


# ---------------------------------------------------------------------------
# delete_job — defense-in-depth boundary check before shutil.rmtree
# ---------------------------------------------------------------------------

def test_delete_job_refuses_to_rmtree_outside_captures_base(isolated_db, captures_base, tmp_path):
    """Simulates a legacy/unsafe row already in the DB (e.g. from before this
    fix existed) whose capture_path escapes the captures root. Deleting the
    job must not remove the out-of-tree directory."""
    from backend.database import get_db
    from backend.routers.jobs import delete_job

    outside_dir = tmp_path / "etc_pwn"
    outside_dir.mkdir()
    (outside_dir / "sentinel.txt").write_text("do not delete me")

    # Craft a capture_path that resolves outside captures_base, mirroring
    # what the pre-fix code could have produced.
    unsafe_rel_path = os.path.relpath(str(outside_dir), captures_base)

    with get_db() as conn:
        job_id = insert_job(conn, name="legacy", capture_path=unsafe_rel_path)

    asyncio.run(delete_job(job_id))

    # The out-of-tree directory and its contents must survive.
    assert outside_dir.exists()
    assert (outside_dir / "sentinel.txt").exists()

    # The job row itself should still be deleted (delete_job's DB-side effect
    # is independent of the filesystem safety check).
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        assert cursor.fetchone() is None


def test_delete_job_removes_legit_folder_within_captures_base(isolated_db, captures_base):
    from backend.database import get_db
    from backend.routers.jobs import delete_job

    job_dir = os.path.join(captures_base, "1_legit")
    os.makedirs(job_dir)
    with open(os.path.join(job_dir, "capture.jpg"), "w") as f:
        f.write("fake")

    with get_db() as conn:
        job_id = insert_job(conn, name="legit", capture_path="1_legit")

    asyncio.run(delete_job(job_id))

    assert not os.path.exists(job_dir)

"""
Unit tests for Fix #2: arbitrary file registration via the maintenance
"import orphaned files" endpoint.

`import_orphaned_files()` must only ever register files that live within
the target job's own capture directory — never an arbitrary path supplied
by the client (e.g. the SQLite DB file, or another job's captures).
"""
import os
import pytest

from backend.services.maintenance import import_orphaned_files
from backend.database import get_db
from conftest import insert_job


def _make_job_dir(captures_base, job_id, name="myjob"):
    job_dir = os.path.join(captures_base, f"{job_id}_{name}")
    os.makedirs(job_dir)
    return job_dir


def test_import_orphaned_files_accepts_file_within_job_directory(isolated_db, captures_base):
    with get_db() as conn:
        job_id = insert_job(conn, capture_path=f"1_myjob")
    job_dir = _make_job_dir(captures_base, job_id)
    legit_file = os.path.join(job_dir, "capture_0001.jpg")
    with open(legit_file, "w") as f:
        f.write("fake image bytes")

    result = import_orphaned_files(job_id, [
        {"file_path": legit_file, "file_size": 16, "captured_at": "2026-01-01T00:00:00+00:00"},
    ])

    assert result["imported_count"] == 1
    assert result["skipped_count"] == 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM captures WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        assert row is not None


def test_import_orphaned_files_rejects_path_outside_job_directory(isolated_db, captures_base, tmp_path):
    """The core exploit: register an arbitrary file (e.g. the DB) as a capture."""
    with get_db() as conn:
        job_id = insert_job(conn, capture_path="1_myjob")
    _make_job_dir(captures_base, job_id)

    secret_file = tmp_path / "chronosnap.db"
    secret_file.write_text("pretend this is the sqlite database with the API key")

    result = import_orphaned_files(job_id, [
        {"file_path": str(secret_file), "file_size": 100, "captured_at": "2026-01-01T00:00:00+00:00"},
    ])

    assert result["imported_count"] == 0
    assert result["skipped_count"] == 1

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM captures WHERE job_id = ?", (job_id,))
        assert cursor.fetchone()[0] == 0


def test_import_orphaned_files_rejects_another_jobs_directory(isolated_db, captures_base):
    """A path that's legitimately within captures_base, but belongs to a
    *different* job, must still be rejected for this job's import call."""
    with get_db() as conn:
        job_id_a = insert_job(conn, name="job-a", capture_path="1_job-a")
        job_id_b = insert_job(conn, name="job-b", capture_path="2_job-b")
    _make_job_dir(captures_base, job_id_a, "job-a")
    other_job_dir = _make_job_dir(captures_base, job_id_b, "job-b")
    other_file = os.path.join(other_job_dir, "capture_0001.jpg")
    with open(other_file, "w") as f:
        f.write("belongs to job b")

    # Attempt to import job B's file into job A's import call
    result = import_orphaned_files(job_id_a, [
        {"file_path": other_file, "file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},
    ])

    assert result["imported_count"] == 0
    assert result["skipped_count"] == 1


def test_import_orphaned_files_rejects_traversal_sequence(isolated_db, captures_base):
    with get_db() as conn:
        job_id = insert_job(conn, capture_path="1_myjob")
    job_dir = _make_job_dir(captures_base, job_id)

    traversal_path = os.path.join(job_dir, "..", "..", "outside.jpg")

    result = import_orphaned_files(job_id, [
        {"file_path": traversal_path, "file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},
    ])

    assert result["imported_count"] == 0
    assert result["skipped_count"] == 1


@pytest.mark.parametrize("bad_file_info", [
    {"file_path": None, "file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},
    {"file_path": 12345, "file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},
    {"file_path": "", "file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},
    {"file_size": 10, "captured_at": "2026-01-01T00:00:00+00:00"},  # missing file_path entirely
    "not-even-a-dict",
])
def test_import_orphaned_files_rejects_malformed_entries(isolated_db, captures_base, bad_file_info):
    with get_db() as conn:
        job_id = insert_job(conn, capture_path="1_myjob")
    _make_job_dir(captures_base, job_id)

    result = import_orphaned_files(job_id, [bad_file_info])

    assert result["imported_count"] == 0
    assert result["skipped_count"] == 1


def test_import_orphaned_files_mixed_batch_only_imports_valid_entries(isolated_db, captures_base, tmp_path):
    with get_db() as conn:
        job_id = insert_job(conn, capture_path="1_myjob")
    job_dir = _make_job_dir(captures_base, job_id)
    legit_file = os.path.join(job_dir, "capture_0001.jpg")
    with open(legit_file, "w") as f:
        f.write("fake")

    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("nope")

    result = import_orphaned_files(job_id, [
        {"file_path": legit_file, "file_size": 4, "captured_at": "2026-01-01T00:00:00+00:00"},
        {"file_path": str(secret_file), "file_size": 4, "captured_at": "2026-01-01T00:00:00+00:00"},
    ])

    assert result["imported_count"] == 1
    assert result["skipped_count"] == 1

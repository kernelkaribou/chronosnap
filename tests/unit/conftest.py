"""
Shared pytest fixtures for backend unit tests.

These tests run *inside* the built application image (see tests/unit/run.sh) —
backend/config.py assumes /app exists (its DATA_DIR.mkdir() runs at import
time), so they are not designed to run against a bare host Python.

Each test gets an isolated, throwaway SQLite DB and captures/timelapses
directory via monkeypatched config values — nothing here touches the real
/app/data, /captures, or /timelapses paths.
"""
import sys
import os
import pytest

sys.path.insert(0, "/app")

from backend import config, database  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point config.DATABASE_PATH at a fresh temp SQLite file and initialize schema."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path))
    database.init_db()
    return str(db_path)


@pytest.fixture
def captures_base(tmp_path, monkeypatch):
    """Point config.DEFAULT_CAPTURES_PATH at a fresh temp directory."""
    base = tmp_path / "captures"
    base.mkdir()
    monkeypatch.setattr(config, "DEFAULT_CAPTURES_PATH", str(base))
    return str(base)


@pytest.fixture
def videos_base(tmp_path, monkeypatch):
    """Point config.DEFAULT_VIDEOS_PATH at a fresh temp directory."""
    base = tmp_path / "timelapses"
    base.mkdir()
    monkeypatch.setattr(config, "DEFAULT_VIDEOS_PATH", str(base))
    return str(base)


def insert_job(conn, *, name="Test Job", capture_path="1_Test Job", job_id=None):
    """Insert a minimal job row directly, bypassing the API layer, for
    service-level unit tests. Returns the inserted job id."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs (
            id, name, url, stream_type, start_datetime, interval_seconds,
            framerate, capture_path, naming_pattern, created_at, updated_at
        ) VALUES (?, ?, 'http://example.invalid/stream.jpg', 'http', '2026-01-01T00:00:00+00:00',
                  60, 1, ?, '{job_name}_{count}_{timestamp}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
    """, (job_id, name, capture_path))
    conn.commit()
    return cursor.lastrowid

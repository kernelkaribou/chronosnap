"""Reusable database helper functions to reduce boilerplate across routers."""

import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def get_or_404(cursor, query: str, params: tuple, detail: str = "Resource not found") -> dict:
    """Execute a SELECT query and return the row as a dict, or raise 404."""
    cursor.execute(query, params)
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=detail)
    return dict(row)


def fetch_one(cursor, query: str, params: tuple) -> dict | None:
    """Execute a SELECT query and return the row as a dict, or None."""
    cursor.execute(query, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def ensure_column(cursor, table: str, column: str, definition: str):
    """Add a column to a table if it doesn't already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [col[1] for col in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info(f"Migration: added {table}.{column}")


def decrement_job_stats(cursor, job_id: int, file_size: int, now_iso: str):
    """Decrement a job's capture_count and storage_size after a capture deletion."""
    cursor.execute("""
        UPDATE jobs
        SET capture_count = CASE 
                WHEN capture_count > 0 THEN capture_count - 1 
                ELSE 0 
            END,
            storage_size = CASE 
                WHEN storage_size >= ? THEN storage_size - ? 
                ELSE 0 
            END,
            updated_at = ?
        WHERE id = ?
    """, (file_size, file_size, now_iso, job_id))


def enrich_capture(capture_dict: dict, has_thumbnail_fn, get_thumbnail_path_fn) -> dict:
    """Add thumbnail and favorite fields to a capture dict."""
    capture_dict['has_thumbnail'] = has_thumbnail_fn(capture_dict['file_path'])
    capture_dict['thumbnail_path'] = (
        get_thumbnail_path_fn(capture_dict['file_path']) 
        if capture_dict['has_thumbnail'] else None
    )
    capture_dict['is_favorite'] = bool(capture_dict.get('is_favorite', 0))
    return capture_dict


def normalize_favorite(record_dict: dict) -> dict:
    """Convert SQLite integer is_favorite to Python bool."""
    record_dict['is_favorite'] = bool(record_dict.get('is_favorite', 0))
    return record_dict

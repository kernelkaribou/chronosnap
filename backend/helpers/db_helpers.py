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
    from .file_helpers import resolve_capture_path
    resolved_fp = resolve_capture_path(capture_dict['file_path'])
    capture_dict['has_thumbnail'] = has_thumbnail_fn(resolved_fp)
    capture_dict['thumbnail_path'] = (
        get_thumbnail_path_fn(resolved_fp) 
        if capture_dict['has_thumbnail'] else None
    )
    capture_dict['is_favorite'] = bool(capture_dict.get('is_favorite', 0))
    return capture_dict


def normalize_favorite(record_dict: dict) -> dict:
    """Convert SQLite integer is_favorite to Python bool."""
    record_dict['is_favorite'] = bool(record_dict.get('is_favorite', 0))
    return record_dict


def fetch_tags_for_jobs(cursor, job_ids: list) -> dict:
    """Fetch tags for a list of job IDs. Returns {job_id: [tag_dicts]}."""
    if not job_ids:
        return {}
    placeholders = ','.join('?' for _ in job_ids)
    cursor.execute(f"""
        SELECT jt.job_id, t.id, t.name, t.color
        FROM job_tags jt
        JOIN tags t ON t.id = jt.tag_id
        WHERE jt.job_id IN ({placeholders})
        ORDER BY t.name
    """, job_ids)
    result = {jid: [] for jid in job_ids}
    for row in cursor.fetchall():
        result[row['job_id']].append({'id': row['id'], 'name': row['name'], 'color': row['color']})
    return result


def fetch_tags_for_videos(cursor, video_ids: list) -> dict:
    """Fetch tags for a list of video IDs. Returns {video_id: [tag_dicts]}."""
    if not video_ids:
        return {}
    placeholders = ','.join('?' for _ in video_ids)
    cursor.execute(f"""
        SELECT vt.video_id, t.id, t.name, t.color
        FROM video_tags vt
        JOIN tags t ON t.id = vt.tag_id
        WHERE vt.video_id IN ({placeholders})
        ORDER BY t.name
    """, video_ids)
    result = {vid: [] for vid in video_ids}
    for row in cursor.fetchall():
        result[row['video_id']].append({'id': row['id'], 'name': row['name'], 'color': row['color']})
    return result


def set_job_tags(cursor, job_id: int, tag_ids: list):
    """Replace all tags for a job."""
    cursor.execute("DELETE FROM job_tags WHERE job_id = ?", (job_id,))
    for tag_id in tag_ids:
        cursor.execute("INSERT OR IGNORE INTO job_tags (job_id, tag_id) VALUES (?, ?)", (job_id, tag_id))


def set_video_tags(cursor, video_id: int, tag_ids: list):
    """Replace all tags for a video."""
    cursor.execute("DELETE FROM video_tags WHERE video_id = ?", (video_id,))
    for tag_id in tag_ids:
        cursor.execute("INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)", (video_id, tag_id))

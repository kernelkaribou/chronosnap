"""
Storage statistics API endpoints
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import os
import shutil
import logging

from ..database import get_db, dict_from_row
from .. import config

router = APIRouter()
logger = logging.getLogger(__name__)


class JobStorageInfo(BaseModel):
    job_id: int
    job_name: str
    capture_count: int
    capture_size: int
    video_count: int
    video_size: int
    total_size: int


class StorageStats(BaseModel):
    captures_total_size: int
    captures_total_count: int
    videos_total_size: int
    videos_total_count: int
    disk_total: int
    disk_used: int
    disk_free: int
    jobs: List[JobStorageInfo]


def _safe_disk_usage(path: str) -> dict:
    """Get disk usage for a path, returning zeros if unavailable."""
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except (OSError, FileNotFoundError):
        return {"total": 0, "used": 0, "free": 0}


@router.get("/stats", response_model=StorageStats)
async def get_storage_stats():
    """Get storage statistics across all jobs."""
    with get_db() as db:
        # Per-job capture stats
        capture_stats = db.execute("""
            SELECT j.id as job_id, j.name as job_name,
                   COUNT(c.id) as capture_count,
                   COALESCE(SUM(c.file_size), 0) as capture_size
            FROM jobs j
            LEFT JOIN captures c ON c.job_id = j.id
            GROUP BY j.id, j.name
        """).fetchall()

        # Per-job video stats
        video_stats = db.execute("""
            SELECT j.id as job_id,
                   COUNT(v.id) as video_count,
                   COALESCE(SUM(v.file_size), 0) as video_size
            FROM jobs j
            LEFT JOIN processed_videos v ON v.job_id = j.id AND v.status = 'completed'
            GROUP BY j.id
        """).fetchall()

    video_map = {row['job_id']: dict_from_row(row) for row in video_stats}

    jobs = []
    captures_total_size = 0
    captures_total_count = 0
    videos_total_size = 0
    videos_total_count = 0

    for row in capture_stats:
        cap = dict_from_row(row)
        vid = video_map.get(cap['job_id'], {'video_count': 0, 'video_size': 0})
        job_info = JobStorageInfo(
            job_id=cap['job_id'],
            job_name=cap['job_name'],
            capture_count=cap['capture_count'],
            capture_size=cap['capture_size'],
            video_count=vid['video_count'],
            video_size=vid['video_size'],
            total_size=cap['capture_size'] + vid['video_size']
        )
        jobs.append(job_info)
        captures_total_size += cap['capture_size']
        captures_total_count += cap['capture_count']
        videos_total_size += vid['video_size']
        videos_total_count += vid['video_count']

    # Sort by total size descending
    jobs.sort(key=lambda j: j.total_size, reverse=True)

    # Disk usage from captures path (primary storage volume)
    disk = _safe_disk_usage(config.DEFAULT_CAPTURES_PATH)

    return StorageStats(
        captures_total_size=captures_total_size,
        captures_total_count=captures_total_count,
        videos_total_size=videos_total_size,
        videos_total_count=videos_total_count,
        disk_total=disk['total'],
        disk_used=disk['used'],
        disk_free=disk['free'],
        jobs=jobs
    )

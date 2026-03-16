"""
Auto-build service — automatically generates timelapse videos on a schedule
"""
import os
import re
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from ..database import get_db, dict_from_row
from ..utils import get_now, to_iso, parse_iso
from .. import config
from .video_processor import process_video
from .webhook import send_webhook_event

logger = logging.getLogger(__name__)


def check_auto_builds():
    """Check all jobs for pending auto-builds and trigger if due."""
    now = get_now()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM jobs
            WHERE auto_build_enabled = 1
              AND auto_build_in_progress = 0
              AND status IN ('active', 'sleeping', 'completed')
              AND capture_count > 0
        """)
        jobs = [dict_from_row(row) for row in cursor.fetchall()]

    for job in jobs:
        if _is_auto_build_due(job, now):
            thread = threading.Thread(
                target=_run_auto_build,
                args=(job, now),
                daemon=True
            )
            thread.start()


def _is_auto_build_due(job: dict, now: datetime) -> bool:
    """Determine if a job's auto-build is due."""
    last_build = job.get('last_auto_build_at')
    if not last_build:
        return False

    last_build_dt = parse_iso(last_build)
    interval = timedelta(days=job.get('auto_build_interval_days', 7))
    return now >= last_build_dt + interval


def _run_auto_build(job: dict, now: datetime):
    """Execute an auto-build for a job. Runs in a daemon thread."""
    job_id = job['id']
    job_name = job['name']

    # Set in_progress flag
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE jobs SET auto_build_in_progress = 1 WHERE id = ?",
            (job_id,)
        )

    try:
        logger.info(f"Auto-build starting for job {job_id} ({job_name})")

        # Determine capture range: since last auto-build or all captures
        last_build = job.get('last_auto_build_at')
        start_time = last_build if last_build else None

        # Check if there are captures in this range
        with get_db() as conn:
            cursor = conn.cursor()
            if start_time:
                cursor.execute(
                    "SELECT COUNT(*) FROM captures WHERE job_id = ? AND captured_at > ?",
                    (job_id, start_time)
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM captures WHERE job_id = ?",
                    (job_id,)
                )
            count = cursor.fetchone()[0]

        if count == 0:
            logger.info(f"Auto-build skipped for job {job_id} ({job_name}): no new captures")
            return

        # Build video name
        date_str = now.strftime("%Y%m%d_%H%M%S")
        sanitized_name = re.sub(r'[^\w\s-]', '', job_name).strip()
        video_name = f"{sanitized_name}_auto_{date_str}"

        # Prepare output path
        videos_path = config.DEFAULT_VIDEOS_PATH
        job_folder = f"{job_id}_{sanitized_name}"
        job_dir = os.path.join(videos_path, job_folder)
        os.makedirs(job_dir, exist_ok=True)
        output_path = os.path.join(job_dir, f"{video_name}.mp4")

        # Create video record
        now_str = to_iso(now)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO processed_videos (
                    job_id, job_name, name, file_path, file_size, resolution,
                    framerate, quality, start_time, end_time,
                    total_frames, duration_seconds, status, build_source, created_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, 0, 'processing', 'auto', ?)
            """, (
                job_id, job_name, video_name, output_path,
                job.get('auto_build_resolution', '1920x1080'),
                job.get('auto_build_fps', 30),
                job.get('auto_build_quality', 'medium'),
                start_time,
                now_str,
                now_str
            ))
            video_id = cursor.lastrowid

        # Run video processing (blocking in this thread)
        job_dict = dict(job)
        process_video(
            video_id=video_id,
            job_dict=job_dict,
            resolution=job.get('auto_build_resolution', '1920x1080'),
            framerate=job.get('auto_build_fps', 30),
            quality=job.get('auto_build_quality', 'medium'),
            start_capture_id=None,
            end_capture_id=None,
            start_time=start_time,
            end_time=now_str,
            output_path=output_path
        )

        # Check if video completed successfully
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM processed_videos WHERE id = ?",
                (video_id,)
            )
            row = cursor.fetchone()
            video_status = row[0] if row else 'failed'

        if video_status == 'completed':
            logger.info(f"Auto-build completed for job {job_id} ({job_name}): video_id={video_id}")
            send_webhook_event('auto_build_complete', job_name, job_id)
        else:
            logger.warning(f"Auto-build failed for job {job_id} ({job_name}): video status={video_status}")

    except Exception as e:
        logger.error(f"Auto-build error for job {job_id} ({job_name}): {e}", exc_info=True)
    finally:
        # Clear in_progress flag and update last_auto_build_at
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE jobs SET auto_build_in_progress = 0, last_auto_build_at = ? WHERE id = ?",
                (to_iso(now), job_id)
            )

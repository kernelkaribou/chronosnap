"""
Jobs API endpoints
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
import os
import re
import json
import logging
import time
from urllib.parse import urlparse, urlunparse

from ..models import JobCreate, JobUpdate, JobResponse, TestUrlResponse, DurationEstimate, MaintenanceResult, MaintenanceCleanup, MaintenanceImport
from ..database import get_db, dict_from_row
from ..services.url_tester import test_stream_url
from ..services.duration_calculator import calculate_duration
from ..services.image_capture import capture_image
from ..services.event_service import add_event
from ..services.capture_scheduler import get_scheduler
from ..services.maintenance import scan_job_files, cleanup_missing_captures, import_orphaned_files
from ..services.job_state import calculate_job_state
from ..services.auto_builder import get_next_auto_build_at
from ..utils import get_now, to_iso, parse_iso, ensure_timezone_aware
from ..helpers.db_helpers import get_or_404, fetch_tags_for_jobs, set_job_tags
from ..helpers.file_helpers import validate_writable_directory, resolve_capture_path

router = APIRouter()
logger = logging.getLogger(__name__)


def enrich_job_with_next_capture(job_dict: dict) -> dict:
    """Add next_capture_at and next_auto_build_at fields to job dict, compute warning status"""
    now = get_now()
    pending = parse_iso(job_dict['next_scheduled_capture_at']) if job_dict.get('next_scheduled_capture_at') else None
    status, next_capture, reason = calculate_job_state(job_dict, now, pending)
    job_dict['next_capture_at'] = to_iso(next_capture) if next_capture else None
    job_dict['next_auto_build_at'] = get_next_auto_build_at(job_dict)
    # Surface warning as a status when the job has a warning_message
    if job_dict.get('warning_message') and job_dict['status'] not in ('disabled', 'completed'):
        job_dict['status'] = 'warning'
    return job_dict



@router.post("/", response_model=JobResponse, status_code=201)
async def create_job(job: JobCreate):
    """Create a new timelapse job"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Always use the global captures path from settings
        from ..services.import_service import get_captures_path, get_default_naming_pattern
        captures_base = get_captures_path()
        
        if not job.naming_pattern:
            job.naming_pattern = get_default_naming_pattern()
        
        # Validate captures path exists and is writable
        validate_writable_directory(captures_base, "Capture path")
        
        now = get_now()
        now_str = to_iso(now)
        
        # Insert job first to get the ID
        cursor.execute("""
            INSERT INTO jobs (
                name, url, stream_type, start_datetime, end_datetime,
                interval_seconds, framerate, capture_path, naming_pattern,
                time_window_enabled, time_window_start, time_window_end,
                warning_threshold,
                auto_build_enabled, auto_build_interval_hours, auto_build_fps,
                auto_build_quality, auto_build_resolution, auto_build_text_overlay,
                capture_quality, capture_resolution,
                source_width, source_height,
                last_auto_build_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.name, job.url, job.stream_type.value,
            to_iso(job.start_datetime),
            to_iso(job.end_datetime) if job.end_datetime else None,
            job.interval_seconds, job.framerate, "",  # Will update capture_path next
            job.naming_pattern,
            1 if job.time_window_enabled else 0,
            job.time_window_start if job.time_window_enabled else None,
            job.time_window_end if job.time_window_enabled else None,
            job.warning_threshold,
            1 if job.auto_build_enabled else 0,
            job.auto_build_interval_hours,
            job.auto_build_fps,
            job.auto_build_quality,
            job.auto_build_resolution,
            job.auto_build_text_overlay,
            job.capture_quality,
            job.capture_resolution,
            job.source_width,
            job.source_height,
            now_str if job.auto_build_enabled else None,
            now_str, now_str
        ))
        
        job_id = cursor.lastrowid
        
        # Create job directory with ID prefix
        rel_job_dir = f"{job_id}_{job.name}"
        abs_job_dir = os.path.join(captures_base, rel_job_dir)
        try:
            os.makedirs(abs_job_dir, exist_ok=True)
        except PermissionError:
            cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            raise HTTPException(
                status_code=400,
                detail=f"Permission denied creating job directory: {abs_job_dir}"
            )
        except Exception as e:
            cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            raise HTTPException(
                status_code=400,
                detail=f"Failed to create job directory"
            )
        
        # Store the relative directory name
        cursor.execute("UPDATE jobs SET capture_path = ? WHERE id = ?",
                       (rel_job_dir, job_id))
        
        add_event(f"Job '{job.name}' created", "job", {"job_id": job_id})
        
        # Get the job we just created
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job_dict = dict_from_row(cursor.fetchone())
        
        # Calculate initial state
        status, next_capture, reason = calculate_job_state(job_dict, now, pending_capture_time=None)
        
        # Update with calculated state
        cursor.execute(
            "UPDATE jobs SET status = ?, next_scheduled_capture_at = ? WHERE id = ?",
            (status, to_iso(next_capture) if next_capture else None, job_id)
        )
        
        # Get final job state
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        final_job = dict_from_row(cursor.fetchone())
        
        # Set tags if provided
        if job.tag_ids:
            set_job_tags(cursor, job_id, job.tag_ids)
        final_job['tags'] = fetch_tags_for_jobs(cursor, [job_id]).get(job_id, [])
        
        logger.info(f"Created job '{job.name}' (ID: {job_id}) with status: {status} - {reason}")
        return enrich_job_with_next_capture(final_job)


@router.get("/", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """List all timelapse jobs"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if status:
            cursor.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset)
            )
        else:
            cursor.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
        
        rows = cursor.fetchall()
        job_ids = [row['id'] for row in rows]
        
        # Batch-fetch latest capture per job in one query
        latest_captures = {}
        if job_ids:
            placeholders = ','.join('?' for _ in job_ids)
            cursor.execute(f"""
                SELECT c.* FROM captures c
                INNER JOIN (
                    SELECT job_id, MAX(captured_at) as max_captured_at
                    FROM captures WHERE job_id IN ({placeholders})
                    GROUP BY job_id
                ) latest ON c.job_id = latest.job_id AND c.captured_at = latest.max_captured_at
            """, job_ids)
            for cap_row in cursor.fetchall():
                latest_captures[cap_row['job_id']] = dict_from_row(cap_row)
        
        # Batch-fetch tags per job
        tags_by_job = fetch_tags_for_jobs(cursor, job_ids) if job_ids else {}
        
        jobs = []
        for row in rows:
            job = dict_from_row(row)
            job['latest_capture'] = latest_captures.get(job['id'])
            job['tags'] = tags_by_job.get(job['id'], [])
            jobs.append(enrich_job_with_next_capture(job))
        
        return jobs


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int):
    """Get a specific job by ID"""
    with get_db() as conn:
        cursor = conn.cursor()
        job = get_or_404(cursor, "SELECT * FROM jobs WHERE id = ?", (job_id,), "Job not found")
        
        # Get latest capture for this job
        cursor.execute(
            "SELECT * FROM captures WHERE job_id = ? ORDER BY captured_at DESC LIMIT 1",
            (job_id,)
        )
        latest_capture_row = cursor.fetchone()
        job['latest_capture'] = dict_from_row(latest_capture_row) if latest_capture_row else None
        job['tags'] = fetch_tags_for_jobs(cursor, [job_id]).get(job_id, [])
        
        return enrich_job_with_next_capture(job)



@router.patch("/{job_id}", response_model=JobResponse)
async def update_job(job_id: int, job_update: JobUpdate):
    """Update a job's configuration"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check if job exists and get current job data
        current_job = get_or_404(cursor, "SELECT * FROM jobs WHERE id = ?", (job_id,), "Job not found")
        
        # Build update query dynamically
        updates = []
        values = []
        
        if job_update.name is not None:
            updates.append("name = ?")
            values.append(job_update.name)
        
        if job_update.url is not None:
            updates.append("url = ?")
            values.append(job_update.url)
        
        if job_update.stream_type is not None:
            updates.append("stream_type = ?")
            values.append(job_update.stream_type.value)
        
        if job_update.start_datetime is not None:
            updates.append("start_datetime = ?")
            values.append(to_iso(job_update.start_datetime))
        
        # Validate and handle end_datetime if being updated
        if hasattr(job_update, 'end_datetime') and job_update.model_fields_set and 'end_datetime' in job_update.model_fields_set:
            end_time = job_update.end_datetime
            
            if end_time is not None:
                # Ensure timezone awareness for comparison
                end_time = ensure_timezone_aware(end_time)
                now = get_now()
                
                # Only validate future end time if not explicitly completing the job
                # Allow end_datetime to be now or past when status is being set to completed
                is_completing = job_update.status is not None and job_update.status.value == 'completed'
                
                if not is_completing:
                    # Check if end time is in the past
                    if end_time <= now:
                        raise HTTPException(status_code=400, detail="End time must be in the future")
                    
                    # Check if end time is at least one interval in the future
                    min_end_time = now.timestamp() + current_job['interval_seconds']
                    if end_time.timestamp() < min_end_time:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"End time must be at least {current_job['interval_seconds']} seconds in the future"
                        )
            
            # Add to updates (can be None for ongoing jobs)
            # Status will be recalculated later based on end_datetime and time windows
            updates.append("end_datetime = ?")
            values.append(to_iso(end_time) if end_time else None)
        
        if job_update.interval_seconds is not None:
            updates.append("interval_seconds = ?")
            values.append(job_update.interval_seconds)
        
        if job_update.framerate is not None:
            updates.append("framerate = ?")
            values.append(job_update.framerate)
        
        if job_update.warning_threshold is not None:
            updates.append("warning_threshold = ?")
            values.append(job_update.warning_threshold)
        
        if job_update.auto_build_enabled is not None:
            updates.append("auto_build_enabled = ?")
            values.append(1 if job_update.auto_build_enabled else 0)
            # When enabling auto-build, seed last_auto_build_at to now so the
            # first build covers only future captures instead of the entire history
            if job_update.auto_build_enabled and not current_job.get('auto_build_enabled'):
                updates.append("last_auto_build_at = ?")
                values.append(to_iso(get_now()))
        
        if job_update.auto_build_interval_hours is not None:
            updates.append("auto_build_interval_hours = ?")
            values.append(job_update.auto_build_interval_hours)
        
        if job_update.auto_build_fps is not None:
            updates.append("auto_build_fps = ?")
            values.append(job_update.auto_build_fps)
        
        if job_update.auto_build_quality is not None:
            updates.append("auto_build_quality = ?")
            values.append(job_update.auto_build_quality)
        
        if job_update.auto_build_resolution is not None:
            updates.append("auto_build_resolution = ?")
            values.append(job_update.auto_build_resolution)
        
        if job_update.auto_build_text_overlay is not None:
            updates.append("auto_build_text_overlay = ?")
            values.append(job_update.auto_build_text_overlay)
        
        if job_update.capture_quality is not None:
            updates.append("capture_quality = ?")
            values.append(job_update.capture_quality)
        
        if job_update.capture_resolution is not None:
            updates.append("capture_resolution = ?")
            values.append(job_update.capture_resolution)
        
        # Track manual status changes
        manual_status_change = False
        if job_update.status is not None:
            updates.append("status = ?")
            values.append(job_update.status.value)
            manual_status_change = job_update.status.value in ('completed', 'disabled')
            if job_update.status.value == 'completed':
                add_event(f"Job '{current_job['name']}' completed", "job", {"job_id": job_id})
        
        # Track if schedule-affecting fields are being updated
        schedule_changed = False
        
        # Handle time window updates
        if job_update.time_window_enabled is not None:
            updates.append("time_window_enabled = ?")
            values.append(1 if job_update.time_window_enabled else 0)
            schedule_changed = True
        
        if job_update.time_window_start is not None:
            updates.append("time_window_start = ?")
            values.append(job_update.time_window_start)
            schedule_changed = True
        
        if job_update.time_window_end is not None:
            updates.append("time_window_end = ?")
            values.append(job_update.time_window_end)
            schedule_changed = True
        
        # Check if interval or start time changed
        if job_update.interval_seconds is not None:
            schedule_changed = True
        
        if job_update.start_datetime is not None:
            schedule_changed = True
        
        # End date changes affect status
        if job_update.end_datetime is not None:
            schedule_changed = True
        
        if not updates and job_update.tag_ids is None:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        if updates:
            updates.append("updated_at = ?")
            values.append(to_iso(get_now()))
            values.append(job_id)
            
            query = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
        
        # Reload job with updates
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        updated_job = dict_from_row(cursor.fetchone())
        
        # Recalculate state using state manager if needed (within same transaction)
        # Recalculate state using state calculator if needed (within same transaction)
        if schedule_changed and not manual_status_change:
            pending = parse_iso(updated_job['next_scheduled_capture_at']) if updated_job.get('next_scheduled_capture_at') else None
            new_status, next_capture, reason = calculate_job_state(updated_job, get_now(), pending)
            
            cursor.execute(
                "UPDATE jobs SET status = ?, next_scheduled_capture_at = ? WHERE id = ?",
                (new_status, to_iso(next_capture) if next_capture else None, job_id)
            )
            
            # Reload with new state
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            updated_job = dict_from_row(cursor.fetchone())
            logger.info(f"Job {job_id}: Schedule updated, new status: {new_status} - {reason}")
            
        elif job_update.status is not None and job_update.status.value == 'active':
            # Re-enabling - recalculate state and clear warnings/failure counts
            new_status, next_capture, reason = calculate_job_state(updated_job, get_now(), pending_capture_time=None)
            
            cursor.execute(
                "UPDATE jobs SET status = ?, next_scheduled_capture_at = ?, warning_message = NULL WHERE id = ?",
                (new_status, to_iso(next_capture) if next_capture else None, job_id)
            )
            
            # Reset in-memory failure count so it gets a fresh start
            scheduler = get_scheduler()
            scheduler.failure_counts.pop(job_id, None)
            
            # Reload with new state
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            updated_job = dict_from_row(cursor.fetchone())
            logger.info(f"Job {job_id}: Re-enabled, new status: {new_status} - {reason}")
        
        # Clear warning when manually disabling or completing a job
        if manual_status_change and updated_job.get('warning_message'):
            cursor.execute("UPDATE jobs SET warning_message = NULL WHERE id = ?", (job_id,))
            updated_job['warning_message'] = None
            logger.info(f"Job {job_id}: Cleared warning on manual {job_update.status.value}")
        
        # Update tags if provided
        if job_update.tag_ids is not None:
            set_job_tags(cursor, job_id, job_update.tag_ids)
        updated_job['tags'] = fetch_tags_for_jobs(cursor, [job_id]).get(job_id, [])
        
        # Log changes
        changes = [f"{field}" for field in job_update.model_fields_set]
        if changes:
            logger.info(f"Updated job '{current_job['name']}' (ID: {job_id}) - Changed: {', '.join(changes)}")
        
        return enrich_job_with_next_capture(updated_job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: int):
    """
    Permanently delete a job, all its capture records, and all capture files.
    Timelapse videos created from this job are preserved (their job_id is set to NULL).
    """
    import shutil
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check if job exists and get capture path
        job_info = get_or_404(cursor,
            "SELECT name, capture_path FROM jobs WHERE id = ?",
            (job_id,), "Job not found")
        
        # Delete the entire job folder from disk
        if job_info['capture_path']:
            try:
                abs_job_dir = resolve_capture_path(job_info['capture_path'])
                if os.path.exists(abs_job_dir) and os.path.isdir(abs_job_dir):
                    shutil.rmtree(abs_job_dir)
                    logger.info(f"Deleted job folder: {abs_job_dir}")
            except Exception as e:
                logger.warning(f"Failed to delete job folder {job_info['capture_path']}: {e}")
        
        # Delete job (cascades captures, sets NULL on processed_videos)
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        
        add_event(f"Job '{job_info['name']}' deleted", "job", {"job_id": job_id})
        logger.info(f"Deleted job '{job_info['name']}' (ID: {job_id}) and all captures")


@router.post("/test-url", response_model=TestUrlResponse)
async def test_url(url: str, stream_type: str = Query(None, pattern=r"^(http|rtsp|device)$"),
                   quality: str = Query('maximum', pattern=r"^(maximum|high|medium|low)$"),
                   resolution: str = Query('native', pattern=r"^(native|\d+x\d+)$")):
    """Test a URL or device path and capture a sample image with optional quality/resolution settings"""
    # Validate device paths
    if url.startswith('/dev/'):
        import re
        if not re.match(r'^/dev/video\d+$', url):
            raise HTTPException(status_code=400, detail="Invalid device path. Must be /dev/videoN")
    result = await test_stream_url(url, stream_type, quality, resolution)
    return result


@router.get("/{job_id}/duration-estimate", response_model=DurationEstimate)
async def estimate_duration(
    job_id: int,
    hours: Optional[float] = Query(None, description="Hours to estimate (for ongoing jobs)"),
    days: Optional[float] = Query(None, description="Days to estimate (for ongoing jobs)")
):
    """Calculate estimated video duration based on capture settings"""
    with get_db() as conn:
        cursor = conn.cursor()
        job_dict = get_or_404(cursor, "SELECT * FROM jobs WHERE id = ?", (job_id,), "Job not found")
        return calculate_duration(job_dict, hours, days)


@router.get("/{job_id}/latest-image")
async def get_latest_image(job_id: int):
    """Get the path to the latest captured image for a job"""
    with get_db() as conn:
        cursor = conn.cursor()
        cap = get_or_404(cursor, """
            SELECT file_path FROM captures
            WHERE job_id = ?
            ORDER BY captured_at DESC
            LIMIT 1
        """, (job_id,), "No captures found for this job")
        
        return {"file_path": cap['file_path']}


@router.post("/{job_id}/capture")
async def manual_capture(job_id: int):
    """
    Take a manual snapshot using the job's existing stream settings.
    Does not affect the job's scheduling state (next_scheduled_capture_at, status).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        job = get_or_404(cursor, "SELECT * FROM jobs WHERE id = ?", (job_id,), "Job not found")
    
    logger.info(f"Manual capture requested for job '{job['name']}' (ID: {job_id})")
    
    # Prevent duplicate capture if scheduler is already capturing this job
    scheduler = get_scheduler()
    if scheduler.is_capture_in_progress(job_id):
        raise HTTPException(status_code=409, detail="A capture is already in progress for this job")
    
    success, error_msg = capture_image(job)
    
    if not success:
        raise HTTPException(status_code=500, detail="Capture failed")
    
    # Update last_captured_at for display purposes only — scheduling is unaffected
    now = get_now()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE jobs SET last_captured_at = ?, updated_at = ? WHERE id = ?",
            (to_iso(now), to_iso(now), job_id)
        )
    
    logger.info(f"Manual capture completed for job '{job['name']}' (ID: {job_id})")
    return {"success": True, "message": f"Manual capture completed for '{job['name']}'"}


@router.post("/{job_id}/maintenance/scan", response_model=MaintenanceResult)
async def scan_job_maintenance(job_id: int):
    """
    Scan a job's captures to identify missing files on disk.
    Returns a list of captures that reference files that no longer exist.
    """
    try:
        result = scan_job_files(job_id)
        logger.info(f"Maintenance scan completed for job {job_id}: "
                   f"{result['missing_count']} missing files found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error during maintenance scan for job {job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Maintenance scan failed")


@router.post("/{job_id}/maintenance/cleanup")
async def cleanup_job_maintenance(job_id: int, cleanup: MaintenanceCleanup):
    """
    Remove database records for captures that are missing on disk.
    This endpoint should be called after scan to confirm which records to delete.
    """
    try:
        if not cleanup.capture_ids:
            raise HTTPException(status_code=400, detail="No capture IDs provided")
        
        result = cleanup_missing_captures(job_id, cleanup.capture_ids)
        logger.info(f"Maintenance cleanup completed for job {job_id}: "
                   f"{result['deleted_count']} records removed")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during maintenance cleanup for job {job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Maintenance cleanup failed")


@router.post("/{job_id}/maintenance/import")
async def import_job_maintenance(job_id: int, import_data: MaintenanceImport):
    """
    Import orphaned files found on disk into the database.
    This endpoint should be called after scan to add missing capture records.
    """
    try:
        if not import_data.orphaned_files:
            raise HTTPException(status_code=400, detail="No orphaned files provided")
        
        result = import_orphaned_files(job_id, import_data.orphaned_files)
        logger.info(f"Maintenance import completed for job {job_id}: "
                   f"{result['imported_count']} files imported")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during maintenance import for job {job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Maintenance import failed")


# ── Job Export ───────────────────────────────────────────────────────────

def _get_export_files(job_id: int, job_name: str):
    """Gather all files for a job export: captures, videos, and job metadata.
    
    Returns (files, total_size, job_dict) where files is a list of
    (archive_path, disk_path) tuples.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        job = get_or_404(cursor, "SELECT * FROM jobs WHERE id = ?", (job_id,), "Job not found")
        job_dict = dict(job)
        
        # Get all captures
        cursor.execute(
            "SELECT file_path, file_size FROM captures WHERE job_id = ? ORDER BY captured_at",
            (job_id,)
        )
        captures = cursor.fetchall()
        
        # Get all completed videos
        cursor.execute(
            "SELECT file_path, file_size, thumbnail_path FROM processed_videos WHERE job_id = ? AND status = 'completed'",
            (job_id,)
        )
        videos = cursor.fetchall()
    
    sanitized = re.sub(r'[^\w -]', '', job_name.replace('\n', '').replace('\r', '')).strip().replace(' ', '_')
    prefix = f"{job_id}_{sanitized}"
    
    files = []
    total_size = 0
    
    # Captures -- preserve directory structure relative to job folder
    job_dir = job_dict.get('capture_path') or f"{job_id}_{job_name}"
    abs_job_dir = os.path.normpath(resolve_capture_path(job_dir))
    for row in captures:
        disk_path = os.path.normpath(resolve_capture_path(row[0]))
        if os.path.islink(disk_path) or not os.path.isfile(disk_path):
            continue
        if disk_path.startswith(abs_job_dir + os.sep) or disk_path == abs_job_dir:
            rel = os.path.relpath(disk_path, abs_job_dir)
            if '..' in rel.split(os.sep):
                rel = os.path.basename(disk_path)
        else:
            rel = os.path.basename(disk_path)
        archive_path = f"{prefix}/captures/{rel}"
        files.append((archive_path, disk_path))
        total_size += row[1] or os.path.getsize(disk_path)
    
    # Videos + thumbnails
    from ..helpers.file_helpers import resolve_video_path
    for row in videos:
        disk_path = resolve_video_path(row[0])
        if os.path.islink(disk_path) or not os.path.isfile(disk_path):
            continue
        archive_path = f"{prefix}/videos/{os.path.basename(disk_path)}"
        files.append((archive_path, disk_path))
        total_size += row[1] or os.path.getsize(disk_path)
        
        # Include thumbnail if exists
        thumb_path = resolve_video_path(row[2]) if row[2] else None
        if thumb_path and not os.path.islink(thumb_path) and os.path.isfile(thumb_path):
            archive_path = f"{prefix}/videos/{os.path.basename(thumb_path)}"
            files.append((archive_path, thumb_path))
            total_size += os.path.getsize(thumb_path)
    
    return files, total_size, job_dict


@router.get("/{job_id}/export/estimate")
async def export_estimate(job_id: int):
    """Get size estimate and file counts for a job export."""
    with get_db() as conn:
        cursor = conn.cursor()
        job = get_or_404(cursor, "SELECT id, name FROM jobs WHERE id = ?", (job_id,), "Job not found")
        
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM captures WHERE job_id = ?",
            (job_id,)
        )
        cap_count, cap_size = cursor.fetchone()
        
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM processed_videos WHERE job_id = ? AND status = 'completed'",
            (job_id,)
        )
        vid_count, vid_size = cursor.fetchone()
    
    total_size = cap_size + vid_size
    
    return {
        'job_id': job_id,
        'job_name': job['name'],
        'capture_count': cap_count,
        'capture_size': cap_size,
        'video_count': vid_count,
        'video_size': vid_size,
        'total_size': total_size,
    }


@router.post("/{job_id}/export")
async def export_job(job_id: int):
    """Export a job as a streamed ZIP archive built on-the-fly."""
    with get_db() as conn:
        cursor = conn.cursor()
        job = get_or_404(cursor, "SELECT id, name FROM jobs WHERE id = ?", (job_id,), "Job not found")
        job_tags = fetch_tags_for_jobs(cursor, [job_id]).get(job_id, [])
    
    job_name = job['name']
    files, total_size, job_dict = _get_export_files(job_id, job_name)
    
    if not files:
        raise HTTPException(status_code=404, detail="No files to export for this job")
    
    sanitized = re.sub(r'[^\w -]', '', job_name.replace('\n', '').replace('\r', '')).strip().replace(' ', '_')
    ts = int(time.time())
    zip_name = f"{job_id}_{sanitized}_{ts}_export.zip"
    prefix = f"{job_id}_{sanitized}"
    
    # Redact credentials from stream URL for export metadata
    raw_url = job_dict.get('stream_url', '')
    try:
        parsed = urlparse(raw_url)
        if parsed.username or parsed.password:
            safe_url = urlunparse(parsed._replace(netloc=f"***@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")))
        else:
            safe_url = raw_url
    except Exception:
        safe_url = '(redacted)'
    
    metadata = {
        'job_id': job_dict['id'],
        'name': job_dict['name'],
        'stream_url': safe_url,
        'stream_type': job_dict.get('stream_type', ''),
        'interval_seconds': job_dict.get('interval_seconds'),
        'start_date': job_dict.get('start_date'),
        'end_date': job_dict.get('end_date'),
        'time_window_start': job_dict.get('time_window_start'),
        'time_window_end': job_dict.get('time_window_end'),
        'created_at': job_dict.get('created_at'),
        'capture_count': len([f for f in files if '/captures/' in f[0]]),
        'video_count': len([f for f in files if '/videos/' in f[0] and not f[0].endswith('_thumb.jpg')]),
        'total_size': total_size,
        'exported_at': to_iso(get_now()),
        'tags': [{'name': t['name'], 'color': t['color']} for t in job_tags],
    }
    metadata_json = json.dumps(metadata, indent=2)
    
    import zipstream
    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)
    zs.add(metadata_json.encode(), f"{prefix}/job.json")
    for archive_path, disk_path in files:
        zs.add_path(disk_path, archive_path)
    
    add_event(f"Export completed for job '{job_name}'", "export", {"job_id": job_id})
    
    return StreamingResponse(
        zs,
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{zip_name}"',
        }
    )


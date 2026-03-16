"""
Captures API endpoints
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import List, Optional
from pydantic import BaseModel
import os
import re
import shutil
import logging

from ..models import CaptureResponse, CaptureListResponse, CaptureDeleteRequest
from ..database import get_db, dict_from_row
from ..utils import get_now, to_iso, parse_iso
from ..helpers.db_helpers import get_or_404, fetch_one, enrich_capture, decrement_job_stats
from ..helpers.file_helpers import delete_capture_file
from ..services.thumbnail_generator import get_thumbnail_path, has_thumbnail, delete_thumbnail
from .. import config

router = APIRouter()
logger = logging.getLogger(__name__)


class FavoriteRequest(BaseModel):
    ids: List[int]
    is_favorite: bool = True


@router.get("/", response_model=CaptureListResponse)
async def list_captures(
    job_id: Optional[int] = Query(None, description="Filter by job ID"),
    start_time: Optional[str] = Query(None, description="Start time (ISO format)"),
    end_time: Optional[str] = Query(None, description="End time (ISO format)"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    sort_order: str = Query("asc", regex="^(asc|desc)$", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page")
):
    """List captures with pagination and filtering"""
    offset = (page - 1) * page_size
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Build query with filters
        conditions = []
        params = []
        
        if job_id is not None:
            conditions.append("c.job_id = ?")
            params.append(job_id)
        
        if start_time:
            try:
                parse_iso(start_time)  # Validate format
                conditions.append("c.captured_at >= ?")
                params.append(start_time)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid start_time format")
        
        if end_time:
            try:
                parse_iso(end_time)  # Validate format
                conditions.append("c.captured_at <= ?")
                params.append(end_time)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid end_time format")
        
        if favorites_only:
            conditions.append("c.is_favorite = 1")
        
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        # Determine sort order
        order_direction = "ASC" if sort_order == "asc" else "DESC"
        
        # Get total count
        count_query = f"""
            SELECT COUNT(*) FROM captures c
            {where_clause}
        """
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        # Get captures with job name
        query = f"""
            SELECT c.*, j.name as job_name
            FROM captures c
            LEFT JOIN jobs j ON c.job_id = j.id
            {where_clause}
            ORDER BY c.captured_at {order_direction}
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, params + [page_size, offset])
        
        captures = []
        for row in cursor.fetchall():
            capture_dict = dict_from_row(row)
            enrich_capture(capture_dict, has_thumbnail, get_thumbnail_path)
            captures.append(capture_dict)
        
        total_pages = (total + page_size - 1) // page_size
        
        return {
            "captures": captures,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }


class OrphanedCleanupRequest(BaseModel):
    folders: Optional[List[str]] = None
    job_ids: Optional[List[int]] = None
    delete_all: bool = False


@router.get("/orphaned")
async def scan_orphaned_captures():
    """
    Scan for orphaned captures:
    1. Filesystem orphans: folders on disk that don't belong to any existing job
    2. Database orphans: capture records referencing deleted jobs
    """
    captures_base = config.DEFAULT_CAPTURES_PATH
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get all existing job IDs and their capture_paths
        cursor.execute("SELECT id, capture_path FROM jobs")
        jobs = {row[0]: row[1] for row in cursor.fetchall()}
        active_job_ids = set(jobs.keys())
        active_paths = set(jobs.values())
        
        # --- Database orphans: captures referencing deleted jobs ---
        cursor.execute("""
            SELECT c.job_id, COUNT(*) as cnt, SUM(c.file_size) as total_size
            FROM captures c
            LEFT JOIN jobs j ON c.job_id = j.id
            WHERE j.id IS NULL
            GROUP BY c.job_id
            ORDER BY c.job_id
        """)
        
        db_orphaned_groups = []
        db_total_records = 0
        db_total_size = 0
        
        for row in cursor.fetchall():
            job_id, count, size = row[0], row[1], row[2] or 0
            db_orphaned_groups.append({
                "type": "database",
                "original_job_id": job_id,
                "original_job_name": f"Deleted Job #{job_id}",
                "record_count": count,
                "total_size": size
            })
            db_total_records += count
            db_total_size += size
    
    # --- Filesystem orphans: folders with no matching job ---
    fs_orphaned_groups = []
    fs_total_files = 0
    fs_total_size = 0
    
    if os.path.exists(captures_base):
        for entry in os.scandir(captures_base):
            if not entry.is_dir():
                continue
            
            folder_path = entry.path
            
            if folder_path in active_paths:
                continue
            
            folder_name = entry.name
            match = re.match(r'^(\d+)_(.+)$', folder_name)
            original_job_id = int(match.group(1)) if match else None
            original_job_name = match.group(2) if match else folder_name
            
            if original_job_id and original_job_id in active_job_ids:
                continue
            
            file_count = 0
            folder_size = 0
            for root, dirs, files in os.walk(folder_path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        file_count += 1
                        folder_size += os.path.getsize(fp)
                    except OSError:
                        pass
            
            fs_orphaned_groups.append({
                "type": "filesystem",
                "folder_path": folder_path,
                "folder_name": folder_name,
                "original_job_id": original_job_id,
                "original_job_name": original_job_name,
                "file_count": file_count,
                "total_size": folder_size
            })
            fs_total_files += file_count
            fs_total_size += folder_size
    
    # Merge: DB orphans that also have a filesystem folder get combined
    for db_group in db_orphaned_groups:
        job_id = db_group['original_job_id']
        matching_fs = next((g for g in fs_orphaned_groups if g['original_job_id'] == job_id), None)
        if matching_fs:
            matching_fs['type'] = 'both'
            matching_fs['record_count'] = db_group['record_count']
            matching_fs['db_size'] = db_group['total_size']
            matching_fs['total_size'] += db_group['total_size']
        # Otherwise DB-only group stands alone
    
    # Build final list: combined fs groups + db-only groups
    all_groups = list(fs_orphaned_groups)
    fs_job_ids = {g['original_job_id'] for g in fs_orphaned_groups}
    for db_group in db_orphaned_groups:
        if db_group['original_job_id'] not in fs_job_ids:
            all_groups.append(db_group)
    
    all_groups.sort(key=lambda g: (g.get('original_job_id') is None, g.get('original_job_id') or 0))
    
    return {
        "orphaned_groups": all_groups,
        "total_fs_files": fs_total_files,
        "total_fs_size": fs_total_size,
        "total_db_records": db_total_records,
        "total_db_size": db_total_size
    }


@router.post("/orphaned/cleanup")
async def cleanup_orphaned_captures(request: OrphanedCleanupRequest):
    """
    Delete orphaned captures from disk and/or database.
    Supports folder paths (filesystem), job_ids (database records), or delete_all.
    """
    captures_base = config.DEFAULT_CAPTURES_PATH
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, capture_path FROM jobs")
        jobs = {row[0]: row[1] for row in cursor.fetchall()}
        active_job_ids = set(jobs.keys())
        active_paths = set(jobs.values())
    
    deleted_folders = []
    deleted_db_groups = []
    errors = []
    total_freed = 0
    total_db_records_deleted = 0
    
    if request.delete_all:
        scan = await scan_orphaned_captures()
        folders_to_delete = [g['folder_path'] for g in scan['orphaned_groups'] if g.get('folder_path')]
        db_job_ids_to_delete = [g['original_job_id'] for g in scan['orphaned_groups'] 
                                if g['type'] in ('database', 'both') and g.get('original_job_id')]
    else:
        folders_to_delete = request.folders or []
        db_job_ids_to_delete = request.job_ids or []
    
    # Delete filesystem folders
    for folder_path in folders_to_delete:
        resolved_path = os.path.abspath(folder_path)
        resolved_base = os.path.abspath(captures_base)
        if not resolved_path.startswith(resolved_base + os.sep):
            errors.append(f"{folder_path}: not under captures directory")
            continue
        if folder_path in active_paths:
            errors.append(f"{folder_path}: belongs to an active job")
            continue
        if not os.path.exists(folder_path):
            errors.append(f"{folder_path}: does not exist")
            continue
        
        try:
            folder_size = 0
            for root, dirs, files in os.walk(folder_path):
                for f in files:
                    try:
                        folder_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            
            shutil.rmtree(folder_path)
            deleted_folders.append(folder_path)
            total_freed += folder_size
            logger.info(f"Deleted orphaned capture folder: {folder_path}")
        except Exception as e:
            logger.error(f"Failed to delete orphaned folder {folder_path}: {e}")
            errors.append(f"{folder_path}: {str(e)}")
    
    # Delete database orphan records
    for job_id in db_job_ids_to_delete:
        if job_id in active_job_ids:
            errors.append(f"Job #{job_id}: still active, skipping")
            continue
        
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                
                # Delete associated files from disk first
                cursor.execute("SELECT file_path FROM captures WHERE job_id = ?", (job_id,))
                file_paths = [row[0] for row in cursor.fetchall()]
                
                for fp in file_paths:
                    try:
                        if os.path.exists(fp):
                            os.remove(fp)
                            # Also delete thumbnail
                            delete_thumbnail(fp)
                    except OSError:
                        pass
                
                # Delete DB records
                cursor.execute("SELECT COUNT(*) FROM captures WHERE job_id = ?", (job_id,))
                count = cursor.fetchone()[0]
                cursor.execute("DELETE FROM captures WHERE job_id = ?", (job_id,))
                
                deleted_db_groups.append(job_id)
                total_db_records_deleted += count
                logger.info(f"Deleted {count} orphaned DB records for job_id={job_id}")
        except Exception as e:
            logger.error(f"Failed to delete orphaned DB records for job {job_id}: {e}")
            errors.append(f"Job #{job_id}: {str(e)}")
    
    return {
        "deleted_folders": deleted_folders,
        "deleted_db_job_ids": deleted_db_groups,
        "total_folders_deleted": len(deleted_folders),
        "total_db_records_deleted": total_db_records_deleted,
        "total_freed": total_freed,
        "errors": errors
    }


@router.get("/{capture_id}", response_model=CaptureResponse)
async def get_capture(capture_id: int):
    """Get a specific capture by ID with job name and thumbnail info"""
    with get_db() as conn:
        cursor = conn.cursor()
        capture_dict = get_or_404(cursor,
            "SELECT c.*, j.name as job_name FROM captures c LEFT JOIN jobs j ON c.job_id = j.id WHERE c.id = ?",
            (capture_id,), "Capture not found")
        enrich_capture(capture_dict, has_thumbnail, get_thumbnail_path)
        return capture_dict


@router.delete("/{capture_id}", status_code=204)
async def delete_capture(capture_id: int):
    """Delete a specific capture and its files"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        cap = get_or_404(cursor,
            "SELECT file_path, file_size, job_id FROM captures WHERE id = ?",
            (capture_id,), "Capture not found")
        
        # Delete the image file
        try:
            delete_capture_file(cap['file_path'], delete_thumbnail)
        except Exception as e:
            logger.error(f"Failed to delete capture file {cap['file_path']}: {e}")
        
        # Delete capture record
        cursor.execute("DELETE FROM captures WHERE id = ?", (capture_id,))
        
        # Update job statistics
        decrement_job_stats(cursor, cap['job_id'], cap['file_size'], to_iso(get_now()))


@router.post("/delete-multiple", status_code=200)
async def delete_multiple_captures(request: CaptureDeleteRequest):
    """Delete multiple captures at once"""
    if not request.capture_ids:
        raise HTTPException(status_code=400, detail="No capture IDs provided")
    
    deleted_count = 0
    errors = []
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        for capture_id in request.capture_ids:
            try:
                # Get capture info
                cap = fetch_one(cursor,
                    "SELECT file_path, file_size, job_id FROM captures WHERE id = ?",
                    (capture_id,))
                
                if not cap:
                    errors.append(f"Capture {capture_id} not found")
                    continue
                
                # Delete files
                try:
                    delete_capture_file(cap['file_path'], delete_thumbnail)
                except Exception as e:
                    logger.error(f"Failed to delete files for capture {capture_id}: {e}")
                
                # Delete record
                cursor.execute("DELETE FROM captures WHERE id = ?", (capture_id,))
                
                # Update job statistics
                decrement_job_stats(cursor, cap['job_id'], cap['file_size'], to_iso(get_now()))
                
                deleted_count += 1
                
            except Exception as e:
                logger.error(f"Error deleting capture {capture_id}: {e}")
                errors.append(f"Capture {capture_id}: {str(e)}")
    
    return {
        "deleted": deleted_count,
        "requested": len(request.capture_ids),
        "errors": errors
    }


@router.post("/favorite", status_code=200)
async def set_capture_favorites(request: FavoriteRequest):
    """Set or unset favorite status on multiple captures"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="No capture IDs provided")
    
    value = 1 if request.is_favorite else 0
    placeholders = ','.join('?' * len(request.ids))
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE captures SET is_favorite = ? WHERE id IN ({placeholders})",
            [value] + request.ids
        )
        updated = cursor.rowcount
    
    return {"updated": updated, "requested": len(request.ids)}


@router.get("/job/{job_id}/count")
async def get_capture_count(job_id: int):
    """Get the total number of captures for a job"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM captures WHERE job_id = ?", (job_id,))
        count = cursor.fetchone()[0]
        
        return {"job_id": job_id, "count": count}


@router.get("/job/{job_id}/time-range")
async def get_capture_time_range(
    job_id: int,
    start_time: Optional[str] = Query(None, description="Start time for filtering (ISO format)"),
    end_time: Optional[str] = Query(None, description="End time for filtering (ISO format)")
):
    """Get capture count and first/last capture times for a job, optionally filtered by time range"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Build query based on time filters
        if start_time and end_time:
            # Count captures in time range
            cursor.execute("""
                SELECT COUNT(*), MIN(captured_at), MAX(captured_at)
                FROM captures
                WHERE job_id = ? AND captured_at >= ? AND captured_at <= ?
            """, (job_id, start_time, end_time))
        else:
            # Get overall stats
            cursor.execute("""
                SELECT COUNT(*), MIN(captured_at), MAX(captured_at)
                FROM captures
                WHERE job_id = ?
            """, (job_id,))
        
        row = cursor.fetchone()
        count, first_time, last_time = row
        
        return {
            "job_id": job_id,
            "count": count,
            "first_capture_time": first_time,
            "last_capture_time": last_time
        }


@router.get("/job/{job_id}/nearest")
async def get_nearest_capture(
    job_id: int,
    timestamp: str = Query(..., description="ISO 8601 timestamp to find nearest capture to")
):
    """Find the capture closest to a given timestamp for a job"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Find closest capture before/at and after the timestamp, pick the nearer one
        cursor.execute("""
            SELECT *, ABS(julianday(captured_at) - julianday(?)) as time_diff
            FROM captures
            WHERE job_id = ?
            ORDER BY time_diff ASC
            LIMIT 1
        """, (timestamp, job_id))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No captures found for this job")
        
        capture = dict_from_row(row)
        capture.pop('time_diff', None)
        
        # Add job name
        cursor.execute("SELECT name FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        capture['job_name'] = job_row[0] if job_row else None
        enrich_capture(capture, has_thumbnail, get_thumbnail_path)
        
        return capture


@router.get("/{capture_id}/image")
async def get_capture_image(capture_id: int):
    """Serve the actual capture image file"""
    with get_db() as conn:
        cursor = conn.cursor()
        cap = get_or_404(cursor,
            "SELECT file_path FROM captures WHERE id = ?",
            (capture_id,), "Capture not found")
        
        file_path = cap['file_path']
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Capture file not found on disk")
        
        if not os.access(file_path, os.R_OK):
            raise HTTPException(status_code=403, detail="No read permission for capture file")
        
        return FileResponse(file_path, media_type="image/jpeg")


@router.get("/{capture_id}/thumbnail")
async def get_capture_thumbnail(capture_id: int):
    """Serve the thumbnail image file"""
    with get_db() as conn:
        cursor = conn.cursor()
        cap = get_or_404(cursor,
            "SELECT file_path FROM captures WHERE id = ?",
            (capture_id,), "Capture not found")
        
        file_path = cap['file_path']
        thumbnail_path = get_thumbnail_path(file_path)
        
        if not os.path.exists(thumbnail_path):
            # Try to generate thumbnail on-the-fly
            from ..services.thumbnail_generator import generate_thumbnail
            success, error = generate_thumbnail(file_path)
            if not success:
                raise HTTPException(status_code=404, detail="Thumbnail not available")
        
        if not os.access(thumbnail_path, os.R_OK):
            raise HTTPException(status_code=403, detail="No read permission for thumbnail file")
        
        return FileResponse(thumbnail_path, media_type="image/webp")

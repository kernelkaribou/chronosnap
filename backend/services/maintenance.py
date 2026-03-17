"""
Maintenance service for checking and cleaning up job files
"""
import os
import logging
import re
from datetime import datetime
from typing import Dict, List, Any
from PIL import Image
from ..database import get_db, dict_from_row
from ..utils import get_now, to_iso, ensure_timezone_aware
from ..helpers.file_helpers import resolve_capture_path, make_relative

logger = logging.getLogger(__name__)


def extract_timestamp_from_file(file_path: str) -> datetime:
    """
    Extract timestamp from image file using multiple methods
    
    Priority order:
    1. Filename pattern (YYYYMMDD_HHMMSS)
    2. EXIF DateTimeOriginal
    3. File modification time
    
    Returns timezone-aware datetime in local timezone
    """
    # Try to extract from filename first
    filename = os.path.basename(file_path)
    # Pattern: YYYYMMDD_HHMMSS anywhere in filename
    timestamp_pattern = r'(\d{8})_(\d{6})'
    match = re.search(timestamp_pattern, filename)
    
    if match:
        date_str = match.group(1)  # YYYYMMDD
        time_str = match.group(2)  # HHMMSS
        try:
            # Parse as naive datetime, then make timezone-aware
            dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            return ensure_timezone_aware(dt)
        except ValueError:
            logger.warning(f"Failed to parse timestamp from filename: {filename}")
    
    # Try EXIF data
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            if exif:
                # EXIF tag 36867 is DateTimeOriginal
                date_taken = exif.get(36867)
                if date_taken:
                    # EXIF format: "YYYY:MM:DD HH:MM:SS"
                    dt = datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
                    return ensure_timezone_aware(dt)
    except Exception as e:
        logger.debug(f"Could not read EXIF from {file_path}: {e}")
    
    # Fall back to file modification time
    mtime = os.path.getmtime(file_path)
    dt = datetime.fromtimestamp(mtime)
    return ensure_timezone_aware(dt)


def scan_job_files(job_id: int) -> Dict[str, Any]:
    """
    Scan all captures for a job and identify:
    1. Missing files (in DB but not on disk)
    2. Orphaned files (on disk but not in DB)
    
    Args:
        job_id: The ID of the job to scan
        
    Returns:
        Dictionary containing scan results with missing, orphaned, and existing file counts
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get job details
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        
        if not job_row:
            raise ValueError(f"Job {job_id} not found")
        
        job = dict_from_row(job_row)
        abs_capture_path = resolve_capture_path(job['capture_path'])
        
        # Get all captures for this job from database
        cursor.execute("""
            SELECT id, file_path, file_size, captured_at 
            FROM captures 
            WHERE job_id = ? 
            ORDER BY captured_at
        """, (job_id,))
        
        captures = cursor.fetchall()
        
        # Build a set of known file paths (resolved absolute) for quick lookup
        known_files = set()
        missing_files = []
        existing_count = 0
        total_size_recovered = 0
        
        for capture in captures:
            capture_dict = dict_from_row(capture)
            abs_file_path = resolve_capture_path(capture_dict['file_path'])
            known_files.add(abs_file_path)
            
            if not os.path.exists(abs_file_path):
                missing_files.append({
                    'id': capture_dict['id'],
                    'file_path': capture_dict['file_path'],
                    'file_size': capture_dict['file_size'],
                    'captured_at': capture_dict['captured_at']
                })
                total_size_recovered += capture_dict['file_size']
            else:
                existing_count += 1
        
        # Now scan the file system for orphaned files
        orphaned_files = []
        if os.path.exists(abs_capture_path):
            for root, dirs, files in os.walk(abs_capture_path):
                for filename in files:
                    # Only consider image files
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                        full_path = os.path.join(root, filename)
                        
                        # If this file is not in our database, it's orphaned
                        if full_path not in known_files:
                            try:
                                file_size = os.path.getsize(full_path)
                                timestamp = extract_timestamp_from_file(full_path)
                                
                                orphaned_files.append({
                                    'file_path': full_path,
                                    'file_size': file_size,
                                    'captured_at': to_iso(timestamp)
                                })
                            except Exception as e:
                                logger.warning(f"Could not process orphaned file {full_path}: {e}")
        
        result = {
            'job_id': job_id,
            'job_name': job['name'],
            'total_captures': len(captures),
            'missing_files': missing_files,
            'missing_count': len(missing_files),
            'orphaned_files': orphaned_files,
            'orphaned_count': len(orphaned_files),
            'existing_count': existing_count,
            'total_size_recovered': total_size_recovered
        }
        
        logger.info(f"Maintenance scan for job {job_id} ({job['name']}): "
                   f"{existing_count} existing, {len(missing_files)} missing, "
                   f"{len(orphaned_files)} orphaned files")
        
        return result


def cleanup_missing_captures(job_id: int, capture_ids: List[int]) -> Dict[str, Any]:
    """
    Remove database records for missing captures
    
    Args:
        job_id: The ID of the job
        capture_ids: List of capture IDs to remove
        
    Returns:
        Dictionary containing cleanup results
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Verify all capture_ids belong to this job
        placeholders = ','.join('?' * len(capture_ids))
        cursor.execute(f"""
            SELECT COUNT(*) as count 
            FROM captures 
            WHERE id IN ({placeholders}) AND job_id = ?
        """, (*capture_ids, job_id))
        
        count = cursor.fetchone()[0]
        
        if count != len(capture_ids):
            raise ValueError("Some capture IDs do not belong to this job")
        
        # Get the total size before deletion
        cursor.execute(f"""
            SELECT SUM(file_size) as total_size 
            FROM captures 
            WHERE id IN ({placeholders})
        """, capture_ids)
        
        result = cursor.fetchone()
        total_size = result[0] if result[0] else 0
        
        # Delete the captures
        cursor.execute(f"""
            DELETE FROM captures 
            WHERE id IN ({placeholders})
        """, capture_ids)
        
        deleted_count = cursor.rowcount
        
        # Update job statistics
        cursor.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(file_size), 0) as total_size
            FROM captures
            WHERE job_id = ?
        """, (job_id,))
        
        stats = cursor.fetchone()
        new_count = stats[0]
        new_size = stats[1]
        
        cursor.execute("""
            UPDATE jobs 
            SET capture_count = ?, storage_size = ?, updated_at = ?
            WHERE id = ?
        """, (new_count, new_size, 
              to_iso(get_now()), 
              job_id))
        
        logger.info(f"Cleaned up {deleted_count} missing captures for job {job_id}, "
                   f"recovered {total_size} bytes from database")
        
        return {
            'deleted_count': deleted_count,
            'size_recovered': total_size,
            'new_capture_count': new_count,
            'new_storage_size': new_size
        }


def import_orphaned_files(job_id: int, orphaned_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Import orphaned files found on disk into the database
    
    Args:
        job_id: The ID of the job
        orphaned_files: List of orphaned file dictionaries with file_path, file_size, captured_at
        
    Returns:
        Dictionary containing import results
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Verify job exists
        cursor.execute("SELECT id FROM jobs WHERE id = ?", (job_id,))
        if not cursor.fetchone():
            raise ValueError(f"Job {job_id} not found")
        
        imported_count = 0
        total_size = 0
        
        for file_info in orphaned_files:
            try:
                # file_path from scan is already absolute
                abs_fp = file_info['file_path']
                if not os.path.exists(abs_fp):
                    logger.warning(f"Orphaned file no longer exists: {abs_fp}")
                    continue
                
                # Store relative path in DB
                from .import_service import get_captures_path
                rel_fp = make_relative(abs_fp, get_captures_path())
                
                # Insert capture record
                cursor.execute("""
                    INSERT INTO captures (job_id, file_path, file_size, captured_at)
                    VALUES (?, ?, ?, ?)
                """, (job_id, rel_fp, file_info['file_size'], file_info['captured_at']))
                
                imported_count += 1
                total_size += file_info['file_size']
                
            except Exception as e:
                logger.error(f"Failed to import {file_info['file_path']}: {e}")
        
        # Update job statistics
        cursor.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(file_size), 0) as total_size
            FROM captures
            WHERE job_id = ?
        """, (job_id,))
        
        stats = cursor.fetchone()
        new_count = stats[0]
        new_size = stats[1]
        
        cursor.execute("""
            UPDATE jobs 
            SET capture_count = ?, storage_size = ?, updated_at = ?
            WHERE id = ?
        """, (new_count, new_size, to_iso(get_now()), job_id))
        
        logger.info(f"Imported {imported_count} orphaned files for job {job_id}, "
                   f"added {total_size} bytes to database")
        
        return {
            'imported_count': imported_count,
            'total_size_imported': total_size,
            'new_capture_count': new_count,
            'new_storage_size': new_size
        }


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}


def scan_directory(directory: str) -> Dict[str, Any]:
    """Scan a directory for importable image files.
    
    Returns dict with 'files' list and 'count'/'total_size' summary.
    Each file entry has: file_path, file_size, captured_at (ISO string).
    """
    if not os.path.isdir(directory):
        raise ValueError(f"Directory not found: {directory}")
    
    files = []
    for entry in os.scandir(directory):
        if not entry.is_file():
            continue
        ext = os.path.splitext(entry.name)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            continue
        try:
            ts = extract_timestamp_from_file(entry.path)
            files.append({
                'file_path': entry.path,
                'file_size': entry.stat().st_size,
                'captured_at': to_iso(ts),
            })
        except Exception as e:
            logger.warning(f"Skipping {entry.path}: {e}")
    
    files.sort(key=lambda f: f['captured_at'])
    total_size = sum(f['file_size'] for f in files)
    
    return {
        'count': len(files),
        'total_size': total_size,
        'first_capture': files[0]['captured_at'] if files else None,
        'last_capture': files[-1]['captured_at'] if files else None,
        'files': files,
    }

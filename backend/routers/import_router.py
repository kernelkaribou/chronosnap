"""
Import API endpoints — upload, browse, scan, analyze, execute, cleanup.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Form
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import shutil
import logging

from ..services.import_service import (
    create_staging_session, get_staging_dir, cleanup_staging,
    count_active_sessions, MAX_CONCURRENT_SESSIONS,
    validate_path_within, sanitize_filename, detect_file_type,
    extract_archive, analyze_staging, browse_directory,
    execute_image_import, execute_video_import, probe_video,
    get_import_path, check_disk_space,
    set_staging_source, cleanup_import_source,
)
from .. import config
from ..utils import get_now, to_iso

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    path: str

class ExecuteRequest(BaseModel):
    image_job_name: Optional[str] = None
    image_stream_url: Optional[str] = ''
    image_stream_type: Optional[str] = 'rtsp'
    image_interval_seconds: Optional[int] = 60
    videos: Optional[List[Dict[str, Any]]] = None  # [{file_name, name, job_id?}]
    selected_images: Optional[List[str]] = None     # file_paths to include (None = all)
    selected_videos: Optional[List[str]] = None     # file_names to include (None = all)

class ImportPathSettings(BaseModel):
    import_path: str


# ---------------------------------------------------------------------------
# Upload endpoints
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a single file to a new or existing staging session.
    
    Streams to disk to handle large files without memory issues.
    """
    if count_active_sessions() >= MAX_CONCURRENT_SESSIONS:
        raise HTTPException(status_code=429, detail="Too many active import sessions")
    
    # Validate file size from content-length if available
    if not check_disk_space(config.IMPORT_STAGING_DIR, config.MAX_UPLOAD_SIZE):
        raise HTTPException(status_code=507, detail="Insufficient disk space")
    
    session_id = create_staging_session()
    staging_dir = get_staging_dir(session_id)
    raw_dir = os.path.join(staging_dir, 'raw')
    
    # Sanitize filename
    safe_name = sanitize_filename(file.filename or 'upload')
    dest_path = os.path.join(raw_dir, safe_name)
    
    try:
        # Stream to disk in chunks
        total_size = 0
        with open(dest_path, 'wb') as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > config.MAX_UPLOAD_SIZE:
                    os.remove(dest_path)
                    cleanup_staging(session_id)
                    raise HTTPException(status_code=413, detail="File exceeds maximum upload size")
                f.write(chunk)
        
        os.chmod(dest_path, 0o644)
        
        # If it's an archive, extract it
        file_type = detect_file_type(dest_path)
        extraction_result = None
        if file_type == 'archive':
            extracted_dir = os.path.join(staging_dir, 'extracted')
            extraction_result = extract_archive(dest_path, extracted_dir)
            # Remove the archive after extraction
            os.remove(dest_path)
        
        logger.info(f"Upload complete: {safe_name} ({total_size} bytes) -> session {session_id}")
        
        return {
            'session_id': session_id,
            'file_name': safe_name,
            'file_size': total_size,
            'file_type': file_type,
            'extraction': extraction_result,
        }
    except HTTPException:
        raise
    except Exception as e:
        cleanup_staging(session_id)
        logger.error(f"Upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Upload failed")


@router.post("/upload-batch")
async def upload_batch(files: List[UploadFile] = File(...), session_id: Optional[str] = Form(None)):
    """Upload multiple files to a staging session. If session_id is provided, appends to existing session."""
    
    # Create new session or reuse existing
    if session_id:
        try:
            staging_dir = get_staging_dir(session_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        if count_active_sessions() >= MAX_CONCURRENT_SESSIONS:
            raise HTTPException(status_code=429, detail="Too many active import sessions")
        session_id = create_staging_session()
        staging_dir = get_staging_dir(session_id)
    
    if not check_disk_space(config.IMPORT_STAGING_DIR, config.MAX_UPLOAD_SIZE):
        raise HTTPException(status_code=507, detail="Insufficient disk space")
    
    raw_dir = os.path.join(staging_dir, 'raw')
    
    uploaded = []
    total_size = 0
    archives_to_extract = []
    
    try:
        for file in files:
            safe_name = sanitize_filename(file.filename or 'upload')
            dest_path = os.path.join(raw_dir, safe_name)
            
            # Handle name collision
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(safe_name)
                counter = 1
                while os.path.exists(dest_path):
                    dest_path = os.path.join(raw_dir, f"{base}_{counter}{ext}")
                    counter += 1
            
            file_size = 0
            with open(dest_path, 'wb') as f:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    total_size += len(chunk)
                    if total_size > config.MAX_UPLOAD_SIZE:
                        cleanup_staging(session_id)
                        raise HTTPException(status_code=413, detail="Total upload exceeds maximum size")
                    f.write(chunk)
            
            os.chmod(dest_path, 0o644)
            file_type = detect_file_type(dest_path)
            
            if file_type == 'archive':
                archives_to_extract.append(dest_path)
            
            uploaded.append({
                'file_name': os.path.basename(dest_path),
                'file_size': file_size,
                'file_type': file_type,
            })
        
        # Extract archives
        for archive_path in archives_to_extract:
            extracted_dir = os.path.join(staging_dir, 'extracted')
            extract_archive(archive_path, extracted_dir)
            os.remove(archive_path)
        
        logger.info(f"Batch upload: {len(uploaded)} files ({total_size} bytes) -> session {session_id}")
        
        return {
            'session_id': session_id,
            'files': uploaded,
            'total_size': total_size,
            'file_count': len(uploaded),
        }
    except HTTPException:
        raise
    except Exception as e:
        cleanup_staging(session_id)
        logger.error(f"Batch upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Batch upload failed")


# ---------------------------------------------------------------------------
# Browse endpoint
# ---------------------------------------------------------------------------

@router.get("/browse")
async def browse_import_directory(path: str = Query(default="")):
    """Browse files/folders within the configured import path.
    
    Security: strictly jailed to import_path, canonicalized, no file content served.
    """
    try:
        import_path = get_import_path()
        browse_path = path if path else import_path
        
        entries = browse_directory(browse_path)
        
        return {
            'path': browse_path,
            'import_root': import_path,
            'entries': entries,
            'count': len(entries),
        }
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Browse error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to browse directory")


# ---------------------------------------------------------------------------
# Server-path scan
# ---------------------------------------------------------------------------

@router.post("/scan")
async def scan_import_path(request: ScanRequest):
    """Scan a server-side path, copy files to staging, extract archives.
    
    The path must be within the configured import directory.
    """
    if count_active_sessions() >= MAX_CONCURRENT_SESSIONS:
        raise HTTPException(status_code=429, detail="Too many active import sessions")
    
    import_path = get_import_path()
    
    try:
        real_path = validate_path_within(request.path, import_path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="Path not found")
    
    session_id = create_staging_session()
    staging_dir = get_staging_dir(session_id)
    raw_dir = os.path.join(staging_dir, 'raw')
    extracted_dir = os.path.join(staging_dir, 'extracted')
    
    try:
        if os.path.isfile(real_path):
            # Single file — copy to raw
            safe_name = sanitize_filename(os.path.basename(real_path))
            dest = os.path.join(raw_dir, safe_name)
            shutil.copy2(real_path, dest)
            os.chmod(dest, 0o644)
            
            if detect_file_type(dest) == 'archive':
                extract_archive(dest, extracted_dir)
                os.remove(dest)
        
        elif os.path.isdir(real_path):
            # Directory — recursively copy all files to raw
            for root, dirs, files in os.walk(real_path):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for fname in files:
                    if fname.startswith('.'):
                        continue
                    
                    src = os.path.join(root, fname)
                    
                    # Validate file is within import path (prevents symlink escape)
                    try:
                        validate_path_within(src, import_path)
                    except ValueError:
                        logger.warning(f"Scan: skipping file escaping jail: {fname}")
                        continue
                    
                    safe_name = sanitize_filename(fname)
                    dest = os.path.join(raw_dir, safe_name)
                    
                    # Handle name collision
                    if os.path.exists(dest):
                        base, ext = os.path.splitext(safe_name)
                        counter = 1
                        while os.path.exists(dest):
                            dest = os.path.join(raw_dir, f"{base}_{counter}{ext}")
                            counter += 1
                    
                    shutil.copy2(src, dest)
                    os.chmod(dest, 0o644)
                    
                    if detect_file_type(dest) == 'archive':
                        extract_archive(dest, extracted_dir)
                        os.remove(dest)
        
        logger.info(f"Scan complete for {real_path} -> session {session_id}")
        
        # Record source path for post-import cleanup
        set_staging_source(session_id, request.path)
        
        return {
            'session_id': session_id,
            'source_path': request.path,
        }
    except HTTPException:
        raise
    except Exception as e:
        cleanup_staging(session_id)
        logger.error(f"Scan error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to scan path")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@router.get("/{session_id}/analyze")
async def analyze_import(session_id: str):
    """Analyze staged files — classify images, videos, detect duplicates."""
    try:
        result = analyze_staging(session_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analysis failed")


# ---------------------------------------------------------------------------
# Execute import
# ---------------------------------------------------------------------------

@router.post("/{session_id}/execute")
async def execute_import(session_id: str, request: ExecuteRequest):
    """Execute the import — move files to final locations, create DB records."""
    try:
        # Re-analyze to get current file list
        analysis = analyze_staging(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    results = {
        'images': None,
        'videos': [],
        'skipped_duplicates': [],
        'errors': [],
    }
    
    try:
        # --- Import images ---
        images = analysis['images']
        if request.selected_images is not None:
            selected_set = set(request.selected_images)
            images = [i for i in images if i['file_path'] in selected_set]
        
        if images and request.image_job_name:
            try:
                img_result = execute_image_import(
                    session_id=session_id,
                    images=images,
                    job_name=request.image_job_name,
                    stream_url=request.image_stream_url or '',
                    stream_type=request.image_stream_type or 'rtsp',
                    interval_seconds=request.image_interval_seconds or 60,
                )
                results['images'] = img_result
            except Exception as e:
                results['errors'].append(f"Image import failed: {e}")
                logger.error(f"Image import failed: {e}", exc_info=True)
        
        # --- Import videos ---
        videos_to_import = analysis['videos']
        if request.selected_videos is not None:
            selected_set = set(request.selected_videos)
            videos_to_import = [v for v in videos_to_import if v['file_name'] in selected_set]
        
        # Block duplicates from being imported
        video_duplicates = analysis.get('video_duplicates', {})
        skipped_dupes = []
        if video_duplicates:
            filtered = []
            for v in videos_to_import:
                if v['file_name'] in video_duplicates:
                    dupe_info = video_duplicates[v['file_name']]
                    skipped_dupes.append(f"'{v['file_name']}' (matches existing '{dupe_info['existing_name']}')")
                    logger.info(f"Skipping duplicate video: {v['file_name']} -> {dupe_info}")
                else:
                    filtered.append(v)
            videos_to_import = filtered
        
        # Build video config lookup
        video_configs = {}
        if request.videos:
            for vc in request.videos:
                video_configs[vc['file_name']] = vc
        
        for video in videos_to_import:
            vc = video_configs.get(video['file_name'], {})
            video_name = vc.get('name', os.path.splitext(video['file_name'])[0])
            linked_job_id = vc.get('job_id')
            
            try:
                vid_result = execute_video_import(
                    session_id=session_id,
                    video=video,
                    video_name=video_name,
                    job_id=linked_job_id,
                )
                results['videos'].append(vid_result)
            except Exception as e:
                results['errors'].append(f"Video '{video['file_name']}' import failed: {e}")
                logger.error(f"Video import failed for {video['file_name']}: {e}", exc_info=True)
        
        results['skipped_duplicates'] = skipped_dupes
        
        # Clean up source files from /imports
        cleanup_import_source(session_id)
        
        # Clean up staging
        cleanup_staging(session_id)
        
        logger.info(f"Import execution complete for session {session_id}")
        return results
    
    except Exception as e:
        logger.error(f"Execute import error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import execution failed: {e}")


# ---------------------------------------------------------------------------
# Progress (simple polling)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/progress")
async def import_progress(session_id: str):
    """Check import session status. For now returns basic staging info."""
    try:
        staging_dir = get_staging_dir(session_id)
        
        # Count files in staging
        file_count = 0
        total_size = 0
        for root, dirs, files in os.walk(staging_dir):
            for f in files:
                fp = os.path.join(root, f)
                file_count += 1
                total_size += os.path.getsize(fp)
        
        return {
            'session_id': session_id,
            'status': 'staged',
            'file_count': file_count,
            'total_size': total_size,
        }
    except ValueError:
        return {
            'session_id': session_id,
            'status': 'completed',
            'file_count': 0,
            'total_size': 0,
        }


@router.get("/{session_id}/thumbnail/{file_name}")
async def get_staging_thumbnail(session_id: str, file_name: str):
    """Serve a staging video thumbnail."""
    try:
        staging_dir = get_staging_dir(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Sanitize file_name to prevent path traversal
    safe_name = sanitize_filename(file_name)
    thumb_name = os.path.splitext(safe_name)[0] + '_thumb.jpg'
    thumb_path = os.path.join(staging_dir, thumb_name)
    
    if not os.path.isfile(thumb_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    return FileResponse(thumb_path, media_type='image/jpeg')


# ---------------------------------------------------------------------------
# Cancel / cleanup
# ---------------------------------------------------------------------------

@router.delete("/{session_id}")
async def cancel_import(session_id: str):
    """Cancel an import and clean up staging files."""
    try:
        cleanup_staging(session_id)
        return {'status': 'cleaned', 'session_id': session_id}
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        raise HTTPException(status_code=500, detail="Cleanup failed")


# ---------------------------------------------------------------------------
# Import path settings
# ---------------------------------------------------------------------------

@router.get("/settings/path")
async def get_import_path_setting():
    """Get the configured import path."""
    path = get_import_path()
    return {'import_path': path}


@router.put("/settings/path")
async def update_import_path_setting(settings: ImportPathSettings):
    """Update the import path setting."""
    from ..database import get_db
    
    # Validate the path exists and is a directory
    path = settings.import_path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Import path cannot be empty")
    
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {path}")
    
    now = to_iso(get_now())
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
                ('import_path', path, now, path, now)
            )
        logger.info(f"Import path updated to: {path}")
        return {'import_path': path}
    except Exception as e:
        logger.error(f"Error updating import path: {e}")
        raise HTTPException(status_code=500, detail="Failed to update import path")

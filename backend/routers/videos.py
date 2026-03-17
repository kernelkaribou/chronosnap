"""
Processed videos API endpoints
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import logging

from ..models import VideoCreate, VideoResponse
from ..database import get_db, dict_from_row
from ..services.video_processor import process_video, cancel_video
from ..utils import get_now, to_iso
from ..helpers.db_helpers import get_or_404, normalize_favorite, fetch_tags_for_videos, fetch_tags_for_jobs, set_video_tags
from ..helpers.file_helpers import validate_writable_directory, delete_video_files, resolve_video_path, make_relative

router = APIRouter()
logger = logging.getLogger(__name__)


def fetch_share_tokens(cursor, video_ids):
    """Batch-fetch active shared link tokens for a list of video IDs. Returns {video_id: token}."""
    if not video_ids:
        return {}
    placeholders = ','.join('?' * len(video_ids))
    cursor.execute(
        f"SELECT video_id, token FROM shared_links WHERE video_id IN ({placeholders})",
        video_ids
    )
    return {row['video_id']: row['token'] for row in cursor.fetchall()}


@router.post("/", response_model=VideoResponse, status_code=201)
async def create_video(video: VideoCreate, background_tasks: BackgroundTasks):
    """Create a new processed video from captures"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Verify job exists
        job_dict = get_or_404(cursor,
            "SELECT * FROM jobs WHERE id = ?",
            (video.job_id,), "Job not found")
        
        # Always use the global timelapses path from settings
        from ..services.import_service import get_timelapses_path
        videos_path = get_timelapses_path()
        
        # Create job subfolder: {job_id}_{sanitized_job_name}
        import re
        sanitized_name = re.sub(r'[^\w\s-]', '', job_dict['name']).strip()
        job_folder = f"{video.job_id}_{sanitized_name}"
        job_dir = os.path.join(videos_path, job_folder)
        os.makedirs(job_dir, exist_ok=True)
        
        # Create video record - name already includes timestamp from frontend
        now = to_iso(get_now())
        output_path = os.path.join(job_dir, f"{video.name}.mp4")
        rel_output = make_relative(output_path, videos_path)
        
        cursor.execute("""
            INSERT INTO processed_videos (
                job_id, job_name, name, file_path, file_size, resolution,
                framerate, quality, start_capture_id, end_capture_id,
                start_time, end_time, total_frames, duration_seconds, status,
                text_overlay, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'processing', ?, ?)
        """, (
            video.job_id, job_dict['name'], video.name, rel_output, video.resolution,
            video.framerate, video.quality, video.start_capture_id,
            video.end_capture_id, video.start_time, video.end_time,
            video.text_overlay.model_dump_json() if video.text_overlay else None,
            now
        ))
        
        video_id = cursor.lastrowid
        
        # Set tags if provided
        if video.tag_ids:
            set_video_tags(cursor, video_id, video.tag_ids)
        
        logger.info(f"Started video processing for job '{job_dict['name']}' (ID: {video.job_id}) - Video: {video.name}, Resolution: {video.resolution}, FPS: {video.framerate}")
        
        # Start video processing in background
        text_overlay_dict = video.text_overlay.model_dump() if video.text_overlay else None
        background_tasks.add_task(
            process_video,
            video_id=video_id,
            job_dict=job_dict,
            resolution=video.resolution,
            framerate=video.framerate,
            quality=video.quality,
            start_capture_id=video.start_capture_id,
            end_capture_id=video.end_capture_id,
            start_time=video.start_time,
            end_time=video.end_time,
            output_path=output_path,
            text_overlay=text_overlay_dict
        )
        
        cursor.execute("SELECT * FROM processed_videos WHERE id = ?", (video_id,))
        return dict_from_row(cursor.fetchone())


@router.get("/fonts")
async def list_fonts():
    """List available fonts for text overlay"""
    from ..services.text_overlay import get_available_fonts
    return get_available_fonts()


class TextOverlayPreviewRequest(BaseModel):
    image_path: Optional[str] = None
    image_data: Optional[str] = None  # Base64-encoded image (from test-url)
    config: dict
    job_name: str = "Sample Job"


@router.post("/text-overlay-preview")
async def text_overlay_preview(request: TextOverlayPreviewRequest):
    """Generate a preview image with text overlay applied"""
    from ..services.text_overlay import render_preview_bytes

    if not request.image_path and not request.image_data:
        raise HTTPException(status_code=400, detail="Either image_path or image_data required")

    if request.image_path and not os.path.isfile(request.image_path):
        raise HTTPException(status_code=404, detail="Image not found")

    # Build sample variables for preview
    from ..utils import get_now
    now = get_now()
    variables = {
        'job_name': request.job_name,
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'datetime': now.strftime('%Y-%m-%d %H:%M:%S'),
        'frame': '1',
        'total_frames': '100',
    }

    try:
        preview_bytes = render_preview_bytes(
            image_path=request.image_path,
            image_data=request.image_data,
            config=request.config,
            variables=variables,
        )
        return Response(content=preview_bytes, media_type="image/jpeg")
    except Exception as e:
        logger.error(f"Text overlay preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {str(e)}")


@router.get("/", response_model=List[VideoResponse])
async def list_videos(
    job_id: Optional[int] = Query(None, description="Filter by job ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """List all processed videos with optional filtering"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = """
            SELECT v.*, COALESCE(v.job_name, j.name) as job_name
            FROM processed_videos v
            LEFT JOIN jobs j ON v.job_id = j.id
            WHERE 1=1
        """
        params = []
        
        if job_id is not None:
            query += " AND v.job_id = ?"
            params.append(job_id)
        
        if status is not None:
            query += " AND v.status = ?"
            params.append(status)
        
        if favorites_only:
            query += " AND v.is_favorite = 1"
        
        query += " ORDER BY v.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        video_ids = [row['id'] for row in rows]
        
        # Batch-fetch video-specific tags
        video_tags_map = fetch_tags_for_videos(cursor, video_ids)
        
        # Batch-fetch inherited job tags
        job_ids = list(set(row['job_id'] for row in rows if row['job_id']))
        job_tags_map = fetch_tags_for_jobs(cursor, job_ids) if job_ids else {}
        
        # Batch-fetch share tokens
        share_map = fetch_share_tokens(cursor, video_ids)
        
        videos = []
        for row in rows:
            video_dict = dict_from_row(row)
            normalize_favorite(video_dict)
            # Merge own tags + inherited job tags (deduplicate by id)
            own_tags = video_tags_map.get(video_dict['id'], [])
            inherited_tags = job_tags_map.get(video_dict.get('job_id'), [])
            seen_ids = set()
            merged = []
            for t in own_tags + inherited_tags:
                if t['id'] not in seen_ids:
                    seen_ids.add(t['id'])
                    merged.append(t)
            video_dict['tags'] = merged
            video_dict['share_token'] = share_map.get(video_dict['id'])
            videos.append(video_dict)
        return videos


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(video_id: int):
    """Get a specific video by ID"""
    with get_db() as conn:
        cursor = conn.cursor()
        video_dict = get_or_404(cursor, """
            SELECT v.*, COALESCE(v.job_name, j.name) as job_name
            FROM processed_videos v
            LEFT JOIN jobs j ON v.job_id = j.id
            WHERE v.id = ?
        """, (video_id,), "Video not found")
        normalize_favorite(video_dict)
        # Merge own tags + inherited job tags
        own_tags = fetch_tags_for_videos(cursor, [video_id]).get(video_id, [])
        inherited_tags = fetch_tags_for_jobs(cursor, [video_dict['job_id']]).get(video_dict.get('job_id'), []) if video_dict.get('job_id') else []
        seen_ids = set()
        merged = []
        for t in own_tags + inherited_tags:
            if t['id'] not in seen_ids:
                seen_ids.add(t['id'])
                merged.append(t)
        video_dict['tags'] = merged
        video_dict['share_token'] = fetch_share_tokens(cursor, [video_id]).get(video_id)
        return video_dict


@router.get("/{video_id}/check")
async def check_video_file(video_id: int):
    """Check if video file exists and is accessible"""
    with get_db() as conn:
        cursor = conn.cursor()
        vid = get_or_404(cursor,
            "SELECT file_path, status FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        
        if vid['status'] != "completed":
            return {"accessible": False, "reason": "Video is still processing"}
        
        abs_path = resolve_video_path(vid['file_path'])
        if not os.path.exists(abs_path):
            return {"accessible": False, "reason": "Video file not found on disk"}
        
        if not os.access(abs_path, os.R_OK):
            return {"accessible": False, "reason": "No read permission for video file"}
        
        return {"accessible": True, "reason": None}


@router.get("/{video_id}/thumbnail")
async def get_video_thumbnail(video_id: int):
    """Get the thumbnail image for a video"""
    with get_db() as conn:
        cursor = conn.cursor()
        vid = get_or_404(cursor,
            "SELECT thumbnail_path, status FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        
        if not vid['thumbnail_path']:
            raise HTTPException(status_code=404, detail="Thumbnail not available")
        abs_thumb = resolve_video_path(vid['thumbnail_path'])
        if not os.path.exists(abs_thumb):
            raise HTTPException(status_code=404, detail="Thumbnail not available")
        
        return FileResponse(abs_thumb, media_type="image/jpeg")


@router.get("/{video_id}/download")
async def download_video(video_id: int):
    """Download a processed video file"""
    with get_db() as conn:
        cursor = conn.cursor()
        vid = get_or_404(cursor,
            "SELECT file_path, name, status FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        
        if vid['status'] != "completed":
            raise HTTPException(status_code=400, detail="Video is not ready for download")
        
        abs_path = resolve_video_path(vid['file_path'])
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="Video file not found on disk")
        
        return FileResponse(
            abs_path,
            media_type="video/mp4",
            filename=f"{vid['name']}.mp4"
        )


@router.post("/{video_id}/cancel")
async def cancel_video_build(video_id: int):
    """Cancel an in-progress video build"""
    with get_db() as conn:
        cursor = conn.cursor()
        vid = get_or_404(cursor,
            "SELECT id, status FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        if vid['status'] != 'processing':
            raise HTTPException(status_code=400, detail="Video is not currently processing")
    
    if cancel_video(video_id):
        return {"status": "cancelled"}
    raise HTTPException(status_code=400, detail="No active build process found")


@router.delete("/{video_id}", status_code=204)
async def delete_video(video_id: int):
    """Delete a processed video"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        vid = get_or_404(cursor,
            "SELECT name, file_path, thumbnail_path FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        
        # Delete files
        abs_fp = resolve_video_path(vid['file_path'])
        abs_thumb = resolve_video_path(vid['thumbnail_path']) if vid.get('thumbnail_path') else None
        delete_video_files(abs_fp, abs_thumb)
        
        # Clean up empty parent folder
        _cleanup_empty_folder(abs_fp)
        
        # Delete record
        cursor.execute("DELETE FROM processed_videos WHERE id = ?", (video_id,))
        
        logger.info(f"Deleted video '{vid['name']}' (ID: {video_id})")


class BulkDeleteRequest(BaseModel):
    video_ids: List[int]


@router.post("/delete-multiple")
async def delete_multiple_videos(request: BulkDeleteRequest):
    """Delete multiple processed videos"""
    deleted = 0
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        for video_id in request.video_ids:
            from ..helpers.db_helpers import fetch_one
            vid = fetch_one(cursor,
                "SELECT name, file_path, thumbnail_path FROM processed_videos WHERE id = ?",
                (video_id,))
            if not vid:
                continue
            
            delete_video_files(
                resolve_video_path(vid['file_path']),
                resolve_video_path(vid['thumbnail_path']) if vid.get('thumbnail_path') else None
            )
            _cleanup_empty_folder(resolve_video_path(vid['file_path']))
            
            cursor.execute("DELETE FROM processed_videos WHERE id = ?", (video_id,))
            deleted += 1
            logger.info(f"Deleted video '{vid['name']}' (ID: {video_id})")
    
    return {"deleted": deleted, "requested": len(request.video_ids)}


class BulkFavoriteRequest(BaseModel):
    ids: List[int]
    is_favorite: bool = True


@router.post("/favorite")
async def set_video_favorites(request: BulkFavoriteRequest):
    """Set or unset favorite status on multiple videos"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="No video IDs provided")
    
    value = 1 if request.is_favorite else 0
    placeholders = ','.join('?' * len(request.ids))
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE processed_videos SET is_favorite = ? WHERE id IN ({placeholders})",
            [value] + request.ids
        )
        updated = cursor.rowcount
    
    return {"updated": updated, "requested": len(request.ids)}


class VideoTagsRequest(BaseModel):
    tag_ids: List[int]


@router.put("/{video_id}/tags")
async def update_video_tags(video_id: int, request: VideoTagsRequest):
    """Set tags on a video"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM processed_videos WHERE id = ?", (video_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")
        if request.tag_ids:
            placeholders = ','.join('?' * len(request.tag_ids))
            cursor.execute(f"SELECT id FROM tags WHERE id IN ({placeholders})", request.tag_ids)
            valid_ids = {row[0] for row in cursor.fetchall()}
            invalid = [tid for tid in request.tag_ids if tid not in valid_ids]
            if invalid:
                raise HTTPException(status_code=400, detail=f"Invalid tag IDs: {invalid}")
        set_video_tags(cursor, video_id, request.tag_ids)
        tags = fetch_tags_for_videos(cursor, [video_id]).get(video_id, [])
        return {"tags": tags}


def _cleanup_empty_folder(file_path: str):
    """Remove parent folder if empty after file deletion"""
    folder = os.path.dirname(file_path)
    try:
        if folder and os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
            logger.info(f"Removed empty folder: {folder}")
    except OSError:
        pass

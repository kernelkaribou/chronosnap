"""
Processed videos API endpoints
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import logging

from ..models import VideoCreate, VideoResponse
from ..database import get_db, dict_from_row
from ..services.video_processor import process_video, cancel_video
from ..utils import get_now, to_iso
from ..helpers.db_helpers import get_or_404, normalize_favorite, fetch_tags_for_videos, fetch_tags_for_jobs, set_video_tags
from ..helpers.template_vars import build_datetime_vars
from ..helpers.file_helpers import delete_video_files, resolve_video_path, make_relative, cleanup_empty_parents
from ..services.event_service import add_event

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
        
        import re
        sanitized_job = re.sub(r'[^\w\s-]', '', job_dict['name']).strip()
        sanitized_video = re.sub(r'[^\w\s-]', '', video.name).strip()
        if not sanitized_video:
            raise HTTPException(status_code=400, detail="Video name contains only invalid characters")
        
        # Insert with placeholder path to get the video ID
        now = to_iso(get_now())
        cursor.execute("""
            INSERT INTO processed_videos (
                job_id, job_name, name, file_path, file_size, resolution,
                framerate, quality, start_capture_id, end_capture_id,
                start_time, end_time, total_frames, duration_seconds, status,
                text_overlay, created_at
            ) VALUES (?, ?, ?, '', 0, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'processing', ?, ?)
        """, (
            video.job_id, job_dict['name'], video.name, video.resolution,
            video.framerate, video.quality, video.start_capture_id,
            video.end_capture_id, video.start_time, video.end_time,
            video.text_overlay.model_dump_json() if video.text_overlay else None,
            now
        ))
        
        video_id = cursor.lastrowid
        
        # Create folder structure: {job_id}_{job_name}/{video_id}_{video_name}/
        job_folder = f"{video.job_id}_{sanitized_job}"
        video_folder = f"{video_id}_{sanitized_video}"
        video_dir = os.path.join(videos_path, job_folder, video_folder)
        os.makedirs(video_dir, exist_ok=True)
        
        output_path = os.path.join(video_dir, f"{sanitized_video}.mp4")
        rel_output = make_relative(output_path, videos_path)
        cursor.execute(
            "UPDATE processed_videos SET file_path = ? WHERE id = ?",
            (rel_output, video_id)
        )
        
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
    capture_id: Optional[int] = None
    image_data: Optional[str] = None  # Base64-encoded image (from test-url/preview)
    config: dict
    job_name: str = "Sample Job"


@router.post("/text-overlay-preview")
async def text_overlay_preview(request: TextOverlayPreviewRequest):
    """Generate a preview image with text overlay applied"""
    from ..services.text_overlay import render_preview_bytes
    from ..helpers.file_helpers import resolve_capture_path

    if not request.capture_id and not request.image_data:
        raise HTTPException(status_code=400, detail="Either capture_id or image_data required")

    resolved_path = None
    if request.capture_id:
        with get_db() as conn:
            row = conn.execute("SELECT file_path FROM captures WHERE id = ?", (request.capture_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Capture not found")
        resolved_path = resolve_capture_path(row[0])
        if not os.path.isfile(resolved_path):
            raise HTTPException(status_code=404, detail="Capture image file not found on disk")

    # Build sample variables for preview
    from ..utils import get_now
    now = get_now()
    variables = {
        'job_name': request.job_name,
        'frame': '1',
        'total_frames': '100',
        **build_datetime_vars(now),
    }

    try:
        preview_bytes = render_preview_bytes(
            image_path=resolved_path,
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


@router.get("/download-multiple")
async def download_multiple_videos(ids: str = Query(..., description="Comma-separated video IDs")):
    """Download one or more videos. Single video returns mp4, multiple returns a streamed zip."""
    try:
        video_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid video ID format")

    if not video_ids:
        raise HTTPException(status_code=400, detail="No video IDs provided")

    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in video_ids)
        cursor.execute(
            f"SELECT id, file_path, name, status FROM processed_videos WHERE id IN ({placeholders})",
            video_ids
        )
        videos = [dict_from_row(row) for row in cursor.fetchall()]

    completed = [v for v in videos if v["status"] == "completed"]
    if not completed:
        raise HTTPException(status_code=400, detail="No completed videos found for the given IDs")

    ready = []
    for v in completed:
        abs_path = resolve_video_path(v["file_path"])
        if os.path.exists(abs_path):
            ready.append((v["name"], abs_path))

    if not ready:
        raise HTTPException(status_code=404, detail="No video files found on disk")

    if len(ready) == 1:
        name, path = ready[0]
        return FileResponse(path, media_type="video/mp4", filename=f"{name}.mp4")

    import zipstream
    import time

    ts = int(time.time())
    zip_name = f"timelapses_{ts}.zip"

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)
    for name, path in ready:
        zs.add_path(path, f"{name}.mp4")

    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


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


class VideoRenameRequest(BaseModel):
    name: str


@router.patch("/{video_id}", response_model=VideoResponse)
async def rename_video(video_id: int, body: VideoRenameRequest):
    """Rename a video: updates display name, folder, files, and thumbnail on disk and in DB."""
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name must not be empty")
    
    # Sanitize: remove characters unsafe for filenames
    sanitized = "".join(c for c in new_name if c not in r'\/:*?"<>|').strip()
    if not sanitized:
        raise HTTPException(status_code=400, detail="Name contains only invalid characters")
    
    with get_db() as conn:
        cursor = conn.cursor()
        video = get_or_404(cursor,
            "SELECT * FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")
        
        if video['name'] == new_name:
            video_dict = dict_from_row(cursor.execute("""
                SELECT v.*, COALESCE(v.job_name, j.name) as job_name
                FROM processed_videos v LEFT JOIN jobs j ON v.job_id = j.id
                WHERE v.id = ?
            """, (video_id,)).fetchone())
            normalize_favorite(video_dict)
            video_dict['tags'] = fetch_tags_for_videos(cursor, [video_id]).get(video_id, [])
            video_dict['share_token'] = fetch_share_tokens(cursor, [video_id]).get(video_id)
            return video_dict
        
        from ..services.import_service import get_timelapses_path
        videos_path = get_timelapses_path()
        
        old_file_path = video['file_path']
        old_thumb_path = video['thumbnail_path']
        old_ext = os.path.splitext(old_file_path)[1]  # .mp4
        
        # Structure: {job_folder}/{video_id}_{name}/{name}.mp4
        old_video_dir = os.path.dirname(old_file_path)
        job_folder = os.path.dirname(old_video_dir)
        
        new_video_folder = f"{video_id}_{sanitized}"
        new_video_dir = os.path.join(job_folder, new_video_folder)
        new_file_path = os.path.join(new_video_dir, f"{sanitized}{old_ext}")
        new_thumb_path = os.path.join(new_video_dir, f"{sanitized}_thumb.jpg") if old_thumb_path else None
        
        abs_old_dir = os.path.join(videos_path, old_video_dir)
        abs_new_dir = os.path.join(videos_path, new_video_dir)
        
        if abs_old_dir != abs_new_dir and os.path.isdir(abs_old_dir):
            if os.path.exists(abs_new_dir):
                raise HTTPException(status_code=409, detail=f"A folder named '{new_video_folder}' already exists")
            os.rename(abs_old_dir, abs_new_dir)
        elif not os.path.isdir(abs_old_dir):
            os.makedirs(abs_new_dir, exist_ok=True)
        
        # Rename files inside the (now-renamed) folder
        old_filename = os.path.basename(old_file_path)
        new_filename = f"{sanitized}{old_ext}"
        if old_filename != new_filename:
            abs_old_file = os.path.join(abs_new_dir, old_filename)
            abs_new_file = os.path.join(abs_new_dir, new_filename)
            if os.path.exists(abs_old_file):
                os.rename(abs_old_file, abs_new_file)
        
        if old_thumb_path:
            old_thumb_filename = os.path.basename(old_thumb_path)
            new_thumb_filename = f"{sanitized}_thumb.jpg"
            if old_thumb_filename != new_thumb_filename:
                abs_old_thumb = os.path.join(abs_new_dir, old_thumb_filename)
                abs_new_thumb = os.path.join(abs_new_dir, new_thumb_filename)
                if os.path.exists(abs_old_thumb):
                    os.rename(abs_old_thumb, abs_new_thumb)
        
        # Update database
        cursor.execute("""
            UPDATE processed_videos
            SET name = ?, file_path = ?, thumbnail_path = ?
            WHERE id = ?
        """, (new_name, new_file_path, new_thumb_path, video_id))
        
        logger.info(f"Renamed video {video_id}: '{video['name']}' -> '{new_name}'")
        
        video_dict = dict_from_row(cursor.execute("""
            SELECT v.*, COALESCE(v.job_name, j.name) as job_name
            FROM processed_videos v LEFT JOIN jobs j ON v.job_id = j.id
            WHERE v.id = ?
        """, (video_id,)).fetchone())
        normalize_favorite(video_dict)
        video_dict['tags'] = fetch_tags_for_videos(cursor, [video_id]).get(video_id, [])
        video_dict['share_token'] = fetch_share_tokens(cursor, [video_id]).get(video_id)
        return video_dict


class VideoJobLinkRequest(BaseModel):
    job_id: Optional[int] = None


@router.put("/{video_id}/job", response_model=VideoResponse)
async def update_video_job(video_id: int, body: VideoJobLinkRequest):
    """Update the job association for a video. Set job_id to null to unlink."""
    with get_db() as conn:
        cursor = conn.cursor()
        get_or_404(cursor,
            "SELECT id FROM processed_videos WHERE id = ?",
            (video_id,), "Video not found")

        job_name = None
        if body.job_id is not None:
            job = get_or_404(cursor,
                "SELECT id, name FROM jobs WHERE id = ?",
                (body.job_id,), "Job not found")
            job_name = job['name']

        cursor.execute(
            "UPDATE processed_videos SET job_id = ?, job_name = ? WHERE id = ?",
            (body.job_id, job_name, video_id)
        )

        video_dict = dict_from_row(cursor.execute("""
            SELECT v.*, COALESCE(v.job_name, j.name) as job_name
            FROM processed_videos v LEFT JOIN jobs j ON v.job_id = j.id
            WHERE v.id = ?
        """, (video_id,)).fetchone())
        normalize_favorite(video_dict)
        video_dict['tags'] = fetch_tags_for_videos(cursor, [video_id]).get(video_id, [])
        video_dict['share_token'] = fetch_share_tokens(cursor, [video_id]).get(video_id)

        logger.info(f"Updated video {video_id} job link: job_id={body.job_id}")
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
        
        # Clean up empty parent folders
        from ..services.import_service import get_timelapses_path
        cleanup_empty_parents(abs_fp, get_timelapses_path())
        
        # Delete record
        cursor.execute("DELETE FROM processed_videos WHERE id = ?", (video_id,))
        
        add_event(f"Video '{vid['name']}' deleted", "video", {"video_id": video_id})
        logger.info(f"Deleted video '{vid['name']}' (ID: {video_id})")


class BulkDeleteRequest(BaseModel):
    video_ids: List[int]


@router.post("/delete-multiple")
async def delete_multiple_videos(request: BulkDeleteRequest):
    """Delete multiple processed videos"""
    deleted = 0
    deleted_names = []
    
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
            _abs_fp = resolve_video_path(vid['file_path'])
            from ..services.import_service import get_timelapses_path
            cleanup_empty_parents(_abs_fp, get_timelapses_path())
            
            cursor.execute("DELETE FROM processed_videos WHERE id = ?", (video_id,))
            deleted += 1
            deleted_names.append(vid['name'])
            logger.info(f"Deleted video '{vid['name']}' (ID: {video_id})")
    
    if deleted == 1:
        add_event(f"Video '{deleted_names[0]}' deleted", "video")
    elif deleted > 1:
        add_event(f"{deleted} videos deleted", "video")
    
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


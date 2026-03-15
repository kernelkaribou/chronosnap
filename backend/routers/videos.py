"""
Processed videos API endpoints
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from typing import List, Optional
import os
import logging

from ..models import VideoCreate, VideoResponse
from ..database import get_db, dict_from_row
from ..services.video_processor import process_video
from ..utils import get_now, to_iso

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=VideoResponse, status_code=201)
async def create_video(video: VideoCreate, background_tasks: BackgroundTasks):
    """Create a new processed video from captures"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Verify job exists
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (video.job_id,))
        job = cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        job_dict = dict_from_row(job)
        
        # Get video output path from custom path or settings
        if video.output_path:
            videos_path = video.output_path
            
            # Validate custom path
            if not os.path.exists(videos_path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Output path does not exist: {videos_path}"
                )
            
            if not os.path.isdir(videos_path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Output path is not a directory: {videos_path}"
                )
            
            if not os.access(videos_path, os.W_OK):
                raise HTTPException(
                    status_code=400,
                    detail=f"No write permission for output path: {videos_path}"
                )
        else:
            from .. import config
            videos_path = config.DEFAULT_VIDEOS_PATH
        
        # Create video record - name already includes timestamp from frontend
        now = to_iso(get_now())
        output_path = os.path.join(videos_path, f"{video.name}.mp4")
        
        cursor.execute("""
            INSERT INTO processed_videos (
                job_id, name, file_path, file_size, resolution,
                framerate, quality, start_capture_id, end_capture_id,
                start_time, end_time, total_frames, duration_seconds, status, created_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'processing', ?)
        """, (
            video.job_id, video.name, output_path, video.resolution,
            video.framerate, video.quality, video.start_capture_id,
            video.end_capture_id, video.start_time, video.end_time, now
        ))
        
        video_id = cursor.lastrowid
        
        logger.info(f"Started video processing for job '{job_dict['name']}' (ID: {video.job_id}) - Video: {video.name}, Resolution: {video.resolution}, FPS: {video.framerate}")
        
        # Start video processing in background
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
            output_path=output_path
        )
        
        cursor.execute("SELECT * FROM processed_videos WHERE id = ?", (video_id,))
        return dict_from_row(cursor.fetchone())


@router.get("/", response_model=List[VideoResponse])
async def list_videos(
    job_id: Optional[int] = Query(None, description="Filter by job ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """List all processed videos with optional filtering"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = """
            SELECT v.*, j.name as job_name
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
        
        query += " ORDER BY v.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        return [dict_from_row(row) for row in cursor.fetchall()]


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(video_id: int):
    """Get a specific video by ID"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT v.*, j.name as job_name
            FROM processed_videos v
            LEFT JOIN jobs j ON v.job_id = j.id
            WHERE v.id = ?
        """, (video_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        
        return dict_from_row(row)


@router.get("/{video_id}/check")
async def check_video_file(video_id: int):
    """Check if video file exists and is accessible"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, status FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        
        file_path, status = row
        
        if status != "completed":
            return {"accessible": False, "reason": "Video is still processing"}
        
        if not os.path.exists(file_path):
            return {"accessible": False, "reason": "Video file not found on disk"}
        
        if not os.access(file_path, os.R_OK):
            return {"accessible": False, "reason": "No read permission for video file"}
        
        return {"accessible": True, "reason": None}


@router.get("/{video_id}/download")
async def download_video(video_id: int):
    """Download a processed video file"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, name, status FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        
        file_path, name, status = row
        
        if status != "completed":
            raise HTTPException(status_code=400, detail="Video is not ready for download")
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Video file not found on disk")
        
        return FileResponse(
            file_path,
            media_type="video/mp4",
            filename=f"{name}.mp4"
        )


@router.delete("/{video_id}", status_code=204)
async def delete_video(video_id: int):
    """Delete a processed video"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get video info
        cursor.execute("SELECT name, file_path FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        
        name, file_path = row
        
        # Delete file if it exists
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Delete record
        cursor.execute("DELETE FROM processed_videos WHERE id = ?", (video_id,))
        
        logger.info(f"Deleted video '{name}' (ID: {video_id})")

"""
Video processing service - builds timelapse videos from captured images
"""
import subprocess
import os
import threading
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from ..database import get_db
from .. import config
from .event_service import add_event

logger = logging.getLogger(__name__)

# Track active ffmpeg processes by video_id for cancellation
_active_processes: Dict[int, subprocess.Popen] = {}
_process_lock = threading.Lock()


def process_video(
    video_id: int,
    job_dict: Dict[str, Any],
    resolution: str,
    framerate: int,
    quality: str,
    start_capture_id: Optional[int],
    end_capture_id: Optional[int],
    start_time: Optional[str],
    end_time: Optional[str],
    output_path: str,
    text_overlay: Optional[Dict[str, Any]] = None
):
    """
    Process a timelapse video from captured images
    
    Args:
        video_id: ID of the video record in database
        job_dict: Job configuration
        resolution: Output resolution (e.g., "1920x1080")
        framerate: Output framerate
        quality: Quality setting (low, medium, high, lossless)
        start_capture_id: First capture to include (optional, for backward compatibility)
        end_capture_id: Last capture to include (optional, for backward compatibility)
        start_time: Start timestamp for captures (optional)
        end_time: End timestamp for captures (optional)
        output_path: Path to save the output video
    """
    try:
        logger.info(f"Starting video processing for video_id={video_id}")
        logger.info(f"Time range: start_time={start_time}, end_time={end_time}")
        logger.info(f"ID range: start_capture_id={start_capture_id}, end_capture_id={end_capture_id}")
        
        # Get captures for this job
        with get_db() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM captures WHERE job_id = ?"
            params = [job_dict['id']]
            
            # Prefer time-based filtering over ID-based filtering
            if start_time:
                query += " AND captured_at >= ?"
                params.append(start_time)
            elif start_capture_id:
                query += " AND id >= ?"
                params.append(start_capture_id)
            
            if end_time:
                query += " AND captured_at <= ?"
                params.append(end_time)
            elif end_capture_id:
                query += " AND id <= ?"
                params.append(end_capture_id)
            
            query += " ORDER BY captured_at ASC"
            
            logger.info(f"Query: {query}")
            logger.info(f"Params: {params}")
            
            cursor.execute(query, params)
            captures = cursor.fetchall()
        
        if not captures:
            _update_video_status(video_id, 'failed', 0, "No captures found for processing")
            return
        
        total_frames = len(captures)
        logger.info(f"Processing {total_frames} frames")
        
        # Get first and last capture timestamps
        first_capture_time = captures[0][4]  # captured_at from first capture
        last_capture_time = captures[-1][4]  # captured_at from last capture
        
        # Update video with actual capture range
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE processed_videos
                SET start_time = ?, end_time = ?
                WHERE id = ?
            """, (first_capture_time, last_capture_time, video_id))
        
        # Create a temporary file list for ffmpeg
        import tempfile
        
        overlay_dir = None
        use_overlay = text_overlay and text_overlay.get('enabled') and text_overlay.get('text')
        
        if use_overlay:
            # Process frames with text overlay using PIL
            from .text_overlay import process_frames_with_overlay
            overlay_dir = tempfile.mkdtemp(prefix='overlay_')
            logger.info(f"Applying text overlay to {total_frames} frames...")
            
            def overlay_progress(frame_num, total):
                progress = (frame_num / total) * 40  # Overlay is ~40% of work
                _update_progress(video_id, progress)
            
            overlay_paths = process_frames_with_overlay(
                captures=captures,
                config=text_overlay,
                job_name=job_dict.get('name', ''),
                temp_dir=overlay_dir,
                total_frames=total_frames,
                progress_callback=overlay_progress,
            )
            
            if not overlay_paths:
                _update_video_status(video_id, 'failed', 0, "Text overlay processing produced no frames")
                return
            
            logger.info(f"Text overlay applied to {len(overlay_paths)} frames")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            list_file = f.name
            if use_overlay:
                for path in overlay_paths:
                    f.write(f"file '{path}'\n")
                    f.write(f"duration {1/framerate}\n")
            else:
                from ..helpers.file_helpers import resolve_capture_path
                for capture in captures:
                    abs_fp = resolve_capture_path(capture[2])  # capture[2] is file_path
                    f.write(f"file '{abs_fp}'\n")
                    f.write(f"duration {1/framerate}\n")
        
        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Map quality to CRF values (lower = better quality)
            quality_map = {
                'low': '28',
                'medium': '23',
                'high': '18',
                'lossless': '0'
            }
            crf = quality_map.get(quality, '23')
            
            # Build ffmpeg command
            cmd = [
                'ffmpeg',
                '-loglevel', 'info',
                '-f', 'concat',
                '-safe', '0',
                '-i', list_file,
                '-vf', f'scale={resolution}',
                '-r', str(framerate),
                '-c:v', 'libx264',
                '-crf', crf,
                '-preset', 'medium',
                '-pix_fmt', 'yuv420p',
                '-y',
                output_path
            ]
            
            logger.info(f"Running ffmpeg command: {' '.join(cmd)}")
            
            # Run ffmpeg with progress tracking
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            with _process_lock:
                _active_processes[video_id] = process
            
            try:
                # Monitor progress, collecting stderr for error reporting
                stderr_lines = []
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line)
                    
                    # Parse progress from ffmpeg output
                    if 'frame=' in line:
                        try:
                            frame_str = line.split('frame=')[1].split()[0]
                            current_frame = int(frame_str)
                            if use_overlay:
                                # Overlay used 40%, encoding uses remaining 60%
                                progress = 40 + (current_frame / total_frames) * 60
                            else:
                                progress = (current_frame / total_frames) * 100
                            _update_progress(video_id, progress)
                        except (ValueError, IndexError):
                            pass
                
                process.wait()
            finally:
                with _process_lock:
                    _active_processes.pop(video_id, None)
            
            if process.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                duration = total_frames / framerate
                
                _update_video_completed(
                    video_id=video_id,
                    file_size=file_size,
                    total_frames=total_frames,
                    duration_seconds=duration
                )
                
                logger.info(f"Video processing completed: {output_path}")
            elif process.returncode in (-9, -15):
                # Killed by cancel_video — status already set by cancel_video()
                logger.info(f"Video processing cancelled for video_id={video_id}")
            else:
                error_msg = ''.join(stderr_lines[-20:]) if stderr_lines else "Unknown error"
                _update_video_status(video_id, 'failed', 0, f"FFMPEG error: {error_msg[:200]}")
                logger.error(f"Video processing failed: {error_msg[:500]}")
        
        finally:
            # Clean up temp files
            if os.path.exists(list_file):
                os.remove(list_file)
            if overlay_dir and os.path.isdir(overlay_dir):
                import shutil
                shutil.rmtree(overlay_dir, ignore_errors=True)
    
    except Exception as e:
        logger.error(f"Error processing video {video_id}: {e}")
        _update_video_status(video_id, 'failed', 0, str(e))


def _update_progress(video_id: int, progress: float):
    """Update video processing progress"""
    from .state_manager import update_video_state
    update_video_state(video_id, 'processing', min(progress, 100.0))


def cancel_video(video_id: int) -> bool:
    """Cancel an in-progress video build. Returns True if cancelled."""
    with _process_lock:
        process = _active_processes.get(video_id)
    if process and process.poll() is None:
        process.kill()
        _update_video_status(video_id, 'failed', 0, "Cancelled by user")
        # Clean up partial output file
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM processed_videos WHERE id = ?", (video_id,))
            row = cursor.fetchone()
            if row and row[0] and os.path.exists(row[0]):
                os.remove(row[0])
        logger.info(f"Video build cancelled: video_id={video_id}")
        return True
    return False


def _update_video_status(video_id: int, status: str, progress: float, message: str = ""):
    """Update video status"""
    from .state_manager import update_video_state
    update_video_state(video_id, status, progress, message)
    logger.info(f"Video {video_id} status: {status} - {message}")
    if status == 'failed':
        add_event(f"Video build failed (ID: {video_id})", "video", {"video_id": video_id, "error": message[:100]})


def _update_video_completed(video_id: int, file_size: int, total_frames: int, duration_seconds: float):
    """Mark video as completed with final metadata"""
    from .state_manager import update_video_state
    from ..helpers.file_helpers import resolve_video_path
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, file_path FROM processed_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        video_name, video_path = row[0], resolve_video_path(row[1])
    
    update_video_state(
        video_id,
        'completed',
        progress=100,
        file_size=file_size,
        total_frames=total_frames,
        duration_seconds=duration_seconds
    )
    
    # Generate thumbnail after completion
    generate_thumbnail(video_id, video_path)
    
    logger.info(f"Completed video '{video_name}' (ID: {video_id}) - Frames: {total_frames}, Duration: {duration_seconds:.2f}s, Size: {file_size / (1024*1024):.2f}MB")
    add_event(f"Video '{video_name}' build completed", "video", {"video_id": video_id})


def generate_thumbnail(video_id: int, video_path: str):
    """Extract a thumbnail frame from a completed video.
    video_path must be an absolute filesystem path."""
    if not os.path.exists(video_path):
        logger.warning(f"Cannot generate thumbnail: video file not found at {video_path}")
        return
    
    thumb_path = os.path.splitext(video_path)[0] + "_thumb.jpg"
    
    try:
        cmd = [
            'ffmpeg', '-loglevel', 'error',
            '-i', video_path,
            '-frames:v', '1',
            '-q:v', '2',
            '-y', thumb_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0 and os.path.exists(thumb_path):
            from ..helpers.file_helpers import make_relative
            from .import_service import get_timelapses_path
            rel_thumb = make_relative(thumb_path, get_timelapses_path())
            with get_db() as conn:
                conn.execute(
                    "UPDATE processed_videos SET thumbnail_path = ? WHERE id = ?",
                    (rel_thumb, video_id)
                )
            logger.info(f"Generated thumbnail for video {video_id}: {thumb_path}")
        else:
            logger.warning(f"Thumbnail generation failed for video {video_id}: {result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Thumbnail generation error for video {video_id}: {e}")


def backfill_thumbnails():
    """Generate missing thumbnails for existing completed videos"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, file_path FROM processed_videos
            WHERE status = 'completed' AND (thumbnail_path IS NULL OR thumbnail_path = '')
        """)
        rows = cursor.fetchall()
    
    if not rows:
        return
    
    logger.info(f"Backfilling thumbnails for {len(rows)} videos")
    from ..helpers.file_helpers import resolve_video_path
    for row in rows:
        generate_thumbnail(row[0], resolve_video_path(row[1]))

"""
Video processing service - builds timelapse videos from captured images
"""
import subprocess
import os
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from ..database import get_db
from .. import config

logger = logging.getLogger(__name__)


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
    output_path: str
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
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            list_file = f.name
            for capture in captures:
                # FFMPEG concat demuxer format
                f.write(f"file '{capture[2]}'\n")  # capture[2] is file_path
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
                        progress = (current_frame / total_frames) * 100
                        _update_progress(video_id, progress)
                    except (ValueError, IndexError):
                        pass
            
            process.wait()
            
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
            else:
                error_msg = ''.join(stderr_lines[-20:]) if stderr_lines else "Unknown error"
                _update_video_status(video_id, 'failed', 0, f"FFMPEG error: {error_msg[:200]}")
                logger.error(f"Video processing failed: {error_msg[:500]}")
        
        finally:
            # Clean up temp file
            if os.path.exists(list_file):
                os.remove(list_file)
    
    except Exception as e:
        logger.error(f"Error processing video {video_id}: {e}")
        _update_video_status(video_id, 'failed', 0, str(e))


def _update_progress(video_id: int, progress: float):
    """Update video processing progress"""
    from .state_manager import update_video_state
    update_video_state(video_id, 'processing', min(progress, 100.0))


def _update_video_status(video_id: int, status: str, progress: float, message: str = ""):
    """Update video status"""
    from .state_manager import update_video_state
    update_video_state(video_id, status, progress, message)
    logger.info(f"Video {video_id} status: {status} - {message}")


def _update_video_completed(video_id: int, file_size: int, total_frames: int, duration_seconds: float):
    """Mark video as completed with final metadata"""
    from .state_manager import update_video_state
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM processed_videos WHERE id = ?", (video_id,))
        video_name = cursor.fetchone()[0]
    
    update_video_state(
        video_id,
        'completed',
        progress=100,
        file_size=file_size,
        total_frames=total_frames,
        duration_seconds=duration_seconds
    )
    
    logger.info(f"Completed video '{video_name}' (ID: {video_id}) - Frames: {total_frames}, Duration: {duration_seconds:.2f}s, Size: {file_size / (1024*1024):.2f}MB")

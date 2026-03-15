"""
Universal state management for Jobs and Videos
Now uses the context-aware job_state calculator
"""
from typing import Dict, Optional, Tuple, Literal
from datetime import datetime
import logging

from ..database import get_db
from ..utils import get_now, to_iso, parse_iso
from .job_state import calculate_job_state as calculate_state

logger = logging.getLogger(__name__)

# Valid state transitions
JobStatus = Literal['active', 'sleeping', 'completed', 'disabled']
VideoStatus = Literal['pending', 'processing', 'completed', 'failed']


class StateManager:
    """
    Universal state manager for jobs and videos.
    Provides convenience wrappers around core calculators.
    """
    
    @staticmethod
    def calculate_job_state(job: dict, reference_time: Optional[datetime] = None) -> Tuple[JobStatus, Optional[datetime], Optional[str]]:
        """
        Calculate the correct state for a job based on current conditions.
        
        Returns:
            (status, next_capture_time, reason)
        """
        if reference_time is None:
            reference_time = get_now()
        
        # Get pending capture if job has one scheduled
        pending = parse_iso(job['next_scheduled_capture_at']) if job.get('next_scheduled_capture_at') else None
        
        # Use core calculator with context awareness
        return calculate_state(job, reference_time, pending)
    
    @staticmethod
    def update_job_state(job_id: int, force_status: Optional[JobStatus] = None) -> Dict:
        """
        Update a job's state in the database based on current conditions.
        
        Args:
            job_id: ID of the job to update
            force_status: If provided, force this status (for manual completion/disable)
        
        Returns:
            Updated job dict with new status and next_scheduled_capture_at
        """
        now = get_now()
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get current job
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Job {job_id} not found")
            
            from ..database import dict_from_row
            job = dict_from_row(row)
            
            # If forcing a status (manual action), apply it
            if force_status:
                if force_status == 'disabled':
                    cursor.execute(
                        "UPDATE jobs SET status = ?, next_scheduled_capture_at = NULL, updated_at = ? WHERE id = ?",
                        ('disabled', to_iso(now), job_id)
                    )
                    logger.info(f"Job {job_id} manually disabled")
                elif force_status == 'completed':
                    cursor.execute(
                        "UPDATE jobs SET status = ?, next_scheduled_capture_at = NULL, updated_at = ? WHERE id = ?",
                        ('completed', to_iso(now), job_id)
                    )
                    logger.info(f"Job {job_id} manually completed")
                elif force_status == 'active':
                    # Re-enable job - calculate new state
                    new_status, next_capture, reason = StateManager.calculate_job_state(job, now)
                    cursor.execute(
                        "UPDATE jobs SET status = ?, next_scheduled_capture_at = ?, updated_at = ? WHERE id = ?",
                        (new_status, to_iso(next_capture) if next_capture else None, to_iso(now), job_id)
                    )
                    logger.info(f"Job {job_id} re-enabled: {new_status} - {reason}")
                
                job['status'] = force_status
                return job
            
            # Calculate correct state
            new_status, next_capture, reason = StateManager.calculate_job_state(job, now)
            
            # Only update if state changed
            if new_status != job['status'] or (next_capture and to_iso(next_capture) != job.get('next_scheduled_capture_at')):
                cursor.execute(
                    "UPDATE jobs SET status = ?, next_scheduled_capture_at = ?, warning_message = NULL, updated_at = ? WHERE id = ?",
                    (new_status, to_iso(next_capture) if next_capture else None, to_iso(now), job_id)
                )
                logger.info(f"Job {job_id} state updated: {job['status']} -> {new_status} - {reason}")
                job['status'] = new_status
                job['next_scheduled_capture_at'] = to_iso(next_capture) if next_capture else None
            
            return job
    
    @staticmethod
    def update_video_state(video_id: int, status: VideoStatus, progress: float = 0, message: str = "", 
                          file_size: Optional[int] = None, duration_seconds: Optional[float] = None,
                          total_frames: Optional[int] = None) -> None:
        """
        Update a video's processing state in the database.
        
        Args:
            video_id: ID of the video
            status: New status
            progress: Progress percentage (0-100)
            message: Status message or error details
            file_size: Final file size in bytes (for completed videos)
            duration_seconds: Video duration (for completed videos)
            total_frames: Total frames processed (for completed videos)
        """
        with get_db() as conn:
            cursor = conn.cursor()
            
            updates = ["status = ?", "progress = ?"]
            values = [status, progress]
            
            if message:
                updates.append("error_message = ?")
                values.append(message)
            
            if file_size is not None:
                updates.append("file_size = ?")
                values.append(file_size)
            
            if duration_seconds is not None:
                updates.append("duration_seconds = ?")
                values.append(duration_seconds)
            
            if total_frames is not None:
                updates.append("total_frames = ?")
                values.append(total_frames)
            
            if status == 'completed':
                updates.append("completed_at = ?")
                values.append(to_iso(get_now()))
            
            query = f"UPDATE processed_videos SET {', '.join(updates)} WHERE id = ?"
            values.append(video_id)
            
            cursor.execute(query, values)
            logger.info(f"Video {video_id} state updated: {status} (progress: {progress}%)")


# Singleton-like access
def update_job_state(job_id: int, force_status: Optional[JobStatus] = None) -> Dict:
    """Update job state - convenience wrapper"""
    return StateManager.update_job_state(job_id, force_status)


def update_video_state(video_id: int, status: VideoStatus, progress: float = 0, 
                      message: str = "", **kwargs) -> None:
    """Update video state - convenience wrapper"""
    return StateManager.update_video_state(video_id, status, progress, message, **kwargs)


def calculate_job_state(job: dict, reference_time: Optional[datetime] = None) -> Tuple[JobStatus, Optional[datetime], Optional[str]]:
    """Calculate job state - convenience wrapper"""
    return StateManager.calculate_job_state(job, reference_time)

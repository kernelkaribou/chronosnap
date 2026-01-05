"""
Duration calculation service - estimates video duration based on captures
"""
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, time
import logging

from ..models import DurationEstimate, DurationCalculation

logger = logging.getLogger(__name__)


def calculate_captures_in_time_range(
    start: datetime,
    end: datetime,
    interval_seconds: int,
    time_window_enabled: bool,
    time_window_start: Optional[str] = None,
    time_window_end: Optional[str] = None
) -> int:
    """
    Calculate the number of captures that would occur in a time range,
    accounting for daily time windows if enabled.
    
    Args:
        start: Start datetime
        end: End datetime
        interval_seconds: Capture interval in seconds
        time_window_enabled: Whether time window is enabled
        time_window_start: Start time in HH:MM format
        time_window_end: End time in HH:MM format
        
    Returns:
        Number of captures
    """
    if not time_window_enabled:
        # Simple calculation without time window
        duration_seconds = (end - start).total_seconds()
        return int(duration_seconds / interval_seconds)
    
    # Parse time window
    def parse_time(time_str: str) -> time:
        hours, minutes = map(int, time_str.split(':'))
        return time(hour=hours, minute=minutes)
    
    # Helper to create timezone-aware datetime
    def make_aware(dt: datetime, reference: datetime) -> datetime:
        """Make a naive datetime aware using the timezone from reference datetime"""
        if dt.tzinfo is None and reference.tzinfo is not None:
            return dt.replace(tzinfo=reference.tzinfo)
        return dt
    
    window_start = parse_time(time_window_start)
    window_end = parse_time(time_window_end)
    
    # Determine if window spans midnight
    window_spans_midnight = window_start > window_end
    
    logger.debug(f"Calculating captures: start={start}, end={end}, interval={interval_seconds}s")
    logger.debug(f"Time window: {time_window_start}-{time_window_end} (spans_midnight={window_spans_midnight})")
    
    # Calculate captures by iterating through each window period
    total_captures = 0
    current = start
    
    # Safety limit to prevent infinite loops
    max_iterations = 10000
    iterations = 0
    
    while current < end and iterations < max_iterations:
        iterations += 1
        current_date = current.date()
        current_time = current.time()
        
        # Determine this window's start and end times
        if window_spans_midnight:
            # Window spans midnight (e.g., 22:00 to 02:00)
            if current_time < window_end:
                # We're in the early morning part (before window_end)
                # This window started yesterday
                day_window_start = make_aware(datetime.combine(current_date - timedelta(days=1), window_start), start)
                day_window_end = make_aware(datetime.combine(current_date, window_end), start)
            else:
                # We're after the early morning part
                # Next window starts today at window_start and ends tomorrow at window_end
                day_window_start = make_aware(datetime.combine(current_date, window_start), start)
                day_window_end = make_aware(datetime.combine(current_date + timedelta(days=1), window_end), start)
        else:
            # Normal window (e.g., 08:00 to 20:00)
            day_window_start = make_aware(datetime.combine(current_date, window_start), start)
            day_window_end = make_aware(datetime.combine(current_date, window_end), start)
        
        # Ensure we're not looking at a window in the past
        if day_window_end <= current:
            # This window has already passed, move to next day
            current = make_aware(datetime.combine(current_date + timedelta(days=1), time(0, 0)), start)
            continue
        
        # Find the actual capture period for this window (intersection with our time range)
        capture_start = max(current, day_window_start)
        capture_end = min(end, day_window_end)
        
        logger.debug(f"Iteration {iterations}: day_window={day_window_start.time()}-{day_window_end.time()}, capture_start={capture_start}, capture_end={capture_end}")
        
        if capture_start < capture_end:
            # There's a valid capture period in this window
            window_duration = (capture_end - capture_start).total_seconds()
            window_captures = int(window_duration / interval_seconds)
            total_captures += window_captures
            
            # Move to the end of this window to look for the next one
            current = day_window_end
        else:
            # No overlap with this window, move forward
            if day_window_start >= end:
                # Next window is beyond our end time, we're done
                break
            # Move to the start of this window
            current = day_window_start
    
    logger.debug(f"Total captures calculated: {total_captures}")
    return total_captures


def calculate_duration(
    job: Dict[str, Any],
    hours: Optional[float] = None,
    days: Optional[float] = None
) -> DurationEstimate:
    """
    Calculate estimated video duration for a timelapse job
    
    Args:
        job: Job dictionary with capture settings
        hours: Hours to estimate (for ongoing jobs)
        days: Days to estimate (for ongoing jobs)
        
    Returns:
        DurationEstimate with calculation for job's specified framerate
    """
    interval_seconds = job['interval_seconds']
    fps = job.get('framerate', 30)  # Use job's framerate, default to 30
    time_window_enabled = job.get('time_window_enabled', False)
    
    # Determine number of captures
    if job['end_datetime']:
        # Job has defined end time
        start = datetime.fromisoformat(job['start_datetime'])
        end = datetime.fromisoformat(job['end_datetime'])
        
        total_captures = calculate_captures_in_time_range(
            start, end, interval_seconds,
            time_window_enabled,
            job.get('time_window_start'),
            job.get('time_window_end')
        )
    else:
        # Ongoing job - use provided time estimate
        if days:
            estimate_seconds = days * 24 * 3600
        elif hours:
            estimate_seconds = hours * 3600
        else:
            # Default estimates: 1 hour, 1 day, 1 week, 1 month
            estimate_seconds = 3600  # 1 hour default
        
        start = datetime.fromisoformat(job['start_datetime'])
        end = start + timedelta(seconds=estimate_seconds)
        
        total_captures = calculate_captures_in_time_range(
            start, end, interval_seconds,
            time_window_enabled,
            job.get('time_window_start'),
            job.get('time_window_end')
        )
    
    # Calculate duration for job's specified framerate
    video_duration = total_captures / fps
    
    # Format duration
    hours = int(video_duration // 3600)
    minutes = int((video_duration % 3600) // 60)
    seconds = int(video_duration % 60)
    
    if hours > 0:
        formatted = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        formatted = f"{minutes}m {seconds}s"
    else:
        formatted = f"{seconds}s"
    
    calculation = DurationCalculation(
        fps=fps,
        duration_seconds=video_duration,
        duration_formatted=formatted
    )
    
    return DurationEstimate(
        captures=total_captures,
        calculations=[calculation]
    )

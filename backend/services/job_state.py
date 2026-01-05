"""
Context-aware job state calculator - single source of truth
Replaces the circular dependencies between time_window.py, state_manager.py, and scheduler
"""
from typing import Optional, Tuple, Literal
from datetime import datetime, timedelta, time
import logging

from ..utils import get_now, to_iso, parse_iso, ensure_timezone_aware

logger = logging.getLogger(__name__)

JobStatus = Literal['active', 'sleeping', 'completed', 'disabled']


def calculate_next_capture_on_grid(job: dict, reference_time: datetime) -> Optional[datetime]:
    """
    Calculate next capture time on the schedule grid (start + N * interval).
    Returns None if past end_datetime or before start.
    
    This is the pure mathematical calculation without time window logic.
    """
    start_dt = parse_iso(job['start_datetime'])
    end_dt = parse_iso(job['end_datetime']) if job.get('end_datetime') else None
    interval = job['interval_seconds']
    
    # Before start
    if reference_time < start_dt:
        return start_dt
    
    # Calculate next slot on grid
    elapsed = (reference_time - start_dt).total_seconds()
    intervals_passed = int(elapsed / interval)
    next_capture = start_dt + timedelta(seconds=(intervals_passed + 1) * interval)
    
    # Keep advancing until we find a future time
    while next_capture <= reference_time:
        intervals_passed += 1
        next_capture = start_dt + timedelta(seconds=(intervals_passed + 1) * interval)
    
    # Check if past end
    if end_dt and next_capture > end_dt:
        return None
    
    return next_capture


def is_time_in_window(check_time: time, start_time: time, end_time: time) -> bool:
    """
    Check if a time (hour:minute) is within a time window.
    Ignores seconds - compares only hour and minute.
    """
    current_hm = time(check_time.hour, check_time.minute)
    start_hm = time(start_time.hour, start_time.minute)
    end_hm = time(end_time.hour, end_time.minute)
    
    if start_hm == end_hm:
        # Same minute window (e.g., 12:00-12:00) - only the exact minute matches
        return current_hm == start_hm
    elif start_hm < end_hm:
        # Normal window (doesn't cross midnight)
        return start_hm <= current_hm <= end_hm
    else:
        # Window crosses midnight (e.g., 22:00-02:00)
        return current_hm >= start_hm or current_hm <= end_hm


def parse_time_string(time_str: str) -> time:
    """Parse HH:MM time string to time object"""
    parts = time_str.split(':')
    return time(int(parts[0]), int(parts[1]))


def calculate_next_window_start(reference_time: datetime, start_time: time, end_time: time) -> datetime:
    """Calculate when the time window will next open"""
    current_time = reference_time.time()
    
    # Today's window start - combine date with start_time and preserve timezone from reference_time
    today_start = datetime.combine(reference_time.date(), start_time)
    # Replace timezone with the timezone from reference_time to maintain consistency
    if reference_time.tzinfo:
        today_start = today_start.replace(tzinfo=reference_time.tzinfo)
    
    if is_time_in_window(current_time, start_time, end_time):
        # In window now, next start is tomorrow
        return today_start + timedelta(days=1)
    
    if start_time < end_time:
        # Normal window
        if current_time < start_time:
            return today_start
        else:
            return today_start + timedelta(days=1)
    else:
        # Crosses midnight
        if current_time >= start_time:
            return today_start
        else:
            return today_start - timedelta(days=1)


def find_next_capture_in_window(job: dict, window_start: datetime, start_time: time, end_time: time, max_days: int = 30) -> Optional[datetime]:
    """
    Find the first capture on the grid that falls within a time window.
    Will search across multiple days if needed.
    
    Args:
        job: Job configuration
        window_start: When the first window opens
        start_time: Window start time (HH:MM)
        end_time: Window end time (HH:MM)
        max_days: Maximum number of days to search
        
    Returns:
        First capture time within any window, or None if no captures fit before job ends
    """
    end_dt = parse_iso(job['end_datetime']) if job.get('end_datetime') else None
    
    # Try each day's window
    for day_offset in range(max_days):
        current_window_start = window_start + timedelta(days=day_offset)
        
        # Check if we've gone past the job's end date
        if end_dt and current_window_start > end_dt:
            return None
        
        # Calculate window end for this day
        window_end_time = datetime.combine(current_window_start.date(), end_time)
        if current_window_start.tzinfo:
            window_end_time = window_end_time.replace(tzinfo=current_window_start.tzinfo)
        
        # If window crosses midnight
        if end_time < start_time:
            window_end_time += timedelta(days=1)
        # If start and end are the same (same minute window), extend to end of that minute
        elif end_time == start_time:
            window_end_time += timedelta(seconds=59)
        
        # Start looking from just before the window opens
        search_time = current_window_start - timedelta(seconds=1)
        
        # Look for captures within this specific day's window
        for _ in range(1000):  # Safety limit per day
            candidate = calculate_next_capture_on_grid(job, search_time)
            
            if candidate is None:
                return None
            
            # If candidate is past this window, try next day
            if candidate > window_end_time:
                break
            
            # Check if candidate is within the window
            if is_time_in_window(candidate.time(), start_time, end_time):
                return candidate
            
            # Try next time slot
            search_time = candidate
    
    return None


def calculate_job_state(
    job: dict,
    reference_time: datetime,
    pending_capture_time: Optional[datetime] = None
) -> Tuple[JobStatus, Optional[datetime], str]:
    """
    Calculate the correct state for a job with full context awareness.
    
    This is the single source of truth for job state transitions.
    
    Args:
        job: Job configuration dict
        reference_time: Current time to evaluate state at
        pending_capture_time: If job has a scheduled capture pending, pass it here
                             This enables correct handling of boundary captures
    
    Returns:
        (status, next_capture_time, reason)
        - status: 'active' | 'sleeping' | 'completed' | 'disabled'
        - next_capture_time: When next capture should occur (None if completed)
        - reason: Human-readable explanation
    """
    # Disabled jobs stay disabled
    if job.get('status') == 'disabled':
        return ('disabled', None, 'Job manually disabled')
    
    start_dt = parse_iso(job['start_datetime'])
    end_dt = parse_iso(job['end_datetime']) if job.get('end_datetime') else None
    
    # Job hasn't started yet
    if reference_time < start_dt:
        return ('sleeping', start_dt, f'Job starts at {to_iso(start_dt)}')
    
    # CRITICAL: If there's a pending capture, keep it stable until it's executed
    # This prevents the scheduler from constantly recalculating on every check
    if pending_capture_time:
        # Check if this pending capture has been executed by comparing to last_captured_at
        last_captured = parse_iso(job['last_captured_at']) if job.get('last_captured_at') else None
        
        # If pending capture is after the last capture (or no captures yet), it hasn't been executed
        if not last_captured or pending_capture_time > last_captured:
            # Pending capture hasn't been executed yet - validate it's still valid
            if job.get('time_window_enabled'):
                start_time = parse_time_string(job['time_window_start'])
                end_time = parse_time_string(job['time_window_end'])
                
                # Check BOTH: pending capture is in window AND we're currently in (or very close to) that window
                pending_in_window = is_time_in_window(pending_capture_time.time(), start_time, end_time)
                current_in_window = is_time_in_window(reference_time.time(), start_time, end_time)
                
                # For same-minute windows, only allow execution during that exact minute
                if start_time == end_time and pending_in_window:
                    # Calculate when this specific window opens and closes
                    window_start = datetime.combine(pending_capture_time.date(), start_time)
                    if pending_capture_time.tzinfo:
                        window_start = window_start.replace(tzinfo=pending_capture_time.tzinfo)
                    window_close = window_start + timedelta(seconds=59)
                    
                    # Only allow execution if we're currently in the window
                    if window_start <= reference_time <= window_close:
                        return ('active', pending_capture_time, f'Pending capture at {to_iso(pending_capture_time)}')
                    else:
                        # Outside the window - missed it, recalculate for next window
                        pass  # Fall through to recalculation
                elif pending_in_window and current_in_window:
                    # Normal window - both pending and current are in window
                    return ('active', pending_capture_time, f'Pending capture at {to_iso(pending_capture_time)}')
                else:
                    # Outside window - recalculate
                    pass  # Fall through to recalculation
            else:
                # No time window - allow reasonable catchup (e.g., up to 2x interval for non-windowed jobs)
                max_delay = timedelta(seconds=job['interval_seconds'] * 2)
                if reference_time <= pending_capture_time + max_delay:
                    return ('active', pending_capture_time, f'Pending capture at {to_iso(pending_capture_time)}')
                else:
                    # Too delayed, recalculate
                    pass  # Fall through to recalculation
        
        # If we get here, pending capture was already executed or missed - recalculate
    
    # Calculate next capture on grid
    next_capture = calculate_next_capture_on_grid(job, reference_time)
    
    # No more captures possible (past end_datetime or other issue)
    if next_capture is None:
        return ('completed', None, 'No more captures scheduled')
    
    # Apply time window logic if enabled
    if job.get('time_window_enabled'):
        start_time = parse_time_string(job['time_window_start'])
        end_time = parse_time_string(job['time_window_end'])
        
        # Check if we are CURRENTLY in the time window
        current_in_window = is_time_in_window(reference_time.time(), start_time, end_time)
        next_capture_in_window = is_time_in_window(next_capture.time(), start_time, end_time)
        
        if current_in_window and next_capture_in_window:
            # We're in the window now and next capture is also in window - job is active
            return ('active', next_capture, f'Active, next capture at {to_iso(next_capture)}')
        else:
            # Either we're outside window, or next capture is outside window
            # Calculate when window next opens
            next_window_start = calculate_next_window_start(reference_time, start_time, end_time)
            
            # Find first capture that falls within the window
            window_capture = find_next_capture_in_window(job, next_window_start, start_time, end_time)
            
            if window_capture is None:
                # No captures before job ends
                return ('completed', None, 'Job ends before next window')
            
            return ('sleeping', window_capture, f'Outside time window, next capture at {to_iso(window_capture)}')
    
    # No time window - job is active if there's a next capture
    return ('active', next_capture, f'Active, next capture at {to_iso(next_capture)}')


def should_execute_capture(job: dict, scheduled_time: datetime, current_time: datetime) -> Tuple[bool, str]:
    """
    Determine if a scheduled capture should execute.
    
    This is called when a capture's scheduled time has arrived.
    It validates that the capture is still valid given the job's current configuration.
    
    Args:
        job: Job configuration
        scheduled_time: When this capture was scheduled for
        current_time: Current time
    
    Returns:
        (should_execute, reason)
    """
    start_dt = parse_iso(job['start_datetime'])
    end_dt = parse_iso(job['end_datetime']) if job.get('end_datetime') else None
    
    # Check if scheduled time is within job's valid range
    if scheduled_time < start_dt:
        return (False, 'Scheduled before job start')
    
    if end_dt and scheduled_time > end_dt:
        return (False, 'Scheduled after job end')
    
    # For time-windowed jobs, verify scheduled time was within window
    if job.get('time_window_enabled'):
        start_time = parse_time_string(job['time_window_start'])
        end_time = parse_time_string(job['time_window_end'])
        
        if not is_time_in_window(scheduled_time.time(), start_time, end_time):
            return (False, 'Scheduled time was outside time window')
    
    return (True, 'Valid capture')

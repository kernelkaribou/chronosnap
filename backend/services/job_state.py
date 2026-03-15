"""
Context-aware job state calculator - single source of truth
Replaces the circular dependencies between time_window.py, state_manager.py, and scheduler
"""
from typing import Optional, Tuple, Literal
from datetime import datetime, timedelta, time
import logging

from zoneinfo import ZoneInfo

from ..utils import get_now, to_iso, parse_iso, ensure_timezone_aware, get_local_timezone

logger = logging.getLogger(__name__)

JobStatus = Literal['active', 'sleeping', 'completed', 'disabled']


def calculate_next_capture_on_grid(job: dict, reference_time: datetime) -> Optional[datetime]:
    """
    Calculate next capture time on the schedule grid (start + N * interval).
    Returns None if past end_datetime or before start.
    
    Grid arithmetic is performed in UTC to maintain correct absolute-time
    intervals across DST boundaries. The result is converted back to local
    time so that .time() returns the correct wall-clock time for window checks.
    """
    start_dt = parse_iso(job['start_datetime'])
    end_dt = parse_iso(job['end_datetime']) if job.get('end_datetime') else None
    interval = job['interval_seconds']
    if interval <= 0:
        logger.error(f"Job {job.get('id', '?')} has invalid interval_seconds={interval}, defaulting to 60")
        interval = 60
    local_tz = start_dt.tzinfo or get_local_timezone()
    
    # Before start
    if reference_time < start_dt:
        return start_dt
    
    # Compute grid in UTC where timedelta addition = absolute time addition
    # (no DST wall-clock drift)
    utc = ZoneInfo('UTC')
    start_utc = start_dt.astimezone(utc)
    ref_utc = reference_time.astimezone(utc)
    
    elapsed = (ref_utc - start_utc).total_seconds()
    intervals_passed = int(elapsed / interval)
    next_capture_utc = start_utc + timedelta(seconds=(intervals_passed + 1) * interval)
    
    # Keep advancing until we find a future time
    while next_capture_utc <= ref_utc:
        intervals_passed += 1
        next_capture_utc = start_utc + timedelta(seconds=(intervals_passed + 1) * interval)
    
    # Convert back to local time for correct .time() in window checks
    next_capture = next_capture_utc.astimezone(local_tz)
    
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
    """Calculate when the time window will next open.
    Uses date arithmetic (not timedelta) for DST-safe day advancement."""
    current_time = reference_time.time()
    current_hm = time(current_time.hour, current_time.minute)
    local_tz = reference_time.tzinfo or __import__('backend.utils', fromlist=['get_local_timezone']).get_local_timezone()
    
    # Create today's window start using datetime.combine (DST-safe)
    today_start = datetime.combine(reference_time.date(), start_time, tzinfo=local_tz)
    
    if is_time_in_window(current_time, start_time, end_time):
        # In window now, next start is tomorrow
        tomorrow = reference_time.date() + timedelta(days=1)
        return datetime.combine(tomorrow, start_time, tzinfo=local_tz)
    
    # Handle same-minute window (e.g., 12:00-12:00) - must check before crosses-midnight
    # since start_time == end_time would incorrectly fall into that branch
    if start_time == end_time:
        start_hm = time(start_time.hour, start_time.minute)
        # If we're at or past this minute today, next window is tomorrow
        if current_hm >= start_hm:
            tomorrow = reference_time.date() + timedelta(days=1)
            return datetime.combine(tomorrow, start_time, tzinfo=local_tz)
        else:
            return today_start
    
    if start_time < end_time:
        # Normal window
        if current_time < start_time:
            return today_start
        else:
            tomorrow = reference_time.date() + timedelta(days=1)
            return datetime.combine(tomorrow, start_time, tzinfo=local_tz)
    else:
        # Crosses midnight (e.g., 22:00-06:00)
        if current_time >= start_time:
            # We're currently in the window (past start), next window is tomorrow
            tomorrow = reference_time.date() + timedelta(days=1)
            return datetime.combine(tomorrow, start_time, tzinfo=local_tz)
        else:
            # Before start_time today — next window opens today at start_time
            return today_start


def find_next_capture_in_window(job: dict, window_start: datetime, start_time: time, end_time: time, max_days: int = 30) -> Optional[datetime]:
    """
    Find the first capture on the grid that falls within a time window.
    Will search across multiple days if needed.
    Uses date arithmetic (datetime.combine) for DST-safe day advancement.
    
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
    local_tz = window_start.tzinfo or get_local_timezone()
    base_date = window_start.date()
    
    # Try each day's window using date arithmetic (DST-safe)
    for day_offset in range(max_days):
        window_date = base_date + timedelta(days=day_offset)
        current_window_start = datetime.combine(window_date, start_time, tzinfo=local_tz)
        
        # Check if we've gone past the job's end date
        if end_dt and current_window_start > end_dt:
            return None
        
        # Calculate window end for this day using date arithmetic
        if end_time < start_time:
            # Window crosses midnight — end is the next calendar day
            next_date = window_date + timedelta(days=1)
            window_end_dt = datetime.combine(next_date, end_time, tzinfo=local_tz)
        elif end_time == start_time:
            # Same minute window — extend to end of that minute
            window_end_dt = datetime.combine(window_date, end_time, tzinfo=local_tz) + timedelta(seconds=59)
        else:
            # Normal window within a single day
            window_end_dt = datetime.combine(window_date, end_time, tzinfo=local_tz)
        
        # Start looking from just before the window opens
        search_time = current_window_start - timedelta(seconds=1)
        
        # Look for captures within this specific day's window
        for _ in range(1000):  # Safety limit per day
            candidate = calculate_next_capture_on_grid(job, search_time)
            
            if candidate is None:
                return None
            
            # If candidate is past this window, try next day
            if candidate > window_end_dt:
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
                if not end_dt or end_dt > reference_time:
                    # end_datetime is still in the future — grid may not align with
                    # the window, but the job is NOT done. Stay sleeping and let the
                    # scheduler re-evaluate. Use the next window start as a wake-up hint.
                    remaining = f"{(end_dt - reference_time).days} days" if end_dt else "indefinitely"
                    logger.warning(
                        f"Job '{job.get('name', '?')}': no grid capture aligns with "
                        f"time window within search period, but job runs for "
                        f"{remaining}. Staying sleeping."
                    )
                    return ('sleeping', next_window_start,
                            f'No capture aligned with window yet, re-checking at {to_iso(next_window_start)}')
                # end_datetime has passed or is None with no captures — truly completed
                return ('completed', None, 'No more captures before job ends')
            
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

"""
Capture scheduler service - manages automatic image captures for all active jobs
REFACTORED: Uses context-aware job_state calculator
"""
import threading
import time
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..database import get_db, dict_from_row
from ..utils import get_now, to_iso, parse_iso
from .image_capture import capture_image
from .job_state import calculate_job_state, should_execute_capture
from .webhook import send_webhook_event

logger = logging.getLogger(__name__)


class CaptureScheduler:
    """Background service to schedule and execute captures for all active jobs"""
    
    def __init__(self):
        self.running = False
        self.thread = None
        self.scheduled_captures: Dict[int, datetime] = {}  # job_id -> next scheduled capture time (in-memory queue)
        self.failure_counts: Dict[int, int] = {}  # Track consecutive failures per job
        self.captures_in_progress: set = set()  # Track job_ids currently being captured to prevent duplicates
        self.executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="capture-worker")  # Parallel capture execution
        self._lock = threading.Lock()  # Lock for thread-safe operations on shared data
    
    def is_capture_in_progress(self, job_id: int) -> bool:
        """Check if a capture is currently in progress for a given job"""
        with self._lock:
            return job_id in self.captures_in_progress
    
    def start(self):
        """Start the scheduler thread"""
        if self.running:
            logger.warning("Scheduler already running")
            return
        
        # Hydrate in-memory queue from database on startup
        self._hydrate_from_database()
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"Capture scheduler started with {len(self.scheduled_captures)} jobs in queue")
    
    def stop(self):
        """Stop the scheduler thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        self.executor.shutdown(wait=True, cancel_futures=False)
        logger.info("Capture scheduler stopped")
    
    def is_running(self):
        """Check if scheduler is running"""
        return self.running
    
    def _run_loop(self):
        """Main scheduler loop - runs every 10 seconds"""
        while self.running:
            try:
                self._check_and_capture()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
            
            # Sleep for 10 seconds before next check
            time.sleep(10)
    
    def _hydrate_from_database(self):
        """Load all active/sleeping jobs and their schedules into memory on startup"""
        now = get_now()
        
        with get_db() as conn:
            cursor = conn.cursor()
            # Get all jobs that might need scheduling (not disabled/completed)
            cursor.execute("""
                SELECT * FROM jobs
                WHERE status IN ('active', 'sleeping')
                AND datetime(start_datetime) <= datetime(?)
            """, (to_iso(now),))
            
            jobs = [dict_from_row(row) for row in cursor.fetchall()]
        
        for job in jobs:
            job_id = job['id']
            
            # Calculate state with pending capture awareness
            pending = parse_iso(job['next_scheduled_capture_at']) if job.get('next_scheduled_capture_at') else None
            status, next_capture, reason = calculate_job_state(job, now, pending)
            
            if status == 'active' and next_capture:
                self.scheduled_captures[job_id] = next_capture
                logger.debug(f"Loaded scheduled capture for job {job_id}: {to_iso(next_capture)}")
        
        logger.info(f"Hydrated {len(self.scheduled_captures)} scheduled captures from database")
    
    def _update_job_status(self, job: dict, now: datetime) -> None:
        """
        Update job status based on current conditions.
        Uses context-aware calculator that understands pending captures.
        """
        job_id = job['id']
        current_status = job['status']
        
        # Get pending capture if one exists
        pending = parse_iso(job['next_scheduled_capture_at']) if job.get('next_scheduled_capture_at') else None
        
        # Calculate correct state with full context
        # Important: passing pending ensures we don't recalculate next capture if one is already scheduled
        new_status, next_capture, reason = calculate_job_state(job, now, pending)
        
        # Update database if status changed OR if next_capture changed OR if we need to clear warning for sleeping jobs
        next_capture_iso = to_iso(next_capture) if next_capture else None
        current_next_capture_iso = job.get('next_scheduled_capture_at')
        has_warning = job.get('warning_message') is not None
        should_clear_warning = has_warning and new_status in ('sleeping', 'completed', 'disabled')
        
        if new_status != current_status or next_capture_iso != current_next_capture_iso or should_clear_warning:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE jobs SET status = ?, next_scheduled_capture_at = ?, warning_message = NULL, updated_at = ? WHERE id = ?",
                    (new_status, next_capture_iso, to_iso(now), job_id)
                )
            job['status'] = new_status
            job['next_scheduled_capture_at'] = next_capture_iso
            job['warning_message'] = None
            
            if new_status != current_status:
                logger.info(f"Job {job_id} ({job['name']}) status: {current_status} -> {new_status} - {reason}")
                # Send webhook on completion
                if new_status == 'completed' and current_status != 'completed':
                    send_webhook_event('completed', job['name'], job_id)
            if next_capture_iso != current_next_capture_iso:
                logger.debug(f"Job {job_id} ({job['name']}) next_scheduled_capture_at updated: {current_next_capture_iso} -> {next_capture_iso}")
            if should_clear_warning:
                logger.debug(f"Job {job_id} ({job['name']}) cleared warning message in {new_status} state")
        
        # Update in-memory queue with the value from database
        db_next_capture = parse_iso(job['next_scheduled_capture_at']) if job.get('next_scheduled_capture_at') else None
        
        if new_status == 'active' and db_next_capture:
            self.scheduled_captures[job_id] = db_next_capture
        else:
            self.scheduled_captures.pop(job_id, None)
    
    def _check_and_capture(self):
        """Check scheduled jobs and capture if it's time, using parallel execution"""
        now = get_now()
        
        # Get all jobs that might need processing
        # Include jobs with pending captures even if past end_datetime
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM jobs
                WHERE status IN ('active', 'sleeping')
                AND datetime(start_datetime) <= datetime(?)
                AND (
                    end_datetime IS NULL 
                    OR datetime(end_datetime) >= datetime(?)
                    OR (
                        next_scheduled_capture_at IS NOT NULL
                        AND datetime(next_scheduled_capture_at) <= datetime(end_datetime)
                    )
                )
            """, (to_iso(now), to_iso(now)))
            
            jobs = [dict_from_row(row) for row in cursor.fetchall()]
        
        logger.debug(f"Check at {to_iso(now)}: Found {len(jobs)} active/sleeping jobs")
        
        # PHASE 1: Update job statuses (sleeping/active/completed)
        for job in jobs:
            self._update_job_status(job, now)
        
        # PHASE 2: Collect jobs ready for capture
        jobs_to_capture = []
        for job in jobs:
            # Skip if not active after update
            if job['status'] != 'active':
                continue
            
            job_id = job['id']
            scheduled_time = self.scheduled_captures.get(job_id)
            
            # Check if capture time has arrived
            if not scheduled_time or now < scheduled_time:
                continue
            
            # Validate this capture should execute
            should_execute, reason = should_execute_capture(job, scheduled_time, now)
            if not should_execute:
                logger.debug(f"Job {job_id} ({job['name']}): Skipping capture - {reason}")
                continue
            
            # Only capture if not already in progress
            with self._lock:
                if job_id not in self.captures_in_progress:
                    jobs_to_capture.append(job)
                    self.captures_in_progress.add(job_id)
                    logger.debug(f"Job {job_id} ({job['name']}) ready for capture (scheduled: {to_iso(scheduled_time)})")
                else:
                    logger.info(f"Job {job_id} ({job['name']}): Skipped capture (already in progress)")
        
        # PHASE 3: Execute captures in parallel
        if jobs_to_capture:
            self._execute_captures_parallel(jobs_to_capture, now)
    
    def _execute_captures_parallel(self, jobs: list, capture_time: datetime):
        """Execute multiple captures in parallel using ThreadPoolExecutor"""
        logger.debug(f"Executing {len(jobs)} capture(s) in parallel")
        
        # Submit all capture tasks
        future_to_job = {
            self.executor.submit(self._execute_single_capture, job, capture_time): job
            for job in jobs
        }
        
        # Wait for all captures to complete
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                future.result()  # This will raise any exceptions that occurred
            except Exception as e:
                logger.error(f"Capture task failed for job {job['id']}: {e}", exc_info=True)
    
    def _execute_single_capture(self, job: dict, capture_time: datetime):
        """Execute a single capture and update the schedule"""
        job_id = job['id']
        threshold = job.get('warning_threshold', 3) or 3
        
        try:
            logger.debug(f"Attempting capture for job {job_id}: {job['name']}")
            success, error_message = capture_image(job)
            
            if success:
                prev_failures = self.failure_counts.get(job_id, 0)
                self.failure_counts[job_id] = 0
                logger.debug(f"Successfully captured image for job {job_id}: {job['name']}")
                # Send recovered event if previously in warning state
                if prev_failures >= threshold:
                    send_webhook_event('recovered', job['name'], job_id)
            else:
                self.failure_counts[job_id] = self.failure_counts.get(job_id, 0) + 1
                consecutive_failures = self.failure_counts[job_id]
                
                logger.warning(f"Capture failed for job {job_id}: {job['name']} - {error_message} (failure {consecutive_failures}/{threshold})")
                
                if consecutive_failures >= threshold:
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE jobs SET warning_message = ? WHERE id = ?",
                            (f"{error_message} (after {consecutive_failures} consecutive failures)", job_id)
                        )
                    # Fire webhook when threshold is first reached
                    if consecutive_failures == threshold:
                        send_webhook_event('warning', job['name'], job_id, consecutive_failures, error_message)
                else:
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE jobs SET warning_message = NULL WHERE id = ?",
                            (job_id,)
                        )
        except Exception as e:
            logger.error(f"Failed to capture for job {job_id}: {e}", exc_info=True)
            
            self.failure_counts[job_id] = self.failure_counts.get(job_id, 0) + 1
            consecutive_failures = self.failure_counts[job_id]
            
            if consecutive_failures >= threshold:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE jobs SET warning_message = ? WHERE id = ?",
                        (f"Exception during capture: {str(e)} (after {consecutive_failures} consecutive failures)", job_id)
                    )
                if consecutive_failures == threshold:
                    send_webhook_event('warning', job['name'], job_id, consecutive_failures, str(e))
        finally:
            # Calculate next capture using context-aware calculator
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                if row:
                    job = dict_from_row(row)
                    
                    # Update last_captured_at to the capture time
                    cursor.execute(
                        "UPDATE jobs SET last_captured_at = ? WHERE id = ?",
                        (to_iso(capture_time), job_id)
                    )
                    job['last_captured_at'] = to_iso(capture_time)
                    
                    # Calculate next state (no pending capture now - we just captured)
                    new_status, next_capture, reason = calculate_job_state(job, capture_time, pending_capture_time=None)
                    
                    # Update database
                    cursor.execute(
                        "UPDATE jobs SET status = ?, next_scheduled_capture_at = ?, updated_at = ? WHERE id = ?",
                        (new_status, to_iso(next_capture) if next_capture else None, to_iso(capture_time), job_id)
                    )
                    
                    # Update in-memory queue
                    if new_status == 'active' and next_capture:
                        self.scheduled_captures[job_id] = next_capture
                        logger.debug(f"Job {job_id} next capture at {to_iso(next_capture)}")
                    else:
                        self.scheduled_captures.pop(job_id, None)
                        logger.info(f"Job {job_id} status: {new_status}")
            
            # Remove from in-progress set
            with self._lock:
                self.captures_in_progress.discard(job_id)


# Singleton instance
_scheduler_instance = None

def get_scheduler() -> CaptureScheduler:
    """Get the singleton scheduler instance"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = CaptureScheduler()
    return _scheduler_instance

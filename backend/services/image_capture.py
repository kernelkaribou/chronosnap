"""
Image capture service - handles capturing images from HTTP and RTSP streams
"""
import subprocess
import os
import re
from typing import Dict, Any, Optional
import logging

from ..database import get_db
from .. import config
from ..utils import get_now, to_iso
from .thumbnail_generator import generate_thumbnail
from ..helpers.file_helpers import resolve_capture_path, make_relative
from ..helpers.template_vars import build_datetime_vars

logger = logging.getLogger(__name__)

# Maps quality presets to ffmpeg -q:v values (lower = better quality, 2-31 range)
QUALITY_MAP = {
    'maximum': '2',
    'high': '5',
    'medium': '10',
    'low': '20',
}


def _build_ffmpeg_filters(quality: str = 'maximum', resolution: str = 'native') -> list[str]:
    """Build ffmpeg quality and resolution flags for capture commands."""
    import re
    flags = []
    q_val = QUALITY_MAP.get(quality, '2')
    flags.extend(['-q:v', q_val])
    if resolution and resolution != 'native':
        if not re.match(r'^\d+x\d+$', resolution):
            raise ValueError(f"Invalid resolution format: {resolution}")
        flags.extend(['-vf', f'scale={resolution.replace("x", ":")}'])
    return flags


def capture_image(job: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Capture an image from a video stream
    
    Args:
        job: Job dictionary with capture configuration
        
    Returns:
        tuple: (success: bool, error_message: Optional[str])
    """
    try:
        # Get current capture count
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT capture_count FROM jobs WHERE id = ?", (job['id'],))
            row = cursor.fetchone()
            if row is None:
                return (False, f"Job {job['id']} no longer exists")
            capture_count = row[0]
        
        # Generate filename and hierarchical path structure
        now = get_now()
        pattern = job['naming_pattern']
        count_val = capture_count + 1
        
        # Replace {count} with zero-padded number before .format()
        pattern = pattern.replace('{count}', f'{count_val:06d}')
        
        # Replace remaining placeholders (backward compat: {num:06d}, {timestamp}, etc.)
        dt_vars = build_datetime_vars(now)
        filename = pattern.format(
            job_name=job['name'],
            num=count_val,
            **dt_vars,
        )

        # Sanitize resolved filename: replace chars unsafe across OS/filesystem types
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
        filename += ".jpg"
        
        # Create hierarchical directory structure: job/year/month/day/hour/
        abs_capture_path = resolve_capture_path(job['capture_path'])
        date_path = os.path.join(
            abs_capture_path,
            str(now.year),
            f"{now.month:02d}",
            f"{now.day:02d}",
            f"{now.hour:02d}"
        )
        output_path = os.path.join(date_path, filename)
        
        # Ensure directory exists
        os.makedirs(date_path, exist_ok=True)
        
        # Capture based on stream type
        quality = job.get('capture_quality', 'maximum') or 'maximum'
        resolution = job.get('capture_resolution', 'native') or 'native'
        if job['stream_type'] == 'device':
            success, error_msg = _capture_device(job['url'], output_path, quality, resolution)
        elif job['stream_type'] == 'rtsp':
            success, error_msg = _capture_rtsp(job['url'], output_path, quality, resolution)
        else:  # http
            success, error_msg = _capture_http(job['url'], output_path, quality, resolution)
        
        if success and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            
            # Detect and persist native source dimensions on first capture
            if not job.get('source_width'):
                try:
                    from .url_tester import _probe_source_dimensions
                    stream_type = job.get('stream_type', 'http')
                    sw, sh = _probe_source_dimensions(job['url'], stream_type)
                    if sw and sh:
                        with get_db() as conn:
                            conn.cursor().execute(
                                "UPDATE jobs SET source_width = ?, source_height = ? WHERE id = ?",
                                (sw, sh, job['id'])
                            )
                except Exception as e:
                    logger.debug(f"Could not detect source dimensions for job {job['id']}: {e}")
            
            # Generate thumbnail for the captured image
            try:
                generate_thumbnail(output_path)
            except Exception as thumb_err:
                logger.warning(f"Thumbnail generation failed for {output_path}: {thumb_err}")
            
            # Record capture in database
            with get_db() as conn:
                cursor = conn.cursor()
                
                # Insert capture record (store path relative to captures base)
                from .import_service import get_captures_path
                rel_output = make_relative(output_path, get_captures_path())
                cursor.execute("""
                    INSERT INTO captures (job_id, file_path, file_size, captured_at)
                    VALUES (?, ?, ?, ?)
                """, (job['id'], rel_output, file_size, to_iso(get_now())))
                
                # Update job statistics and clear warning message
                cursor.execute("""
                    UPDATE jobs
                    SET capture_count = capture_count + 1,
                        storage_size = storage_size + ?,
                        updated_at = ?,
                        warning_message = NULL
                    WHERE id = ?
                """, (file_size, to_iso(get_now()), job['id']))
            
            logger.info(f"Captured image for job '{job['name']}' (ID: {job['id']}): {filename}")
            return True, None
        
        return False, error_msg or "Unknown capture error"
        
    except Exception as e:
        logger.error(f"Error capturing image for job {job['id']}: {e}")
        return False, f"Exception: {str(e)}"


def _capture_rtsp(url: str, output_path: str, quality: str = 'maximum', resolution: str = 'native') -> tuple[bool, Optional[str]]:
    """Capture from RTSP stream using FFMPEG over TCP"""
    try:
        cmd = [
            'ffmpeg',
            '-loglevel', 'error',
            '-rtsp_transport', 'tcp',
            '-i', url,
            '-frames:v', '1',
            *_build_ffmpeg_filters(quality, resolution),
            '-y',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=config.FFMPEG_TIMEOUT,
            check=False
        )
        
        if result.returncode == 0:
            return True, None
        else:
            error_msg = result.stderr.decode('utf-8').strip() if result.stderr else "RTSP capture failed"
            logger.error(f"RTSP capture failed: {error_msg}")
            return False, f"RTSP Error: Stream unreachable or invalid"
        
    except subprocess.TimeoutExpired:
        logger.error(f"RTSP capture timed out: {url}")
        return False, "RTSP Error: Connection timeout"
    except Exception as e:
        logger.error(f"RTSP capture error: {e}")
        return False, f"RTSP Error: {str(e)}"


def _capture_http(url: str, output_path: str, quality: str = 'maximum', resolution: str = 'native') -> tuple[bool, Optional[str]]:
    """Capture from HTTP stream using FFMPEG"""
    try:
        cmd = [
            'ffmpeg',
            '-loglevel', 'error',
            '-i', url,
            '-frames:v', '1',
            *_build_ffmpeg_filters(quality, resolution),
            '-y',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=config.FFMPEG_TIMEOUT,
            check=False
        )
        
        if result.returncode == 0:
            return True, None
        else:
            error_msg = result.stderr.decode('utf-8').strip() if result.stderr else "HTTP capture failed"
            logger.error(f"HTTP capture failed: {error_msg}")
            return False, "HTTP Error: Stream unreachable or invalid"
        
    except subprocess.TimeoutExpired:
        logger.error(f"HTTP capture timed out: {url}")
        return False, "HTTP Error: Connection timeout"
    except Exception as e:
        logger.error(f"HTTP capture error: {e}")
        return False, f"HTTP Error: {str(e)}"


def _capture_device(device_path: str, output_path: str, quality: str = 'maximum', resolution: str = 'native') -> tuple[bool, Optional[str]]:
    """Capture from a local video device using the appropriate backend."""
    try:
        if not os.path.exists(device_path):
            return False, f"Device Error: {device_path} not found or inaccessible"
        
        from .capture_backends import build_capture_cmd
        quality_filters = list(_build_ffmpeg_filters(quality, resolution))
        cmd = build_capture_cmd(device_path, output_path, quality_filters)
        
        if cmd is None:
            return False, f"Device Error: No capture backend available for {device_path}"
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=config.FFMPEG_TIMEOUT,
            check=False
        )
        
        if result.returncode == 0:
            return True, None
        else:
            error_msg = result.stderr.decode('utf-8').strip() if result.stderr else "Device capture failed"
            logger.error(f"Device capture failed for {device_path}: {error_msg}")
            return False, f"Device Error: {device_path} not found or inaccessible"
        
    except subprocess.TimeoutExpired:
        logger.error(f"Device capture timed out: {device_path}")
        return False, f"Device Error: Capture timeout on {device_path}"
    except Exception as e:
        logger.error(f"Device capture error: {e}")
        return False, f"Device Error: {str(e)}"

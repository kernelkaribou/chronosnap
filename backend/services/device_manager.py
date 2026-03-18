"""
Device manager service - enumerates and validates V4L2 video devices
"""
import glob
import subprocess
import os
import re
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def list_video_devices() -> List[Dict[str, str]]:
    """
    Enumerate available V4L2 video capture devices.
    
    Scans /dev/video* and uses v4l2-ctl to get device names,
    filtering to only devices that support video capture.
    
    Returns:
        List of dicts with 'path', 'name', and 'driver' keys
    """
    devices = []
    device_paths = sorted(glob.glob('/dev/video*'))
    
    if not device_paths:
        return devices
    
    # Try v4l2-ctl --list-devices for friendly names
    device_names = _get_device_names()
    
    for path in device_paths:
        if not os.access(path, os.R_OK):
            continue
        
        # Check if this device supports video capture
        if not _is_capture_device(path):
            continue
        
        name = device_names.get(path, os.path.basename(path))
        devices.append({
            'path': path,
            'name': name,
            'driver': 'v4l2',
        })
    
    return devices


def _get_device_names() -> Dict[str, str]:
    """Parse v4l2-ctl --list-devices output to map paths to friendly names."""
    names = {}
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--list-devices'],
            capture_output=True, timeout=5, check=False
        )
        if result.returncode != 0:
            return names
        
        output = result.stdout.decode('utf-8', errors='replace')
        current_name = None
        for line in output.splitlines():
            if not line.startswith('\t') and line.strip():
                # Device name line (e.g., "HD Pro Webcam C920 (usb-0000:00:14.0-1):")
                current_name = line.split('(')[0].strip().rstrip(':')
            elif line.strip().startswith('/dev/video') and current_name:
                dev_path = line.strip()
                names[dev_path] = current_name
    except Exception as e:
        logger.debug(f"Could not list device names: {e}")
    
    return names


def _is_capture_device(path: str) -> bool:
    """Check if a V4L2 device supports video capture (not just metadata)."""
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--device', path, '--all'],
            capture_output=True, timeout=5, check=False
        )
        if result.returncode != 0:
            return False
        output = result.stdout.decode('utf-8', errors='replace')
        # Look for "Video Capture" capability
        return 'Video Capture' in output
    except Exception:
        return False


def is_device_available(path: str) -> bool:
    """Check if a device path exists and is accessible."""
    if not re.match(r'^/dev/video\d+$', path):
        return False
    return os.path.exists(path) and os.access(path, os.R_OK)


def get_device_max_resolution(path: str) -> tuple:
    """
    Query the maximum supported resolution for a V4L2 device.
    
    Parses v4l2-ctl --list-formats-ext output, preferring MJPG format
    for higher resolution support. Returns (width, height) or (None, None).
    """
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--device', path, '--list-formats-ext'],
            capture_output=True, timeout=5, check=False
        )
        if result.returncode != 0:
            return None, None
        
        output = result.stdout.decode('utf-8', errors='replace')
        max_area = 0
        best_w, best_h = None, None
        
        for line in output.splitlines():
            match = re.search(r'(\d+)x(\d+)', line)
            if match and 'Size:' in line:
                w, h = int(match.group(1)), int(match.group(2))
                if w * h > max_area:
                    max_area = w * h
                    best_w, best_h = w, h
        
        return best_w, best_h
    except Exception as e:
        logger.debug(f"Could not query max resolution for {path}: {e}")
        return None, None

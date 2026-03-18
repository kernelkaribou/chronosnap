"""
Capture backends package - modular device capture support.

Each backend handles device enumeration, resolution detection,
availability checks, and capture command building for a specific
type of camera interface.
"""
from typing import List, Dict, Optional, Tuple
from .v4l2_backend import V4L2Backend
from .libcamera_backend import LibcameraBackend

import logging

logger = logging.getLogger(__name__)

# Ordered by priority: libcamera first (handles Pi CSI cameras that
# V4L2 can enumerate but not capture from), then V4L2 as the fallback.
_BACKENDS = [
    LibcameraBackend(),
    V4L2Backend(),
]


def list_all_devices() -> List[Dict[str, str]]:
    """
    Enumerate all available capture devices across all backends.
    
    Libcamera devices are checked first. Any device paths claimed by
    libcamera are excluded from V4L2 results to avoid duplicates.
    """
    all_devices = []
    claimed_paths = set()
    
    for backend in _BACKENDS:
        if not backend.is_available():
            continue
        devices = backend.list_devices(exclude_paths=claimed_paths)
        for dev in devices:
            claimed_paths.add(dev['path'])
        all_devices.extend(devices)
    
    return all_devices


def get_device_backend(path: str) -> Optional[object]:
    """Get the appropriate backend for a given device path."""
    for backend in _BACKENDS:
        if not backend.is_available():
            continue
        if backend.owns_device(path):
            return backend
    return None


def get_max_resolution(path: str) -> Tuple[Optional[int], Optional[int]]:
    """Query the max resolution for a device using the appropriate backend."""
    backend = get_device_backend(path)
    if backend:
        return backend.get_max_resolution(path)
    return None, None


def build_capture_cmd(path: str, output_path: str, quality_filters: list) -> Optional[list]:
    """Build the ffmpeg/libcamera capture command for a device."""
    backend = get_device_backend(path)
    if backend:
        return backend.build_capture_cmd(path, output_path, quality_filters)
    return None


def build_probe_cmd(path: str) -> Optional[list]:
    """Build the ffprobe command for a device's resolution detection."""
    backend = get_device_backend(path)
    if backend:
        return backend.build_probe_cmd(path)
    return None


def is_device_available(path: str) -> bool:
    """Check if a device is available via any backend."""
    backend = get_device_backend(path)
    if backend:
        return backend.is_device_available(path)
    return False

"""
V4L2 capture backend - handles USB webcams and V4L2-compatible devices.

Uses ffmpeg with -f v4l2 for capture and v4l2-ctl for device enumeration
and resolution detection. This covers most USB webcams and cameras that
expose standard V4L2 interfaces.
"""
import glob
import subprocess
import os
import re
import logging
from typing import List, Dict, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class V4L2Backend:
    """Capture backend for V4L2-compatible devices (USB webcams, etc.)."""
    
    name = "v4l2"
    
    def is_available(self) -> bool:
        """Check if v4l2-ctl is installed."""
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--version'],
                capture_output=True, timeout=3, check=False
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def list_devices(self, exclude_paths: Set[str] = None) -> List[Dict[str, str]]:
        """
        Enumerate V4L2 video capture devices.
        
        Scans /dev/video*, uses v4l2-ctl for names, and filters to
        devices that support video capture. Skips paths in exclude_paths.
        """
        exclude_paths = exclude_paths or set()
        devices = []
        device_paths = sorted(glob.glob('/dev/video*'))
        
        if not device_paths:
            return devices
        
        device_names = self._get_device_names()
        
        for path in device_paths:
            if path in exclude_paths:
                continue
            if not os.access(path, os.R_OK):
                continue
            if not self._is_capture_device(path):
                continue
            
            name = device_names.get(path, os.path.basename(path))
            devices.append({
                'path': path,
                'name': name,
                'driver': 'v4l2',
            })
        
        return devices
    
    def owns_device(self, path: str) -> bool:
        """Check if this backend should handle a given device path."""
        if not re.match(r'^/dev/video\d+$', path):
            return False
        if not os.path.exists(path):
            return False
        # Check for standard V4L2 capture formats (MJPG, YUYV, etc.)
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', path, '--list-formats-ext'],
                capture_output=True, timeout=5, check=False
            )
            if result.returncode != 0:
                return False
            output = result.stdout.decode('utf-8', errors='replace')
            # V4L2 devices have formats like MJPG, YUYV, H264
            # Unicam/libcamera-only devices have raw Bayer formats only
            standard_formats = ['MJPG', 'YUYV', 'H264', 'H.264', 'JPEG']
            return any(fmt in output for fmt in standard_formats)
        except Exception:
            return False
    
    def is_device_available(self, path: str) -> bool:
        """Check if device path exists and is accessible."""
        if not re.match(r'^/dev/video\d+$', path):
            return False
        return os.path.exists(path) and os.access(path, os.R_OK)
    
    def get_max_resolution(self, path: str) -> Tuple[Optional[int], Optional[int]]:
        """Query the maximum supported resolution via v4l2-ctl."""
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
    
    def build_capture_cmd(self, path: str, output_path: str, quality_filters: list) -> list:
        """Build ffmpeg command for V4L2 device capture."""
        max_w, max_h = self.get_max_resolution(path)
        video_size_args = ['-video_size', f'{max_w}x{max_h}'] if max_w and max_h else []
        
        return [
            'ffmpeg',
            '-loglevel', 'error',
            '-f', 'v4l2',
            *video_size_args,
            '-i', path,
            '-frames:v', '1',
            *quality_filters,
            '-y',
            output_path
        ]
    
    def build_probe_cmd(self, path: str) -> list:
        """Build ffprobe command for V4L2 resolution detection."""
        max_w, max_h = self.get_max_resolution(path)
        video_size_args = ['-video_size', f'{max_w}x{max_h}'] if max_w and max_h else []
        
        return [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
            '-f', 'v4l2',
            *video_size_args,
            path
        ]
    
    def _get_device_names(self) -> Dict[str, str]:
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
                    current_name = line.split('(')[0].strip().rstrip(':')
                elif line.strip().startswith('/dev/video') and current_name:
                    dev_path = line.strip()
                    names[dev_path] = current_name
        except Exception as e:
            logger.debug(f"Could not list device names: {e}")
        
        return names
    
    def _is_capture_device(self, path: str) -> bool:
        """Check if a V4L2 device supports video capture."""
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', path, '--all'],
                capture_output=True, timeout=5, check=False
            )
            if result.returncode != 0:
                return False
            output = result.stdout.decode('utf-8', errors='replace')
            return 'Video Capture' in output
        except Exception:
            return False

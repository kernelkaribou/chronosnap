"""
Libcamera capture backend - handles Raspberry Pi CSI cameras.

Uses libcamera-still for capture and libcamera-hello for device
detection. Required for Pi Camera Modules (v1, v2, v3, HQ) connected
via CSI ribbon cable, which expose raw Bayer formats through V4L2
that ffmpeg cannot directly capture from.

Libcamera tools are not installed by default in the Docker image.
They must be available from the host (via volume mount or Pi OS base
image). This backend gracefully degrades when libcamera is not present.
"""
import subprocess
import os
import re
import logging
from typing import List, Dict, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class LibcameraBackend:
    """Capture backend for Pi CSI cameras via libcamera."""
    
    name = "libcamera"
    _available = None
    _camera_list_cache = None
    
    def is_available(self) -> bool:
        """Check if libcamera-still is installed and functional."""
        if self._available is not None:
            return self._available
        try:
            result = subprocess.run(
                ['libcamera-still', '--version'],
                capture_output=True, timeout=5, check=False
            )
            self._available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._available = False
        
        if self._available:
            logger.info("Libcamera backend available")
        else:
            logger.debug("Libcamera not available, Pi CSI cameras will not be detected")
        
        return self._available
    
    def list_devices(self, exclude_paths: Set[str] = None) -> List[Dict[str, str]]:
        """
        Enumerate libcamera-detected cameras.
        
        Uses libcamera-hello --list-cameras to find Pi CSI cameras.
        Returns devices with their /dev/video* path if identifiable,
        or a libcamera index identifier.
        """
        exclude_paths = exclude_paths or set()
        devices = []
        cameras = self._detect_cameras()
        
        for cam in cameras:
            if cam['path'] in exclude_paths:
                continue
            devices.append({
                'path': cam['path'],
                'name': cam['name'],
                'driver': 'libcamera',
            })
        
        return devices
    
    def owns_device(self, path: str) -> bool:
        """Check if a device path is a libcamera-managed camera."""
        if not self.is_available():
            return False
        cameras = self._detect_cameras()
        return any(cam['path'] == path for cam in cameras)
    
    def is_device_available(self, path: str) -> bool:
        """Check if a libcamera device is accessible."""
        if not self.is_available():
            return False
        if path.startswith('/dev/video'):
            return os.path.exists(path) and os.access(path, os.R_OK)
        return self.owns_device(path)
    
    def get_max_resolution(self, path: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Get the maximum resolution for a libcamera camera.
        
        Parses the mode list from libcamera-hello --list-cameras output.
        """
        cameras = self._detect_cameras()
        for cam in cameras:
            if cam['path'] == path and cam.get('max_width') and cam.get('max_height'):
                return cam['max_width'], cam['max_height']
        return None, None
    
    def build_capture_cmd(self, path: str, output_path: str, quality_filters: list) -> list:
        """
        Build libcamera-still command for CSI camera capture.
        
        Note: libcamera-still outputs JPEG directly. Quality and resolution
        are handled via libcamera flags, not ffmpeg filters. The quality_filters
        list contains ffmpeg-style filters which we translate to libcamera args.
        """
        max_w, max_h = self.get_max_resolution(path)
        camera_index = self._get_camera_index(path)
        
        cmd = ['libcamera-still', '--immediate', '-n']
        
        if camera_index is not None:
            cmd.extend(['--camera', str(camera_index)])
        
        # Set resolution
        width, height = self._extract_resolution_from_filters(quality_filters, max_w, max_h)
        if width and height:
            cmd.extend(['--width', str(width), '--height', str(height)])
        elif max_w and max_h:
            cmd.extend(['--width', str(max_w), '--height', str(max_h)])
        
        # Set quality
        jpeg_quality = self._extract_quality_from_filters(quality_filters)
        if jpeg_quality is not None:
            cmd.extend(['-q', str(jpeg_quality)])
        
        cmd.extend(['-o', output_path])
        return cmd
    
    def build_probe_cmd(self, path: str) -> Optional[list]:
        """
        Libcamera doesn't use ffprobe. Resolution is known from
        _detect_cameras(). Return None to signal the caller to
        use the backend's get_max_resolution() instead.
        """
        return None
    
    def _detect_cameras(self) -> List[Dict]:
        """
        Parse libcamera-hello --list-cameras output.
        
        Example output:
            Available cameras
            -----------------
            0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx219@10)
                Modes: 'SRGGB10_CSI2P' : 640x480 [206.65 fps]
                                          1640x1232 [41.85 fps]
                                          1920x1080 [47.57 fps]
                                          3280x2464 [21.19 fps]
        """
        if self._camera_list_cache is not None:
            return self._camera_list_cache
        
        cameras = []
        try:
            result = subprocess.run(
                ['libcamera-hello', '--list-cameras', '-n'],
                capture_output=True, timeout=10, check=False
            )
            if result.returncode != 0:
                self._camera_list_cache = cameras
                return cameras
            
            output = result.stdout.decode('utf-8', errors='replace')
            if result.stderr:
                output += result.stderr.decode('utf-8', errors='replace')
            
            current_camera = None
            max_area = 0
            
            for line in output.splitlines():
                # Camera header: "0 : imx219 [3280x2464 ..."
                cam_match = re.match(r'^\s*(\d+)\s*:\s*(\S+)\s*\[(\d+)x(\d+)', line)
                if cam_match:
                    if current_camera:
                        cameras.append(current_camera)
                    
                    index = int(cam_match.group(1))
                    sensor = cam_match.group(2)
                    native_w = int(cam_match.group(3))
                    native_h = int(cam_match.group(4))
                    
                    current_camera = {
                        'index': index,
                        'path': self._index_to_path(index),
                        'name': f"{sensor} (Pi Camera)",
                        'max_width': native_w,
                        'max_height': native_h,
                    }
                    max_area = native_w * native_h
                    continue
                
                # Mode resolution lines: "1920x1080 [47.57 fps]"
                if current_camera:
                    mode_match = re.search(r'(\d+)x(\d+)', line)
                    if mode_match and 'Modes' in line or (mode_match and not line.strip().startswith(('Available', '-'))):
                        w, h = int(mode_match.group(1)), int(mode_match.group(2))
                        if w * h > max_area:
                            max_area = w * h
                            current_camera['max_width'] = w
                            current_camera['max_height'] = h
            
            if current_camera:
                cameras.append(current_camera)
            
        except Exception as e:
            logger.debug(f"Error detecting libcamera cameras: {e}")
        
        self._camera_list_cache = cameras
        return cameras
    
    def _index_to_path(self, index: int) -> str:
        """
        Map libcamera camera index to a /dev/video* path.
        
        Libcamera cameras are typically at /dev/video0 for index 0,
        but on systems with other video devices, the mapping may differ.
        We scan for unicam devices to find the correct path.
        """
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--list-devices'],
                capture_output=True, timeout=5, check=False
            )
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='replace')
                unicam_devices = []
                in_unicam = False
                for line in output.splitlines():
                    if not line.startswith('\t') and line.strip():
                        in_unicam = 'unicam' in line.lower() or 'csi' in line.lower()
                    elif line.strip().startswith('/dev/video') and in_unicam:
                        unicam_devices.append(line.strip())
                
                if index < len(unicam_devices):
                    return unicam_devices[index]
        except Exception:
            pass
        
        # Fallback: assume sequential mapping
        return f'/dev/video{index}'
    
    def _get_camera_index(self, path: str) -> Optional[int]:
        """Get the libcamera camera index for a device path."""
        cameras = self._detect_cameras()
        for cam in cameras:
            if cam['path'] == path:
                return cam['index']
        return None
    
    def _extract_resolution_from_filters(self, quality_filters: list, 
                                          max_w: int = None, max_h: int = None) -> Tuple[Optional[int], Optional[int]]:
        """
        Extract target resolution from ffmpeg-style filter arguments.
        
        Looks for scale=WxH patterns in the filter list. Returns (width, height)
        or (None, None) if native resolution should be used.
        """
        for i, arg in enumerate(quality_filters):
            if arg == '-vf' and i + 1 < len(quality_filters):
                scale_match = re.search(r'scale=(\d+):(\d+)', quality_filters[i + 1])
                if scale_match:
                    return int(scale_match.group(1)), int(scale_match.group(2))
        return None, None
    
    def _extract_quality_from_filters(self, quality_filters: list) -> Optional[int]:
        """
        Extract JPEG quality from ffmpeg-style filter arguments.
        
        Maps ffmpeg qscale values (2-15) to libcamera quality (1-100).
        ffmpeg: 2=best, 15=worst; libcamera: 100=best, 1=worst
        """
        for i, arg in enumerate(quality_filters):
            if arg in ('-qscale:v', '-q:v') and i + 1 < len(quality_filters):
                try:
                    ffmpeg_q = int(quality_filters[i + 1])
                    # Map ffmpeg 2-15 to libcamera 95-15
                    libcamera_q = max(15, min(95, int(95 - (ffmpeg_q - 2) * (80 / 13))))
                    return libcamera_q
                except ValueError:
                    pass
        return 95  # Default to high quality
    
    def invalidate_cache(self):
        """Clear the camera detection cache (e.g., after device changes)."""
        self._camera_list_cache = None
        self._available = None

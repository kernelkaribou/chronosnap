"""
URL testing service - validates stream URLs and captures test images
"""
import subprocess
import os
import base64
import tempfile
import logging

from ..models import TestUrlResponse
from .. import config
from .image_capture import _build_ffmpeg_filters

logger = logging.getLogger(__name__)


def _get_image_dimensions(path: str) -> tuple[int, int] | tuple[None, None]:
    """Get image width and height using Pillow."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size  # (width, height)
    except Exception:
        return None, None


def _probe_source_dimensions(url: str, stream_type: str = 'http') -> tuple[int, int] | tuple[None, None]:
    """Probe native source resolution using ffprobe without capturing."""
    try:
        cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=width,height', '-of', 'csv=p=0']
        if stream_type == 'rtsp':
            cmd.extend(['-rtsp_transport', 'tcp'])
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, timeout=config.FFMPEG_TIMEOUT, check=False)
        if result.returncode == 0:
            parts = result.stdout.decode().strip().split(',')
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None, None


async def test_stream_url(url: str, stream_type: str = None,
                          quality: str = 'maximum', resolution: str = 'native') -> TestUrlResponse:
    """
    Test a stream URL by attempting to capture a single frame
    
    Args:
        url: The stream URL to test
        stream_type: Either 'http' or 'rtsp' (auto-detected if not provided)
        quality: Capture quality preset (maximum/high/medium/low)
        resolution: 'native' or 'WxH' string
        
    Returns:
        TestUrlResponse with success status, test image info, and source dimensions
    """
    try:
        # Auto-detect stream type if not provided
        if stream_type is None:
            stream_type = 'rtsp' if url.lower().startswith('rtsp://') else 'http'
        
        # Create temp file for test capture
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            output_path = tmp.name
        
        # Build ffmpeg command with quality/resolution settings
        if stream_type == 'rtsp':
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
        else:  # http
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
        
        if result.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            
            # Detect native source dimensions (before any scaling)
            source_width, source_height = _probe_source_dimensions(url, stream_type)
            # Fallback: if probe fails and no scaling was applied, use image dimensions
            if source_width is None and (not resolution or resolution == 'native'):
                source_width, source_height = _get_image_dimensions(output_path)
            
            # Read and encode image as base64
            with open(output_path, 'rb') as img_file:
                image_bytes = img_file.read()
                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            # Clean up temp file immediately
            os.remove(output_path)
            
            return TestUrlResponse(
                success=True,
                message="Successfully captured test image",
                image_data=f"data:image/jpeg;base64,{image_base64}",
                image_size=file_size,
                source_width=source_width,
                source_height=source_height,
            )
        else:
            # Clean up temp file
            if os.path.exists(output_path):
                os.remove(output_path)
            
            error_msg = result.stderr.decode() if result.stderr else "Unknown error"
            return TestUrlResponse(
                success=False,
                message=f"Error: Please check the URL. {error_msg[:100]}"
            )
            
    except subprocess.TimeoutExpired:
        return TestUrlResponse(
            success=False,
            message="Error: Connection timed out. Please check the URL."
        )
    except Exception as e:
        logger.error(f"Error testing URL: {e}")
        return TestUrlResponse(
            success=False,
            message=f"Error: {str(e)}"
        )

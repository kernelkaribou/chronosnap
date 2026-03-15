"""
Thumbnail generation service - creates small preview images for captures
"""
import os
import hashlib
from typing import Optional
import logging
from PIL import Image

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (384, 216)  # 16:9 aspect ratio for better quality
THUMBNAIL_FORMAT = "webp"
THUMBNAIL_QUALITY = 75


def get_thumbnail_path(image_path: str) -> str:
    """
    Generate the thumbnail path for a given image path using hash-based structure
    
    Args:
        image_path: Path to the original image
        
    Returns:
        Path where thumbnail should be stored
    """
    # Get the job directory (parent of year folder in hierarchy)
    # Path structure: /captures/job_name/year/month/day/hour/image.jpg
    path_parts = image_path.split(os.sep)
    
    # Find the captures directory and extract job folder
    try:
        captures_idx = path_parts.index('captures')
        job_name = path_parts[captures_idx + 1]
        captures_base = os.sep.join(path_parts[:captures_idx + 1])
    except (ValueError, IndexError):
        # Fallback to old behavior if path structure is unexpected
        base, _ = os.path.splitext(image_path)
        return f"{base}.thumb.{THUMBNAIL_FORMAT}"
    
    # Get original filename
    original_filename = os.path.basename(image_path)
    filename_no_ext = os.path.splitext(original_filename)[0]
    
    # Calculate hash of the full image path for distribution
    path_hash = hashlib.md5(image_path.encode('utf-8')).hexdigest()
    
    # Create hierarchical structure: job/thumbs/<first_digit>/<next_two_digits>/
    first_digit = path_hash[0]
    next_two_digits = path_hash[1:3]
    
    # Build thumbnail path
    thumbs_dir = os.path.join(captures_base, job_name, 'thumbs', first_digit, next_two_digits)
    thumbnail_filename = f"{filename_no_ext}.{THUMBNAIL_FORMAT}"
    
    return os.path.join(thumbs_dir, thumbnail_filename)


def generate_thumbnail(image_path: str, force: bool = False) -> tuple[bool, Optional[str]]:
    """
    Generate a thumbnail for an image
    
    Args:
        image_path: Path to the original image
        force: If True, regenerate even if thumbnail exists
        
    Returns:
        tuple: (success: bool, error_message: Optional[str])
    """
    if not os.path.exists(image_path):
        return False, f"Image file not found: {image_path}"
    
    thumbnail_path = get_thumbnail_path(image_path)
    
    # Skip if thumbnail already exists and force is False
    if os.path.exists(thumbnail_path) and not force:
        return True, None
    
    try:
        # Create thumbnail directory if it doesn't exist
        thumbnail_dir = os.path.dirname(thumbnail_path)
        os.makedirs(thumbnail_dir, exist_ok=True)
        
        # Open image with PIL
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (handles RGBA, palette mode, etc.)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # Create thumbnail (maintains aspect ratio)
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            
            # Save as webp
            img.save(thumbnail_path, THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY, method=6)
        
        logger.debug(f"Generated thumbnail: {thumbnail_path}")
        return True, None
        
    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {image_path}: {e}")
        return False, str(e)


def delete_thumbnail(image_path: str) -> bool:
    """
    Delete the thumbnail for an image
    
    Args:
        image_path: Path to the original image
        
    Returns:
        True if thumbnail was deleted or didn't exist, False on error
    """
    thumbnail_path = get_thumbnail_path(image_path)
    
    if not os.path.exists(thumbnail_path):
        return True
    
    try:
        os.remove(thumbnail_path)
        logger.debug(f"Deleted thumbnail: {thumbnail_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete thumbnail {thumbnail_path}: {e}")
        return False


def has_thumbnail(image_path: str) -> bool:
    """
    Check if a thumbnail exists for an image
    
    Args:
        image_path: Path to the original image
        
    Returns:
        True if thumbnail exists, False otherwise
    """
    thumbnail_path = get_thumbnail_path(image_path)
    return os.path.exists(thumbnail_path)

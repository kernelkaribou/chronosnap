"""Reusable file operation helpers."""

import os
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def validate_writable_directory(path: str, label: str = "Path"):
    """Validate that a path exists, is a directory, and is writable. Raises HTTPException on failure."""
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"{label} does not exist: {path}")
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"{label} is not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise HTTPException(status_code=400, detail=f"No write permission for {label.lower()}: {path}")


def delete_capture_file(file_path: str, delete_thumbnail_fn):
    """Delete a capture file and its thumbnail. Logs but doesn't raise on missing files."""
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Deleted capture file: {file_path}")
    delete_thumbnail_fn(file_path)


def delete_video_files(file_path: str, thumbnail_path: str | None = None):
    """Delete a video file and optionally its thumbnail."""
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Deleted video file: {file_path}")
    if thumbnail_path and os.path.exists(thumbnail_path):
        os.remove(thumbnail_path)
        logger.info(f"Deleted video thumbnail: {thumbnail_path}")

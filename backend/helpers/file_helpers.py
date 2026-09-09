"""Reusable file operation helpers."""

import os
import re
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def sanitize_directory_name(name: str) -> str:
    """Strip characters that are unsafe in a filesystem path segment
    (path separators, traversal sequences, quotes, control characters, etc.),
    keeping only word characters, whitespace, and hyphens.

    Used to derive on-disk directory/file names from user-supplied display
    names (job names, video names) — never use the raw name directly when
    building a filesystem path.
    """
    return re.sub(r'[^\w\s-]', '', name or '').strip()


def validate_path_within(path: str, allowed_prefix: str) -> str:
    """Canonicalize a path and verify it's within the allowed prefix.

    Returns the canonicalized (realpath'd) path.
    Raises ValueError if the path escapes the allowed prefix.
    """
    real_path = os.path.realpath(path)
    real_prefix = os.path.realpath(allowed_prefix)

    # Must be the prefix itself or a child of it
    if real_path != real_prefix and not real_path.startswith(real_prefix + os.sep):
        raise ValueError(f"Path escapes allowed boundary: {path}")

    return real_path


# ---------------------------------------------------------------------------
# Path resolution: DB stores relative paths, filesystem needs absolute
# ---------------------------------------------------------------------------

def resolve_path(relative_path: str, base_path: str) -> str:
    """Resolve a relative DB path to an absolute filesystem path."""
    if not relative_path:
        return base_path
    if os.path.isabs(relative_path):
        return relative_path
    return os.path.join(base_path, relative_path)


def make_relative(absolute_path: str, base_path: str) -> str:
    """Convert an absolute filesystem path to a path relative to the base."""
    return os.path.relpath(absolute_path, base_path)


def resolve_capture_path(relative_path: str) -> str:
    """Resolve a capture-relative DB path to absolute."""
    from ..services.import_service import get_captures_path
    return resolve_path(relative_path, get_captures_path())


def resolve_video_path(relative_path: str) -> str:
    """Resolve a video/timelapse-relative DB path to absolute."""
    from ..services.import_service import get_timelapses_path
    return resolve_path(relative_path, get_timelapses_path())


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


def cleanup_empty_parents(file_path: str, base_path: str):
    """Remove empty parent directories between file_path and base_path.
    Walks up from the file's directory, removing each empty folder,
    stopping at (and never removing) base_path."""
    base = os.path.realpath(base_path)
    folder = os.path.dirname(os.path.realpath(file_path))
    # Boundary check must require an exact match or a path *under* base
    # (base + os.sep prefix) — a plain startswith(base) would also match an
    # unrelated sibling directory that happens to share a string prefix,
    # e.g. "/timelapses-old" starting with "/timelapses".
    while folder and folder != base and folder.startswith(base + os.sep):
        try:
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
                logger.info(f"Removed empty folder: {folder}")
                folder = os.path.dirname(folder)
            else:
                break
        except OSError:
            break

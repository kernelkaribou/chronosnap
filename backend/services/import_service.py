"""
Import service — staging management, file classification, path security, archive extraction.
"""
import os
import re
import uuid
import time
import shutil
import hashlib
import logging
import zipfile
import tarfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Set

from .. import config
from ..utils import get_now, to_iso

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported file extensions
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v'}
ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.tgz', '.bz2', '.rar', '.7z'}
JUNK_NAMES = {'__MACOSX', '.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes'}

# ---------------------------------------------------------------------------
# Security limits
# ---------------------------------------------------------------------------
MAX_EXTRACTION_RATIO = 20       # max extracted_size / archive_size
MAX_FILE_COUNT = 100_000
MAX_NESTING_DEPTH = 2
MAX_SINGLE_FILE_SIZE = 50 * 1024 * 1024 * 1024  # 50GB
EXTRACTION_TIMEOUT = 600        # 10 minutes
MAX_FILENAME_LENGTH = 255
MAX_CONCURRENT_SESSIONS = 5
STALE_STAGING_HOURS = 2


# ===========================================================================
# Path security
# ===========================================================================

def _get_setting_path(key: str, default: str) -> str:
    """Get a configured path from settings or return default."""
    try:
        from ..database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
    except Exception as e:
        logger.warning(f"Failed to read setting '{key}': {e}")
    return default


def get_import_path() -> str:
    """Get the configured import path from settings or default."""
    return _get_setting_path('import_path', config.DEFAULT_IMPORT_PATH)


def get_export_path() -> str:
    """Get the configured export path from settings or default."""
    return _get_setting_path('export_path', config.DEFAULT_EXPORTS_PATH)


def get_captures_path() -> str:
    """Get the configured captures path from settings or default."""
    return _get_setting_path('captures_path', config.DEFAULT_CAPTURES_PATH)


def get_timelapses_path() -> str:
    """Get the configured timelapses path from settings or default."""
    return _get_setting_path('timelapses_path', config.DEFAULT_VIDEOS_PATH)


def cleanup_old_exports(max_age_days: int = None):
    """Remove export archives older than max_age_days. 0 = keep indefinitely."""
    if max_age_days is None:
        # Read from DB setting, fall back to config default
        try:
            from ..database import get_db
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM settings WHERE key = 'export_retention_days'")
                row = cursor.fetchone()
                if row and row[0] is not None:
                    max_age_days = int(row[0])
                else:
                    max_age_days = config.EXPORT_RETENTION_DAYS
        except Exception:
            max_age_days = config.EXPORT_RETENTION_DAYS

    if max_age_days == 0:
        return 0

    export_path = get_export_path()
    if not os.path.isdir(export_path):
        return 0

    import time
    now = time.time()
    deleted = 0

    for entry in os.scandir(export_path):
        if not entry.is_file() or not entry.name.endswith('.zip'):
            continue
        age_days = (now - entry.stat().st_mtime) / 86400
        if age_days > max_age_days:
            try:
                os.remove(entry.path)
                deleted += 1
                logger.info(f"Cleaned up old export ({age_days:.0f}d old): {entry.name}")
            except OSError as e:
                logger.error(f"Failed to cleanup export {entry.name}: {e}")

    if deleted:
        logger.info(f"Cleaned up {deleted} old export(s)")
    return deleted


def validate_path_within(path: str, allowed_prefix: str) -> str:
    """Canonicalize a path and verify it's within the allowed prefix.
    
    Returns the canonicalized path.
    Raises ValueError if the path escapes the allowed prefix.
    """
    real_path = os.path.realpath(path)
    real_prefix = os.path.realpath(allowed_prefix)
    
    # Must be the prefix itself or a child of it
    if real_path != real_prefix and not real_path.startswith(real_prefix + os.sep):
        raise ValueError(f"Path escapes allowed boundary: {path}")
    
    return real_path


def sanitize_filename(name: str) -> str:
    """Sanitize a filename for safe filesystem use.
    
    - Strips path separators and null bytes
    - Normalizes Unicode
    - Removes special characters
    - Caps length at MAX_FILENAME_LENGTH
    """
    import unicodedata
    
    # Remove null bytes
    name = name.replace('\x00', '')
    
    # Take only the basename (strip any path components)
    name = os.path.basename(name)
    
    # Normalize unicode
    name = unicodedata.normalize('NFC', name)
    
    # Preserve extension
    base, ext = os.path.splitext(name)
    
    # Remove characters that are problematic on filesystems
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', base)
    ext = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', ext)
    
    # Collapse multiple underscores/spaces
    base = re.sub(r'[_\s]+', '_', base).strip('_. ')
    
    if not base:
        base = 'unnamed'
    
    # Cap length
    max_base = MAX_FILENAME_LENGTH - len(ext)
    if len(base) > max_base:
        base = base[:max_base]
    
    return base + ext


def check_disk_space(path: str, required_bytes: int) -> bool:
    """Check if there's at least required_bytes * 2 available at path."""
    try:
        stat = shutil.disk_usage(path)
        return stat.free >= required_bytes * 2
    except Exception:
        return False


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file. Reads in 8MB chunks for efficiency."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        logger.warning(f"Failed to compute hash for {file_path}: {e}")
        return ''


# ===========================================================================
# File type detection
# ===========================================================================

def detect_file_type(file_path: str) -> str:
    """Detect file type using extension + magic bytes.
    
    Returns one of: 'image', 'video', 'archive', 'unknown'
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    # Quick extension check first
    if ext in IMAGE_EXTENSIONS:
        category = 'image'
    elif ext in VIDEO_EXTENSIONS:
        category = 'video'
    elif ext in ARCHIVE_EXTENSIONS or ext in ('.tar.gz', '.tar.bz2'):
        category = 'archive'
    else:
        return 'unknown'
    
    # Validate with magic bytes
    if not _verify_magic_bytes(file_path, category, ext):
        logger.warning(f"Magic byte mismatch for {file_path} (claimed {category}/{ext})")
        return 'unknown'
    
    return category


def _verify_magic_bytes(file_path: str, category: str, ext: str) -> bool:
    """Verify file content matches expected type via magic bytes."""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)
    except Exception:
        return False
    
    if not header:
        return False
    
    if category == 'image':
        if ext in ('.jpg', '.jpeg'):
            return header[:3] == b'\xff\xd8\xff'
        elif ext == '.png':
            return header[:8] == b'\x89PNG\r\n\x1a\n'
        elif ext == '.bmp':
            return header[:2] == b'BM'
        elif ext in ('.tiff', '.tif'):
            return header[:4] in (b'II\x2a\x00', b'MM\x00\x2a')
        elif ext == '.webp':
            return header[:4] == b'RIFF' and header[8:12] == b'WEBP'
        return True  # Other image types: trust extension
        
    elif category == 'video':
        if ext in ('.mp4', '.m4v', '.mov'):
            # ftyp box: bytes 4-8 should be 'ftyp'
            return header[4:8] == b'ftyp' or header[:4] == b'\x00\x00\x00'
        elif ext == '.avi':
            return header[:4] == b'RIFF' and header[8:12] == b'AVI '
        elif ext in ('.mkv', '.webm'):
            return header[:4] == b'\x1a\x45\xdf\xa3'
        return True  # Trust extension for other video types
        
    elif category == 'archive':
        if ext == '.zip':
            return header[:2] == b'PK'
        elif ext == '.rar':
            return header[:4] == b'Rar!'
        elif ext == '.7z':
            return header[:6] == b'7z\xbc\xaf\x27\x1c'
        elif ext in ('.gz', '.tgz'):
            return header[:2] == b'\x1f\x8b'
        elif ext == '.bz2':
            return header[:2] == b'BZ'
        elif ext == '.tar':
            try:
                return tarfile.is_tarfile(file_path)
            except Exception:
                return False
        return True
    
    return True


def classify_extension(ext: str) -> str:
    """Classify a file extension into a category."""
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    if ext in ARCHIVE_EXTENSIONS:
        return 'archive'
    return 'unknown'


# ===========================================================================
# Staging directory management
# ===========================================================================

def create_staging_session() -> str:
    """Create a new staging session directory. Returns session UUID."""
    session_id = str(uuid.uuid4())
    staging_dir = os.path.join(config.IMPORT_STAGING_DIR, session_id)
    os.makedirs(os.path.join(staging_dir, 'raw'), exist_ok=True)
    os.makedirs(os.path.join(staging_dir, 'extracted'), exist_ok=True)
    logger.info(f"Created import staging session: {session_id}")
    return session_id


def set_staging_source(session_id: str, source_path: str) -> None:
    """Record the original source path for a staging session (for post-import cleanup)."""
    staging_dir = get_staging_dir(session_id)
    meta_file = os.path.join(staging_dir, '.source_path')
    with open(meta_file, 'w') as f:
        f.write(source_path)


def get_staging_source(session_id: str) -> Optional[str]:
    """Get the original source path recorded for a staging session."""
    try:
        staging_dir = get_staging_dir(session_id)
        meta_file = os.path.join(staging_dir, '.source_path')
        if os.path.isfile(meta_file):
            with open(meta_file, 'r') as f:
                return f.read().strip()
    except (ValueError, OSError):
        pass
    return None


def cleanup_import_source(session_id: str) -> None:
    """Remove the original source files/directory after a successful import.
    
    Only cleans up paths within the configured import directory.
    For directories, removes the entire directory tree.
    For single files, removes the file.
    """
    source_path = get_staging_source(session_id)
    if not source_path:
        return
    
    import_path = get_import_path()
    try:
        real_source = validate_path_within(source_path, import_path)
    except ValueError:
        logger.warning(f"Source path escapes import dir, skipping cleanup: {source_path}")
        return
    
    # Never delete the import root itself
    if os.path.realpath(real_source) == os.path.realpath(import_path):
        logger.warning("Source path is the import root, skipping cleanup")
        return
    
    try:
        if os.path.isfile(real_source):
            os.remove(real_source)
            logger.info(f"Cleaned up import source file: {real_source}")
        elif os.path.isdir(real_source):
            shutil.rmtree(real_source)
            logger.info(f"Cleaned up import source directory: {real_source}")
    except Exception as e:
        logger.warning(f"Failed to clean up import source: {e}")


def get_staging_dir(session_id: str) -> str:
    """Get and validate a staging directory path.
    
    Validates session_id is a proper UUID and the dir exists.
    Raises ValueError on any issue.
    """
    # Validate UUID format
    try:
        uuid.UUID(session_id, version=4)
    except ValueError:
        raise ValueError(f"Invalid session ID format")
    
    staging_dir = os.path.join(config.IMPORT_STAGING_DIR, session_id)
    
    # Jail check
    validate_path_within(staging_dir, config.IMPORT_STAGING_DIR)
    
    if not os.path.isdir(staging_dir):
        raise ValueError(f"Staging session not found: {session_id}")
    
    return staging_dir


def cleanup_staging(session_id: str) -> None:
    """Remove a staging session directory."""
    try:
        staging_dir = get_staging_dir(session_id)
        shutil.rmtree(staging_dir, ignore_errors=True)
        logger.info(f"Cleaned up staging session: {session_id}")
    except ValueError:
        pass  # Already gone or invalid


def cleanup_stale_staging() -> int:
    """Remove staging directories older than STALE_STAGING_HOURS. Returns count removed."""
    staging_base = config.IMPORT_STAGING_DIR
    if not os.path.isdir(staging_base):
        return 0
    
    cutoff = time.time() - (STALE_STAGING_HOURS * 3600)
    removed = 0
    
    for entry in os.scandir(staging_base):
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
                logger.info(f"Removed stale staging dir: {entry.name}")
                removed += 1
        except Exception as e:
            logger.warning(f"Error checking staging dir {entry.name}: {e}")
    
    if removed:
        logger.info(f"Cleaned up {removed} stale staging session(s)")
    return removed


def count_active_sessions() -> int:
    """Count currently active staging sessions."""
    staging_base = config.IMPORT_STAGING_DIR
    if not os.path.isdir(staging_base):
        return 0
    return sum(1 for e in os.scandir(staging_base) if e.is_dir())


# ===========================================================================
# Archive extraction
# ===========================================================================

def _is_junk_entry(name: str) -> bool:
    """Check if an archive entry is junk that should be skipped."""
    basename = os.path.basename(name)
    # Skip hidden files/dirs
    if basename.startswith('.'):
        return True
    # Skip known junk
    parts = Path(name).parts
    for part in parts:
        if part in JUNK_NAMES:
            return True
    return False


def _validate_archive_entry(entry_name: str, dest_base: str) -> Optional[str]:
    """Validate an archive entry name is safe. Returns resolved dest path or None to skip."""
    # Reject absolute paths
    if os.path.isabs(entry_name):
        logger.warning(f"Skipping archive entry with absolute path: {entry_name}")
        return None
    
    # Reject path traversal
    if '..' in Path(entry_name).parts:
        logger.warning(f"Skipping archive entry with path traversal: {entry_name}")
        return None
    
    # Skip junk
    if _is_junk_entry(entry_name):
        return None
    
    # Sanitize each component
    parts = Path(entry_name).parts
    safe_parts = [sanitize_filename(p) for p in parts]
    safe_name = os.path.join(*safe_parts) if safe_parts else None
    if not safe_name:
        return None
    
    dest_path = os.path.join(dest_base, safe_name)
    
    # Final jail check
    real_dest = os.path.realpath(dest_path)
    real_base = os.path.realpath(dest_base)
    if not real_dest.startswith(real_base + os.sep) and real_dest != real_base:
        logger.warning(f"Archive entry escapes destination: {entry_name}")
        return None
    
    return dest_path


class ExtractionStats:
    """Track extraction progress and enforce limits."""
    
    def __init__(self, archive_size: int):
        self.archive_size = max(archive_size, 1)
        self.total_extracted = 0
        self.file_count = 0
        self.start_time = time.time()
    
    def check_file(self, file_size: int) -> bool:
        """Check if extracting this file would violate limits. Returns True if ok."""
        if file_size > MAX_SINGLE_FILE_SIZE:
            return False
        
        new_total = self.total_extracted + file_size
        if new_total > self.archive_size * MAX_EXTRACTION_RATIO:
            return False
        
        if self.file_count + 1 > MAX_FILE_COUNT:
            return False
        
        if time.time() - self.start_time > EXTRACTION_TIMEOUT:
            return False
        
        return True
    
    def record_file(self, file_size: int):
        self.total_extracted += file_size
        self.file_count += 1
    
    def check_timeout(self) -> bool:
        return time.time() - self.start_time > EXTRACTION_TIMEOUT


def extract_archive(archive_path: str, dest_dir: str, depth: int = 0) -> Dict[str, Any]:
    """Extract an archive safely with all security checks.
    
    Returns dict with 'extracted_count', 'extracted_size', 'errors', 'nested_archives'.
    """
    if depth > MAX_NESTING_DEPTH:
        return {'extracted_count': 0, 'extracted_size': 0, 'errors': ['Max nesting depth exceeded'], 'nested_archives': []}
    
    archive_size = os.path.getsize(archive_path)
    stats = ExtractionStats(archive_size)
    errors = []
    nested_archives = []
    ext = os.path.splitext(archive_path)[1].lower()
    
    # Detect compound extensions like .tar.gz
    name_lower = archive_path.lower()
    if name_lower.endswith('.tar.gz') or name_lower.endswith('.tgz'):
        ext = '.tar.gz'
    elif name_lower.endswith('.tar.bz2'):
        ext = '.tar.bz2'
    
    try:
        if ext == '.zip':
            _extract_zip(archive_path, dest_dir, stats, errors, nested_archives)
        elif ext in ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.gz'):
            _extract_tar(archive_path, dest_dir, stats, errors, nested_archives)
        elif ext == '.rar':
            _extract_rar(archive_path, dest_dir, stats, errors, nested_archives)
        elif ext == '.7z':
            _extract_7z(archive_path, dest_dir, stats, errors, nested_archives)
        else:
            errors.append(f"Unsupported archive format: {ext}")
    except Exception as e:
        errors.append(f"Extraction error: {str(e)}")
        logger.error(f"Archive extraction failed for {archive_path}: {e}")
    
    # Recursively extract nested archives
    for nested_path in nested_archives:
        if depth + 1 <= MAX_NESTING_DEPTH:
            nested_dest = dest_dir  # Extract in place
            nested_result = extract_archive(nested_path, nested_dest, depth + 1)
            stats.total_extracted += nested_result['extracted_size']
            stats.file_count += nested_result['extracted_count']
            errors.extend(nested_result['errors'])
            # Remove the nested archive after extraction
            try:
                os.remove(nested_path)
            except Exception:
                pass
    
    return {
        'extracted_count': stats.file_count,
        'extracted_size': stats.total_extracted,
        'errors': errors,
        'nested_archives': [],
    }


def _extract_zip(archive_path: str, dest_dir: str, stats: ExtractionStats,
                  errors: List[str], nested_archives: List[str]):
    """Extract a ZIP archive safely."""
    with zipfile.ZipFile(archive_path, 'r') as zf:
        for info in zf.infolist():
            if stats.check_timeout():
                errors.append("Extraction timeout reached")
                break
            
            if info.is_dir():
                continue
            
            dest_path = _validate_archive_entry(info.filename, dest_dir)
            if not dest_path:
                continue
            
            if not stats.check_file(info.file_size):
                errors.append(f"Skipped {info.filename}: exceeds extraction limits")
                continue
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            with zf.open(info) as src, open(dest_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            
            os.chmod(dest_path, 0o644)
            stats.record_file(info.file_size)
            
            # Check if extracted file is a nested archive
            if classify_extension(os.path.splitext(dest_path)[1]) == 'archive':
                nested_archives.append(dest_path)


def _extract_tar(archive_path: str, dest_dir: str, stats: ExtractionStats,
                  errors: List[str], nested_archives: List[str]):
    """Extract a tar/tar.gz/tar.bz2 archive safely."""
    mode = 'r:*'  # Auto-detect compression
    with tarfile.open(archive_path, mode) as tf:
        for member in tf:
            if stats.check_timeout():
                errors.append("Extraction timeout reached")
                break
            
            if not member.isfile():
                continue
            
            dest_path = _validate_archive_entry(member.name, dest_dir)
            if not dest_path:
                continue
            
            if not stats.check_file(member.size):
                errors.append(f"Skipped {member.name}: exceeds extraction limits")
                continue
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            with tf.extractfile(member) as src:
                if src is None:
                    continue
                with open(dest_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            
            os.chmod(dest_path, 0o644)
            stats.record_file(member.size)
            
            if classify_extension(os.path.splitext(dest_path)[1]) == 'archive':
                nested_archives.append(dest_path)


def _extract_rar(archive_path: str, dest_dir: str, stats: ExtractionStats,
                  errors: List[str], nested_archives: List[str]):
    """Extract a RAR archive safely."""
    try:
        import rarfile
        rarfile.UNRAR_TOOL = 'unrar-free'
    except ImportError:
        errors.append("RAR support not available (rarfile package missing)")
        return
    
    with rarfile.RarFile(archive_path, 'r') as rf:
        for info in rf.infolist():
            if stats.check_timeout():
                errors.append("Extraction timeout reached")
                break
            
            if info.is_dir():
                continue
            
            dest_path = _validate_archive_entry(info.filename, dest_dir)
            if not dest_path:
                continue
            
            if not stats.check_file(info.file_size):
                errors.append(f"Skipped {info.filename}: exceeds extraction limits")
                continue
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            with rf.open(info) as src, open(dest_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            
            os.chmod(dest_path, 0o644)
            stats.record_file(info.file_size)
            
            if classify_extension(os.path.splitext(dest_path)[1]) == 'archive':
                nested_archives.append(dest_path)


def _extract_7z(archive_path: str, dest_dir: str, stats: ExtractionStats,
                 errors: List[str], nested_archives: List[str]):
    """Extract a 7z archive safely."""
    try:
        import py7zr
    except ImportError:
        errors.append("7z support not available (py7zr package missing)")
        return
    
    with py7zr.SevenZipFile(archive_path, 'r') as sz:
        for name, bio in sz.read().items():
            if stats.check_timeout():
                errors.append("Extraction timeout reached")
                break
            
            if bio is None:
                continue
            
            dest_path = _validate_archive_entry(name, dest_dir)
            if not dest_path:
                continue
            
            data = bio.read()
            file_size = len(data)
            
            if not stats.check_file(file_size):
                errors.append(f"Skipped {name}: exceeds extraction limits")
                continue
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            with open(dest_path, 'wb') as dst:
                dst.write(data)
            
            os.chmod(dest_path, 0o644)
            stats.record_file(file_size)
            
            if classify_extension(os.path.splitext(dest_path)[1]) == 'archive':
                nested_archives.append(dest_path)


# ===========================================================================
# Video metadata via ffprobe
# ===========================================================================

def probe_video(file_path: str) -> Optional[Dict[str, Any]]:
    """Extract metadata from a video file using ffprobe.
    
    Returns dict with: duration, width, height, fps, codec, frame_count, file_size
    Returns None if ffprobe fails.
    """
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning(f"ffprobe failed for {file_path}: {result.stderr[:200]}")
            return None
        
        import json
        data = json.loads(result.stdout)
        
        # Find the video stream
        video_stream = None
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break
        
        if not video_stream:
            return None
        
        # Parse frame rate
        fps = 30.0  # default
        fps_str = video_stream.get('avg_frame_rate', '') or video_stream.get('r_frame_rate', '')
        if fps_str and '/' in fps_str:
            num, den = fps_str.split('/')
            if int(den) > 0:
                fps = round(int(num) / int(den), 2)
        
        fmt = data.get('format', {})
        duration = float(fmt.get('duration', 0))
        
        # Frame count
        frame_count = int(video_stream.get('nb_frames', 0))
        if frame_count == 0 and duration > 0 and fps > 0:
            frame_count = int(duration * fps)
        
        return {
            'duration': duration,
            'width': int(video_stream.get('width', 0)),
            'height': int(video_stream.get('height', 0)),
            'fps': fps,
            'codec': video_stream.get('codec_name', 'unknown'),
            'frame_count': frame_count,
            'file_size': os.path.getsize(file_path),
        }
    except Exception as e:
        logger.error(f"Error probing video {file_path}: {e}")
        return None


# ===========================================================================
# Content analysis
# ===========================================================================

def analyze_staging(session_id: str) -> Dict[str, Any]:
    """Analyze all files in a staging session.
    
    Returns structured analysis with images, videos, errors, and summary stats.
    """
    from .maintenance import extract_timestamp_from_file
    
    staging_dir = get_staging_dir(session_id)
    extracted_dir = os.path.join(staging_dir, 'extracted')
    raw_dir = os.path.join(staging_dir, 'raw')
    
    images = []
    videos = []
    errors = []
    
    # Walk both raw and extracted directories
    # First pass: collect all filenames per directory for thumbnail detection
    all_files_by_dir = {}
    for search_dir in [raw_dir, extracted_dir]:
        if not os.path.isdir(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            all_files_by_dir[root] = set(files)
    
    for search_dir in [raw_dir, extracted_dir]:
        if not os.path.isdir(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d != 'thumbs']
            dir_files = all_files_by_dir.get(root, set())
            for filename in files:
                file_path = os.path.join(root, filename)
                file_type = detect_file_type(file_path)
                
                if file_type == 'image':
                    # Skip .webp files that are thumbnail companions of a full-size image
                    base, ext = os.path.splitext(filename)
                    if ext.lower() == '.webp':
                        primary_exts = ['.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp']
                        has_primary = any((base + pe) in dir_files for pe in primary_exts)
                        if has_primary:
                            logger.debug(f"Skipping thumbnail companion: {filename}")
                            continue
                    
                    try:
                        ts = extract_timestamp_from_file(file_path)
                        images.append({
                            'file_path': file_path,
                            'file_name': filename,
                            'file_size': os.path.getsize(file_path),
                            'captured_at': to_iso(ts),
                        })
                    except Exception as e:
                        errors.append({
                            'file_name': filename,
                            'reason': f"Could not extract timestamp: {e}",
                        })
                
                elif file_type == 'video':
                    meta = probe_video(file_path)
                    if meta:
                        # Generate staging thumbnail
                        thumb_name = os.path.splitext(filename)[0] + '_thumb.jpg'
                        thumb_path = os.path.join(staging_dir, thumb_name)
                        _generate_staging_thumbnail(file_path, thumb_path)
                        
                        # Compute content hash for duplicate detection
                        file_hash = compute_file_hash(file_path)
                        
                        videos.append({
                            'file_path': file_path,
                            'file_name': filename,
                            'file_size': meta['file_size'],
                            'file_hash': file_hash,
                            'duration': meta['duration'],
                            'width': meta['width'],
                            'height': meta['height'],
                            'fps': meta['fps'],
                            'codec': meta['codec'],
                            'frame_count': meta['frame_count'],
                            'has_thumbnail': os.path.isfile(thumb_path),
                        })
                    else:
                        errors.append({
                            'file_name': filename,
                            'reason': "Could not read video metadata",
                        })
                
                elif file_type == 'unknown':
                    errors.append({
                        'file_name': filename,
                        'reason': "Unsupported file type",
                    })
    
    # Sort images by timestamp
    images.sort(key=lambda f: f['captured_at'])
    
    # Check for video duplicates
    video_duplicates = _check_video_duplicates(videos)
    
    total_image_size = sum(f['file_size'] for f in images)
    total_video_size = sum(f['file_size'] for f in videos)
    
    return {
        'session_id': session_id,
        'images': images,
        'image_count': len(images),
        'image_total_size': total_image_size,
        'image_first': images[0]['captured_at'] if images else None,
        'image_last': images[-1]['captured_at'] if images else None,
        'videos': videos,
        'video_count': len(videos),
        'video_total_size': total_video_size,
        'video_duplicates': video_duplicates,
        'errors': errors,
        'error_count': len(errors),
    }


def _check_video_duplicates(videos: List[Dict]) -> Dict[str, Any]:
    """Check if any videos already exist in the database.
    
    Uses a multi-tier check:
    1. SHA-256 hash match (definitive — same content)
    2. File size + duration match (strong signal — likely same file)
    Returns a dict of file_name -> duplicate info for any matches.
    """
    if not videos:
        return {}
    
    try:
        from ..database import get_db
        duplicates = {}
        with get_db() as conn:
            cursor = conn.cursor()
            for v in videos:
                file_hash = v.get('file_hash', '')
                
                # Tier 1: exact hash match (strongest)
                if file_hash:
                    cursor.execute(
                        "SELECT id, name FROM processed_videos WHERE file_hash = ?",
                        (file_hash,)
                    )
                    row = cursor.fetchone()
                    if row:
                        duplicates[v['file_name']] = {
                            'existing_id': row[0],
                            'existing_name': row[1],
                            'match_type': 'hash',
                        }
                        continue
                
                # Tier 2: size + duration match (very likely same file)
                duration = v.get('duration', 0)
                if v['file_size'] and duration:
                    cursor.execute(
                        "SELECT id, name FROM processed_videos WHERE file_size = ? AND ABS(duration_seconds - ?) < 0.5",
                        (v['file_size'], duration)
                    )
                    row = cursor.fetchone()
                    if row:
                        duplicates[v['file_name']] = {
                            'existing_id': row[0],
                            'existing_name': row[1],
                            'match_type': 'size_duration',
                        }
                        continue
        
        return duplicates
    except Exception as e:
        logger.warning(f"Duplicate check failed: {e}")
        return {}


# ===========================================================================
# Import execution
# ===========================================================================

def execute_image_import(
    session_id: str,
    images: List[Dict],
    job_name: str,
    stream_url: str = '',
    stream_type: str = 'rtsp',
    interval_seconds: int = 60,
) -> Dict[str, Any]:
    """Import images into a new job with proper folder structure.
    
    Returns dict with job_id, job_name, imported_count, total_size.
    """
    from ..database import get_db, dict_from_row
    
    if not images:
        raise ValueError("No images to import")
    
    now = to_iso(get_now())
    first_ts = images[0]['captured_at']
    last_ts = images[-1]['captured_at']
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO jobs (
                name, url, stream_type, start_datetime, end_datetime,
                interval_seconds, framerate, status, capture_path,
                naming_pattern, capture_count, storage_size, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 30, 'completed', ?, ?, 0, 0, ?, ?)
        """, (
            job_name, stream_url, stream_type, first_ts, last_ts,
            interval_seconds,
            '',  # placeholder capture_path
            config.DEFAULT_CAPTURE_PATTERN,
            now, now
        ))
        job_id = cursor.lastrowid
        
        # Create standard job directory
        job_dir = os.path.join(get_captures_path(), f"{job_id}_{job_name}")
        try:
            os.makedirs(job_dir, exist_ok=True)
            os.chmod(job_dir, 0o755)
        except Exception as e:
            cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            raise ValueError(f"Failed to create job directory: {e}")
        
        cursor.execute("UPDATE jobs SET capture_path = ? WHERE id = ?", (job_dir, job_id))
        
        # Move files into hierarchical structure
        moved_count = 0
        total_size = 0
        
        for img in images:
            src = img['file_path']
            ts = datetime.fromisoformat(img['captured_at'])
            
            date_dir = os.path.join(
                job_dir,
                str(ts.year),
                f"{ts.month:02d}",
                f"{ts.day:02d}",
                f"{ts.hour:02d}"
            )
            os.makedirs(date_dir, exist_ok=True)
            
            dest = os.path.join(date_dir, os.path.basename(src))
            
            # Handle collision
            if os.path.exists(dest):
                base, ext = os.path.splitext(os.path.basename(src))
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(date_dir, f"{base}_{counter}{ext}")
                    counter += 1
            
            try:
                shutil.move(src, dest)
                os.chmod(dest, 0o644)
            except Exception as e:
                logger.warning(f"Failed to move {src}: {e}")
                continue
            
            cursor.execute(
                "INSERT INTO captures (job_id, file_path, file_size, captured_at) VALUES (?, ?, ?, ?)",
                (job_id, dest, img['file_size'], img['captured_at'])
            )
            moved_count += 1
            total_size += img['file_size']
        
        if moved_count == 0:
            cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ValueError("Failed to move any images")
        
        cursor.execute(
            "UPDATE jobs SET capture_count = ?, storage_size = ? WHERE id = ?",
            (moved_count, total_size, job_id)
        )
        
        logger.info(f"Imported {moved_count} images as job '{job_name}' (ID: {job_id})")
        
        return {
            'job_id': job_id,
            'job_name': job_name,
            'imported_count': moved_count,
            'total_size': total_size,
        }


def execute_video_import(
    session_id: str,
    video: Dict,
    video_name: str,
    job_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Import a single video as a timelapse.
    
    Returns dict with video_id, name, file_path.
    """
    from ..database import get_db, dict_from_row
    
    src_path = video['file_path']
    if not os.path.exists(src_path):
        raise ValueError(f"Video file not found: {src_path}")
    
    # Determine destination directory
    if job_id:
        # Link to job — use job's timelapse folder
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Job {job_id} not found")
            job_name = row[0]
            sanitized = re.sub(r'[^\w\s-]', '', job_name).strip()
            video_dir = os.path.join(get_timelapses_path(), f"{job_id}_{sanitized}", video_name)
    else:
        # Standalone — use imported folder
        video_dir = os.path.join(get_timelapses_path(), 'imported', video_name)
    
    os.makedirs(video_dir, exist_ok=True)
    os.chmod(video_dir, 0o755)
    
    original_ext = os.path.splitext(video['file_name'])[1].lower() or '.mp4'
    dest_path = os.path.join(video_dir, f"{video_name}{original_ext}")
    
    # Handle collision
    if os.path.exists(dest_path):
        counter = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(video_dir, f"{video_name}_{counter}{original_ext}")
            counter += 1
    
    # Move the video
    shutil.move(src_path, dest_path)
    os.chmod(dest_path, 0o644)
    
    # Get metadata
    meta = probe_video(dest_path)
    if not meta:
        meta = video  # Fall back to pre-probed metadata
    
    now = to_iso(get_now())
    resolution = f"{meta.get('width', 0)}x{meta.get('height', 0)}"
    fps = int(meta.get('fps', 30))
    duration = meta.get('duration', 0)
    frame_count = meta.get('frame_count', 0)
    file_size = os.path.getsize(dest_path)
    file_hash = video.get('file_hash', '') or compute_file_hash(dest_path)
    
    # Determine job_name for DB
    db_job_name = 'Imported'
    if job_id:
        db_job_name = job_name
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO processed_videos (
                job_id, job_name, name, file_path, file_size, file_hash, resolution,
                framerate, quality, total_frames, duration_seconds,
                status, progress, build_source, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'high', ?, ?, 'completed', 100, 'imported', ?, ?)
        """, (
            job_id, db_job_name, video_name, dest_path, file_size, file_hash,
            resolution, fps, frame_count, duration,
            now, now
        ))
        video_id = cursor.lastrowid
    
    # Generate thumbnail
    _generate_video_thumbnail(video_id, dest_path)
    
    logger.info(f"Imported video '{video_name}' (ID: {video_id})")
    
    return {
        'video_id': video_id,
        'name': video_name,
        'file_path': dest_path,
        'duration': duration,
        'resolution': resolution,
    }


def _generate_video_thumbnail(video_id: int, video_path: str):
    """Generate thumbnail for an imported video."""
    thumb_path = os.path.splitext(video_path)[0] + "_thumb.jpg"
    try:
        cmd = [
            'ffmpeg', '-loglevel', 'error',
            '-i', video_path,
            '-ss', '0.5',
            '-frames:v', '1',
            '-q:v', '2',
            '-y', thumb_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0 and os.path.exists(thumb_path):
            os.chmod(thumb_path, 0o644)
            from ..database import get_db
            with get_db() as conn:
                conn.execute(
                    "UPDATE processed_videos SET thumbnail_path = ? WHERE id = ?",
                    (thumb_path, video_id)
                )
            logger.info(f"Generated thumbnail for imported video {video_id}")
        else:
            logger.warning(f"Thumbnail generation failed for video {video_id}: {result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Thumbnail error for video {video_id}: {e}")


def _generate_staging_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Generate a small thumbnail from a video for staging preview."""
    try:
        cmd = [
            'ffmpeg', '-loglevel', 'error',
            '-i', video_path,
            '-ss', '0.5',
            '-frames:v', '1',
            '-vf', 'scale=120:-1',
            '-q:v', '4',
            '-y', thumb_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0 and os.path.exists(thumb_path)
    except Exception:
        return False


# ===========================================================================
# Directory browsing (for server path panel)
# ===========================================================================

def browse_directory(path: str) -> List[Dict[str, Any]]:
    """List contents of a directory within the import path.
    
    Returns list of entries with: name, type, size, modified.
    Only lists single level (no recursion).
    Strictly jailed to the configured import path.
    """
    import_path = get_import_path()
    
    # Default to import path root
    if not path:
        path = import_path
    
    # Canonicalize and jail check
    real_path = validate_path_within(path, import_path)
    
    if not os.path.isdir(real_path):
        raise ValueError(f"Not a directory: {path}")
    
    entries = []
    try:
        for entry in sorted(os.scandir(real_path), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Skip hidden files
            if entry.name.startswith('.'):
                continue
            if entry.name in JUNK_NAMES:
                continue
            
            try:
                stat = entry.stat(follow_symlinks=False)
                
                # If symlink, verify target is within import path
                if entry.is_symlink():
                    try:
                        validate_path_within(os.path.realpath(entry.path), import_path)
                    except ValueError:
                        logger.info(f"Browse: skipping symlink escaping jail: {entry.name}")
                        continue
                
                if entry.is_dir(follow_symlinks=False):
                    entry_type = 'folder'
                    size = 0
                else:
                    ext = os.path.splitext(entry.name)[1].lower()
                    entry_type = classify_extension(ext)
                    size = stat.st_size
                
                entries.append({
                    'name': entry.name,
                    'type': entry_type,
                    'size': size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            except (PermissionError, OSError) as e:
                logger.debug(f"Browse: skipping {entry.name}: {e}")
    except PermissionError:
        raise ValueError(f"Permission denied: {path}")
    
    logger.info(f"Browse directory: {real_path} ({len(entries)} entries)")
    return entries

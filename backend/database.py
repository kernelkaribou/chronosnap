"""
Database models and initialization
"""
import sqlite3
from typing import Dict, Any
from contextlib import contextmanager
import secrets
import string
import logging
from . import config
from .utils import get_now, to_iso
from .helpers.db_helpers import ensure_column

logger = logging.getLogger(__name__)

@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_api_key(length: int = 32) -> str:
    """Generate a random alphanumeric API key"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _migrate_font_size_to_percent(cursor):
    """Migrate text overlay font_size from pixels (8-200) to percentage (1-20).
    Uses 1080p as reference: pct = px / 1080 * 100, clamped to 1-20."""
    import json
    migrated = 0
    # Migrate jobs.auto_build_text_overlay
    cursor.execute("SELECT id, auto_build_text_overlay FROM jobs WHERE auto_build_text_overlay IS NOT NULL")
    for row in cursor.fetchall():
        try:
            cfg = json.loads(row[1])
            if not isinstance(cfg, dict):
                continue
            fs = cfg.get('font_size')
            if isinstance(fs, (int, float)) and fs > 20:
                cfg['font_size'] = round(max(1, min(20, fs / 1080 * 100)), 1)
                cursor.execute("UPDATE jobs SET auto_build_text_overlay = ? WHERE id = ?",
                               (json.dumps(cfg), row[0]))
                migrated += 1
        except (json.JSONDecodeError, TypeError):
            pass
    # Migrate processed_videos.text_overlay
    cursor.execute("SELECT id, text_overlay FROM processed_videos WHERE text_overlay IS NOT NULL")
    for row in cursor.fetchall():
        try:
            cfg = json.loads(row[1])
            if not isinstance(cfg, dict):
                continue
            fs = cfg.get('font_size')
            if isinstance(fs, (int, float)) and fs > 20:
                cfg['font_size'] = round(max(1, min(20, fs / 1080 * 100)), 1)
                cursor.execute("UPDATE processed_videos SET text_overlay = ? WHERE id = ?",
                               (json.dumps(cfg), row[0]))
                migrated += 1
        except (json.JSONDecodeError, TypeError):
            pass
    if migrated:
        logger.info(f"Migration: converted {migrated} text overlay font_size values from pixels to percentage")


def init_db():
    """Initialize the database with required tables"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                stream_type TEXT NOT NULL,
                start_datetime TEXT NOT NULL,
                end_datetime TEXT,
                interval_seconds INTEGER NOT NULL,
                framerate INTEGER NOT NULL,
                status TEXT DEFAULT 'active',
                capture_path TEXT NOT NULL,
                naming_pattern TEXT NOT NULL,
                capture_count INTEGER DEFAULT 0,
                storage_size INTEGER DEFAULT 0,
                warning_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Captures table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
            )
        """)
        
        # Processed videos table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                resolution TEXT NOT NULL,
                framerate INTEGER NOT NULL,
                quality TEXT NOT NULL,
                start_capture_id INTEGER,
                end_capture_id INTEGER,
                start_time TEXT,
                end_time TEXT,
                total_frames INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,
                status TEXT DEFAULT 'processing',
                progress REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE SET NULL
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_captures_job_id ON captures(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_captures_captured_at ON captures(captured_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_job_id ON processed_videos(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        
        # Tags tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#6366f1',
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_tags (
                job_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (job_id, tag_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_tags (
                video_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (video_id, tag_id),
                FOREIGN KEY (video_id) REFERENCES processed_videos(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shared_links (
                token TEXT PRIMARY KEY NOT NULL,
                video_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES processed_videos(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_links_token ON shared_links(token)")
        
        # Migration: drop legacy columns from shared_links (expires_at, autoincrement id)
        cursor.execute("PRAGMA table_info(shared_links)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'expires_at' in cols:
            cursor.execute("""
                CREATE TABLE shared_links_new (
                    token TEXT PRIMARY KEY NOT NULL,
                    video_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES processed_videos(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("INSERT INTO shared_links_new (token, video_id, created_at) SELECT token, video_id, created_at FROM shared_links")
            cursor.execute("DROP TABLE shared_links")
            cursor.execute("ALTER TABLE shared_links_new RENAME TO shared_links")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_links_token ON shared_links(token)")
        
        # Migrations: jobs table columns
        ensure_column(cursor, 'jobs', 'warning_message', 'TEXT')
        ensure_column(cursor, 'jobs', 'time_window_enabled', 'INTEGER DEFAULT 0')
        ensure_column(cursor, 'jobs', 'time_window_start', 'TEXT')
        ensure_column(cursor, 'jobs', 'time_window_end', 'TEXT')
        ensure_column(cursor, 'jobs', 'next_scheduled_capture_at', 'TEXT')
        ensure_column(cursor, 'jobs', 'last_captured_at', 'TEXT')
        ensure_column(cursor, 'jobs', 'warning_threshold', 'INTEGER DEFAULT 3')
        ensure_column(cursor, 'jobs', 'auto_build_enabled', 'INTEGER DEFAULT 0')
        ensure_column(cursor, 'jobs', 'auto_build_interval_days', 'INTEGER DEFAULT 7')
        # Migration: rename interval_days to interval_hours
        cursor.execute("PRAGMA table_info(jobs)")
        job_cols = [col[1] for col in cursor.fetchall()]
        if 'auto_build_interval_hours' not in job_cols:
            cursor.execute("ALTER TABLE jobs ADD COLUMN auto_build_interval_hours INTEGER DEFAULT 168")
            cursor.execute("UPDATE jobs SET auto_build_interval_hours = auto_build_interval_days * 24 WHERE auto_build_interval_days IS NOT NULL")
            logger.info("Migration: added jobs.auto_build_interval_hours")
        ensure_column(cursor, 'jobs', 'auto_build_fps', 'INTEGER DEFAULT 30')
        ensure_column(cursor, 'jobs', 'auto_build_quality', "TEXT DEFAULT 'medium'")
        ensure_column(cursor, 'jobs', 'auto_build_resolution', "TEXT DEFAULT '1920x1080'")
        ensure_column(cursor, 'jobs', 'last_auto_build_at', 'TEXT')
        ensure_column(cursor, 'jobs', 'auto_build_in_progress', 'INTEGER DEFAULT 0')
        ensure_column(cursor, 'jobs', 'auto_build_text_overlay', 'TEXT')
        
        # Migrations: processed_videos table columns
        ensure_column(cursor, 'processed_videos', 'start_time', 'TEXT')
        ensure_column(cursor, 'processed_videos', 'end_time', 'TEXT')
        
        # Migration: Change processed_videos FK from CASCADE to SET NULL
        # SQLite doesn't support ALTER CONSTRAINT, so we need to recreate the table
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='processed_videos'")
        create_sql = cursor.fetchone()[0]
        if 'ON DELETE CASCADE' in create_sql and 'processed_videos' in create_sql:
            logger.info("Migrating processed_videos: changing ON DELETE CASCADE to ON DELETE SET NULL")
            # Disable FK enforcement during migration so videos referencing
            # deleted jobs are preserved (with their job_id set to NULL)
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("ALTER TABLE processed_videos RENAME TO _processed_videos_old")
            cursor.execute("""
                CREATE TABLE processed_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    resolution TEXT NOT NULL,
                    framerate INTEGER NOT NULL,
                    quality TEXT NOT NULL,
                    start_capture_id INTEGER,
                    end_capture_id INTEGER,
                    start_time TEXT,
                    end_time TEXT,
                    total_frames INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    status TEXT DEFAULT 'processing',
                    progress REAL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE SET NULL
                )
            """)
            # Nullify job_id references to deleted jobs during copy
            cursor.execute("""
                INSERT INTO processed_videos
                SELECT
                    id, CASE WHEN job_id IN (SELECT id FROM jobs) THEN job_id ELSE NULL END,
                    name, file_path, file_size, resolution, framerate, quality,
                    start_capture_id, end_capture_id, start_time, end_time,
                    total_frames, duration_seconds, status, progress,
                    created_at, completed_at
                FROM _processed_videos_old
            """)
            cursor.execute("DROP TABLE _processed_videos_old")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_job_id ON processed_videos(job_id)")
            cursor.execute("PRAGMA foreign_keys = ON")
            logger.info("Migration complete: processed_videos FK updated to SET NULL")
        
        # Migration: Add job_name column with backfill (special case)
        cursor.execute("PRAGMA table_info(processed_videos)")
        video_columns = [col[1] for col in cursor.fetchall()]
        if 'job_name' not in video_columns:
            cursor.execute("ALTER TABLE processed_videos ADD COLUMN job_name TEXT")
            cursor.execute("""
                UPDATE processed_videos SET job_name = (
                    SELECT j.name FROM jobs j WHERE j.id = processed_videos.job_id
                ) WHERE job_id IS NOT NULL AND job_name IS NULL
            """)
            logger.info("Migration: added processed_videos.job_name with backfill")
        
        # Remaining processed_videos columns
        ensure_column(cursor, 'processed_videos', 'thumbnail_path', 'TEXT')
        ensure_column(cursor, 'processed_videos', 'build_source', "TEXT DEFAULT 'manual'")
        ensure_column(cursor, 'processed_videos', 'file_hash', 'TEXT')
        ensure_column(cursor, 'processed_videos', 'error_message', 'TEXT')
        ensure_column(cursor, 'processed_videos', 'is_favorite', 'BOOLEAN DEFAULT 0')
        ensure_column(cursor, 'processed_videos', 'text_overlay', 'TEXT')
        
        # Migration: Convert text overlay font_size from pixels to percentage
        _migrate_font_size_to_percent(cursor)
        
        # Migrations: captures table columns
        ensure_column(cursor, 'captures', 'is_favorite', 'BOOLEAN DEFAULT 0')
        
        # Initialize API key if not exists
        cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
        if not cursor.fetchone():
            api_key = generate_api_key()
            now = to_iso(get_now())
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ('api_key', api_key, now)
            )
        
        conn.commit()


def dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite3.Row to a dictionary"""
    return dict(zip(row.keys(), row))

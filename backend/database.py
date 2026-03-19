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
                updated_at TEXT NOT NULL,
                time_window_enabled INTEGER DEFAULT 0,
                time_window_start TEXT,
                time_window_end TEXT,
                next_scheduled_capture_at TEXT,
                last_captured_at TEXT,
                warning_threshold INTEGER DEFAULT 3,
                auto_build_enabled INTEGER DEFAULT 0,
                auto_build_interval_hours INTEGER DEFAULT 168,
                auto_build_fps INTEGER DEFAULT 30,
                auto_build_quality TEXT DEFAULT 'medium',
                auto_build_resolution TEXT DEFAULT '1920x1080',
                last_auto_build_at TEXT,
                auto_build_in_progress INTEGER DEFAULT 0,
                auto_build_text_overlay TEXT,
                capture_quality TEXT DEFAULT 'maximum',
                capture_resolution TEXT DEFAULT 'native',
                source_width INTEGER,
                source_height INTEGER
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
                is_favorite BOOLEAN DEFAULT 0,
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
            )
        """)
        
        # Processed videos table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                job_name TEXT,
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
                thumbnail_path TEXT,
                build_source TEXT DEFAULT 'manual',
                file_hash TEXT,
                error_message TEXT,
                is_favorite BOOLEAN DEFAULT 0,
                text_overlay TEXT,
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

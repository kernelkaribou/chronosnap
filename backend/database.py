"""
Database models and initialization
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import secrets
import string
from . import config


@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_api_key(length: int = 16) -> str:
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
                job_id INTEGER NOT NULL,
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
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_captures_job_id ON captures(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_job_id ON processed_videos(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        
        # Migration: Add warning_message column if it doesn't exist
        cursor.execute("PRAGMA table_info(jobs)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'warning_message' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN warning_message TEXT")
        
        # Migration: Add time window columns if they don't exist
        if 'time_window_enabled' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN time_window_enabled INTEGER DEFAULT 0")
        if 'time_window_start' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN time_window_start TEXT")
        if 'time_window_end' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN time_window_end TEXT")
        
        # Migration: Add next_scheduled_capture_at column if it doesn't exist
        if 'next_scheduled_capture_at' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN next_scheduled_capture_at TEXT")
        
        # Migration: Add last_captured_at column if it doesn't exist
        if 'last_captured_at' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN last_captured_at TEXT")
        
        # Migration: Add start_time and end_time columns to processed_videos if they don't exist
        cursor.execute("PRAGMA table_info(processed_videos)")
        video_columns = [col[1] for col in cursor.fetchall()]
        if 'start_time' not in video_columns:
            cursor.execute("ALTER TABLE processed_videos ADD COLUMN start_time TEXT")
        if 'end_time' not in video_columns:
            cursor.execute("ALTER TABLE processed_videos ADD COLUMN end_time TEXT")
        
        # Initialize API key if not exists
        cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
        if not cursor.fetchone():
            api_key = generate_api_key()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ('api_key', api_key, now)
            )
        
        conn.commit()


def dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite3.Row to a dictionary"""
    return dict(zip(row.keys(), row))

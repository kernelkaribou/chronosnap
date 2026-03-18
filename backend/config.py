"""
Configuration management for the application
"""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent  # /app
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Database
DATABASE_PATH = str(DATA_DIR / "timelapse-manager.db")

# Default paths (hardcoded, customizable per job/video)
DEFAULT_CAPTURES_PATH = "/captures"
DEFAULT_VIDEOS_PATH = "/timelapses"
DEFAULT_IMPORT_PATH = "/imports"

# Import settings
IMPORT_STAGING_DIR = str(BASE_DIR / "import-staging")
MAX_UPLOAD_SIZE = 25 * 1024 * 1024 * 1024  # 25GB

# Default naming patterns
DEFAULT_CAPTURE_PATTERN = "{job_name}_{count}_{timestamp}"

# Server settings
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8080))

# Logging settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# FFMPEG settings
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", 30))

# Timezone Configuration
# The TZ environment variable determines the timezone for all datetime operations
# This includes:
# - Job scheduling and capture timing
# - Timestamp generation for filenames
# - Database timestamp storage (stored as ISO with timezone info)
# - API responses
# Set via docker-compose.yml environment variable, e.g., TZ=America/Chicago
# Default: UTC
TIMEZONE = os.getenv("TZ", "UTC")

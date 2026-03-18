"""
Main FastAPI application for Timelapse Manager
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import logging
import sys
import os

from .database import init_db
from .routers import jobs, captures, videos, settings, storage, tags, shared, devices
from .services.capture_scheduler import CaptureScheduler
from .auth import verify_api_key
from . import config


def get_app_version() -> str:
    """Read version from APP_VERSION env var, or VERSION file, or fallback."""
    env_ver = os.getenv("APP_VERSION", "").strip()
    if env_ver:
        return env_ver
    # Try reading VERSION file from project root
    for path in ["VERSION", "/app/VERSION"]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return "0.0.0"

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


class AccessLogFilter(logging.Filter):
    """Filter to suppress routine GET requests from access logs at INFO level"""
    def filter(self, record: logging.LogRecord) -> bool:
        # At INFO level, suppress routine GET requests
        if record.levelno == logging.INFO:
            message = record.getMessage()
            # Suppress GET requests to API endpoints for data loading
            if '"GET /api/' in message and any(endpoint in message for endpoint in [
                '/api/jobs',
                '/api/videos',
                '/api/captures',
                '/api/storage',

            ]):
                return False
            # Suppress static file requests, root path, and health checks
            if '"GET /static/' in message or '"GET / HTTP' in message or '"GET /health' in message:
                return False
        return True


# Apply filter to uvicorn access logger
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(AccessLogFilter())

# Global scheduler instance
scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global scheduler
    
    # Startup
    init_db()
    
    # Ensure required directories exist (use DB settings with config fallbacks)
    import os
    from .services.import_service import get_import_path
    for path_name, path_val in [("import", get_import_path()), ("staging", config.IMPORT_STAGING_DIR)]:
        try:
            os.makedirs(path_val, exist_ok=True)
        except PermissionError:
            logger.warning(f"Cannot create {path_name} directory: {path_val} (permission denied, skipping)")
    
    # Clean stale import staging directories (>2h old)
    from .services.import_service import cleanup_stale_staging
    cleanup_stale_staging()
    
    # Backfill thumbnails for existing videos
    from .services.video_processor import backfill_thumbnails
    backfill_thumbnails()
    
    scheduler = CaptureScheduler()
    scheduler.start()
    logger.info("Database initialized")
    logger.info(f"Capture scheduler started (Log Level: {config.LOG_LEVEL})")
    
    yield
    
    # Shutdown
    if scheduler:
        scheduler.stop()
        logger.info("Capture scheduler stopped")


app = FastAPI(
    title="Timelapse Manager",
    description="Configuration and management tool for timelapse videos",
    version=get_app_version(),
    lifespan=lifespan,
    redirect_slashes=False,
    redoc_url=None  # Disable ReDoc, use Swagger UI only
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import router (separate for upload size limit)
from .routers import import_router
app.include_router(
    import_router.router,
    prefix="/api/import",
    tags=["import"],
    dependencies=[Depends(verify_api_key)]
)

# Include routers
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(verify_api_key)])
app.include_router(captures.router, prefix="/api/captures", tags=["captures"], dependencies=[Depends(verify_api_key)])
app.include_router(videos.router, prefix="/api/videos", tags=["videos"], dependencies=[Depends(verify_api_key)])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"], dependencies=[Depends(verify_api_key)])
app.include_router(storage.router, prefix="/api/storage", tags=["storage"], dependencies=[Depends(verify_api_key)])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"], dependencies=[Depends(verify_api_key)])
app.include_router(devices.router, prefix="/api/devices", tags=["devices"], dependencies=[Depends(verify_api_key)])
app.include_router(shared.router, prefix="/api/shared", tags=["shared"], dependencies=[Depends(verify_api_key)])
from .routers import event_router
app.include_router(event_router.router, prefix="/api", tags=["events"], dependencies=[Depends(verify_api_key)])

# Public shared link routes — NO auth required
app.include_router(shared.public_router, prefix="/shared", tags=["shared-public"])

# Serve static files for frontend
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


@app.get("/")
async def read_root():
    """Serve the main frontend page with version-based cache busting"""
    from fastapi.responses import HTMLResponse
    with open("frontend/index.html") as f:
        html = f.read().replace("__APP_VERSION__", app.version)
    return HTMLResponse(html)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "scheduler": scheduler.is_running() if scheduler else False}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)

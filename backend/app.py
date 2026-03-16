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

from .database import init_db
from .routers import jobs, captures, videos, settings, storage, tags
from .services.capture_scheduler import CaptureScheduler
from .auth import verify_api_key
from . import config

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
    version="1.0.0",
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

# Include routers
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(verify_api_key)])
app.include_router(captures.router, prefix="/api/captures", tags=["captures"], dependencies=[Depends(verify_api_key)])
app.include_router(videos.router, prefix="/api/videos", tags=["videos"], dependencies=[Depends(verify_api_key)])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"], dependencies=[Depends(verify_api_key)])
app.include_router(storage.router, prefix="/api/storage", tags=["storage"], dependencies=[Depends(verify_api_key)])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"], dependencies=[Depends(verify_api_key)])

# Serve static files for frontend
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


@app.get("/")
async def read_root():
    """Serve the main frontend page"""
    return FileResponse("frontend/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "scheduler": scheduler.is_running() if scheduler else False}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)

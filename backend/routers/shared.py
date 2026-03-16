"""
Shared links — public (no auth) routes for viewing shared timelapses,
and API routes (auth-protected) for managing shared links.

Toggle model: one active link per video. Toggle ON creates a fresh token,
toggle OFF deletes the link. No expiry — links are active until disabled
or the video is deleted (CASCADE).
"""
import logging
import os
import re
import secrets
import string
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..database import get_db, dict_from_row

logger = logging.getLogger(__name__)

# ── Auth-protected API routes ──────────────────────────────────────────────

router = APIRouter()

TOKEN_LENGTH = 24
TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9]{24}$')
MAX_ACTIVE_SHARES = 50

# Allowed base directories for served video files
ALLOWED_VIDEO_DIRS = [
    os.path.realpath('/timelapses'),
    os.path.realpath('/app/data/timelapses'),
]

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; media-src 'self'",
    "Cache-Control": "no-store",
}


def generate_token() -> str:
    return ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def _validate_token_format(token: str):
    """Reject tokens that don't match the expected format before any DB query."""
    if not TOKEN_PATTERN.match(token):
        raise HTTPException(status_code=404, detail="Not found")


def _safe_file_path(file_path: str) -> str:
    """Validate file_path is within allowed directories to prevent path traversal."""
    real = os.path.realpath(file_path)
    if not any(real.startswith(d + os.sep) or real == d for d in ALLOWED_VIDEO_DIRS):
        raise HTTPException(status_code=404, detail="Not found")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="Not found")
    return real


class ToggleShareRequest(BaseModel):
    video_id: int
    enabled: bool


@router.get("/")
async def list_shared_links():
    """List all active shared links."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sl.token, sl.video_id, sl.created_at, pv.name as video_name
            FROM shared_links sl
            LEFT JOIN processed_videos pv ON sl.video_id = pv.id
            ORDER BY sl.created_at DESC
        """)
        return [dict_from_row(row) for row in cursor.fetchall()]


@router.post("/toggle")
async def toggle_share(body: ToggleShareRequest):
    """Toggle sharing on/off for a video. Returns the new share token or null."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM processed_videos WHERE id = ?", (body.video_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")

        if body.enabled:
            cursor.execute("SELECT COUNT(*) FROM shared_links")
            count = cursor.fetchone()[0]
            if count >= MAX_ACTIVE_SHARES:
                raise HTTPException(status_code=400, detail=f"Maximum of {MAX_ACTIVE_SHARES} active shares reached")
            cursor.execute("DELETE FROM shared_links WHERE video_id = ?", (body.video_id,))
            token = generate_token()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO shared_links (token, video_id, created_at) VALUES (?, ?, ?)",
                (token, body.video_id, now)
            )
            return {"enabled": True, "token": token}
        else:
            cursor.execute("DELETE FROM shared_links WHERE video_id = ?", (body.video_id,))
            return {"enabled": False, "token": None}


# ── Public routes (no auth) ───────────────────────────────────────────────

public_router = APIRouter()


def _resolve_shared_link(cursor, token: str):
    """Look up a shared link by token; raises 404 on failure with generic message."""
    _validate_token_format(token)
    cursor.execute(
        "SELECT sl.video_id, pv.file_path FROM shared_links sl "
        "LEFT JOIN processed_videos pv ON sl.video_id = pv.id "
        "WHERE sl.token = ?",
        (token,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    if not row["file_path"]:
        raise HTTPException(status_code=404, detail="Not found")

    return row


@public_router.get("/{token}")
async def shared_page(request: Request, token: str):
    """Serve the standalone shared video page (no auth)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        logger.info("Shared page accessed: video_id=%s ip=%s", row["video_id"], request.client.host if request.client else "unknown")

    with open("frontend/shared.html", "r") as f:
        html = f.read()
    return HTMLResponse(content=html, headers=SECURITY_HEADERS)


@public_router.get("/{token}/video")
async def shared_video_stream(request: Request, token: str):
    """Stream the shared video file (no auth)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        safe_path = _safe_file_path(row["file_path"])
        logger.info("Shared video streamed: video_id=%s ip=%s", row["video_id"], request.client.host if request.client else "unknown")
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename="timelapse.mp4",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )


@public_router.get("/{token}/download")
async def shared_video_download(request: Request, token: str):
    """Download the shared video file (no auth)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        safe_path = _safe_file_path(row["file_path"])
        logger.info("Shared video downloaded: video_id=%s ip=%s", row["video_id"], request.client.host if request.client else "unknown")
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename="timelapse.mp4",
            headers={
                "Content-Disposition": "attachment; filename=timelapse.mp4",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            }
        )

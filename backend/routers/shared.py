"""
Shared links — public (no auth) routes for viewing shared timelapses,
and API routes (auth-protected) for managing shared links.
"""
import os
import re
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..database import get_db, dict_from_row

# ── Auth-protected API routes ──────────────────────────────────────────────

router = APIRouter()

TOKEN_LENGTH = 24
TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9]{24}$')

# Allowed base directories for served video files
ALLOWED_VIDEO_DIRS = [
    os.path.realpath('/timelapses'),
    os.path.realpath('/app/data/timelapses'),
]


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
    """List all active shared links"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sl.*, pv.name as video_name
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
        cursor.execute("SELECT id, name FROM processed_videos WHERE id = ?", (body.video_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")

        if body.enabled:
            # Remove any existing link first, then create fresh
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


@router.delete("/{link_id}")
async def revoke_shared_link(link_id: int):
    """Revoke (delete) a shared link"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM shared_links WHERE id = ?", (link_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        return {"status": "revoked"}


# ── Public routes (no auth) ───────────────────────────────────────────────

public_router = APIRouter()


def _resolve_shared_link(cursor, token: str):
    """Look up a shared link by token; raises 404 on failure with generic message."""
    _validate_token_format(token)
    cursor.execute(
        "SELECT sl.*, pv.file_path FROM shared_links sl "
        "LEFT JOIN processed_videos pv ON sl.video_id = pv.id "
        "WHERE sl.token = ?",
        (token,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(status_code=404, detail="Not found")

    if not row["file_path"]:
        raise HTTPException(status_code=404, detail="Not found")

    return row


@public_router.get("/{token}")
async def shared_page(token: str):
    """Serve the standalone shared video page (no auth)"""
    # Validate the token exists and isn't expired before serving the page
    with get_db() as conn:
        cursor = conn.cursor()
        _resolve_shared_link(cursor, token)
    return FileResponse("frontend/shared.html")


@public_router.get("/{token}/video")
async def shared_video_stream(token: str):
    """Stream the shared video file (no auth)"""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        safe_path = _safe_file_path(row["file_path"])
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename="timelapse.mp4"
        )


@public_router.get("/{token}/download")
async def shared_video_download(token: str):
    """Download the shared video file (no auth)"""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        safe_path = _safe_file_path(row["file_path"])
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename="timelapse.mp4",
            headers={"Content-Disposition": "attachment; filename=timelapse.mp4"}
        )

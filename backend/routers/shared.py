"""
Shared links — public (no auth) routes for viewing shared timelapses,
and API routes (auth-protected) for managing shared links.
"""
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..database import get_db, dict_from_row

# ── Auth-protected API routes ──────────────────────────────────────────────

router = APIRouter()

TOKEN_LENGTH = 24
TOKEN_ALPHABET = string.ascii_letters + string.digits


def generate_token() -> str:
    return ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


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
            raise HTTPException(status_code=404, detail="Shared link not found")
        return {"status": "revoked"}


# ── Public routes (no auth) ───────────────────────────────────────────────

public_router = APIRouter()


def _resolve_shared_link(cursor, token: str):
    """Look up a shared link by token; raises 404 or 410 on failure."""
    cursor.execute(
        "SELECT sl.*, pv.file_path FROM shared_links sl "
        "LEFT JOIN processed_videos pv ON sl.video_id = pv.id "
        "WHERE sl.token = ?",
        (token,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Shared link not found")

    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(status_code=410, detail="This shared link has expired")

    if not row["file_path"]:
        raise HTTPException(status_code=404, detail="Video no longer available")

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
        return FileResponse(
            row["file_path"],
            media_type="video/mp4",
            filename=f"timelapse.mp4"
        )


@public_router.get("/{token}/download")
async def shared_video_download(token: str):
    """Download the shared video file (no auth)"""
    with get_db() as conn:
        cursor = conn.cursor()
        row = _resolve_shared_link(cursor, token)
        return FileResponse(
            row["file_path"],
            media_type="video/mp4",
            filename="timelapse.mp4",
            headers={"Content-Disposition": "attachment; filename=timelapse.mp4"}
        )

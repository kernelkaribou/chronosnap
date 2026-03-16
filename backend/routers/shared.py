"""
Shared links — public (no auth) routes for viewing shared timelapses,
and API routes (auth-protected) for managing shared links.
"""
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..database import get_db
from ..database import get_db, dict_from_row

# ── Auth-protected API routes ──────────────────────────────────────────────

router = APIRouter()

TOKEN_LENGTH = 24
TOKEN_ALPHABET = string.ascii_letters + string.digits


def generate_token() -> str:
    return ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


class CreateSharedLink(BaseModel):
    video_id: int
    expires_in_hours: Optional[int] = None  # None = never expires


class SharedLinkResponse(BaseModel):
    id: int
    token: str
    video_id: int
    video_name: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None
    url: Optional[str] = None


@router.get("/", response_model=List[SharedLinkResponse])
async def list_shared_links(video_id: Optional[int] = Query(None)):
    """List all shared links, optionally filtered by video_id"""
    with get_db() as conn:
        cursor = conn.cursor()
        if video_id is not None:
            cursor.execute("""
                SELECT sl.*, pv.name as video_name
                FROM shared_links sl
                LEFT JOIN processed_videos pv ON sl.video_id = pv.id
                WHERE sl.video_id = ?
                ORDER BY sl.created_at DESC
            """, (video_id,))
        else:
            cursor.execute("""
                SELECT sl.*, pv.name as video_name
                FROM shared_links sl
                LEFT JOIN processed_videos pv ON sl.video_id = pv.id
                ORDER BY sl.created_at DESC
            """)
        return [dict_from_row(row) for row in cursor.fetchall()]


@router.post("/", response_model=SharedLinkResponse)
async def create_shared_link(body: CreateSharedLink):
    """Create a new shared link for a video"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name FROM processed_videos WHERE id = ?",
            (body.video_id,)
        )
        video = cursor.fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        token = generate_token()
        now = datetime.now(timezone.utc).isoformat()
        expires_at = None
        if body.expires_in_hours:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
            ).isoformat()

        cursor.execute(
            "INSERT INTO shared_links (token, video_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, body.video_id, now, expires_at)
        )
        link_id = cursor.lastrowid
        return {
            "id": link_id,
            "token": token,
            "video_id": body.video_id,
            "video_name": video["name"],
            "created_at": now,
            "expires_at": expires_at,
        }


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

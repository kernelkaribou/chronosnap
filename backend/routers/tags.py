"""
Tags API endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import logging

from ..database import get_db
from ..utils import get_now, to_iso

router = APIRouter()
logger = logging.getLogger(__name__)


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")


class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class TagResponse(BaseModel):
    id: int
    name: str
    color: str
    created_at: str
    job_count: int = 0
    video_count: int = 0


class TagMergeRequest(BaseModel):
    target_tag_id: int


def get_tags_with_counts(conn, tag_id: int = None) -> list:
    """Fetch tags with usage counts, optionally filtered by ID."""
    where = "WHERE t.id = ?" if tag_id else ""
    params = (tag_id,) if tag_id else ()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT t.*,
            COALESCE(jc.cnt, 0) AS job_count,
            COALESCE(vc.cnt, 0) AS video_count
        FROM tags t
        LEFT JOIN (SELECT tag_id, COUNT(*) as cnt FROM job_tags GROUP BY tag_id) jc ON jc.tag_id = t.id
        LEFT JOIN (SELECT tag_id, COUNT(*) as cnt FROM video_tags GROUP BY tag_id) vc ON vc.tag_id = t.id
        {where}
        ORDER BY t.name
    """, params)
    return [dict(row) for row in cursor.fetchall()]


@router.get("/", response_model=List[TagResponse])
async def list_tags():
    """List all tags with usage counts"""
    with get_db() as conn:
        return get_tags_with_counts(conn)


@router.post("/", response_model=TagResponse, status_code=201)
async def create_tag(tag: TagCreate):
    """Create a new tag"""
    with get_db() as conn:
        cursor = conn.cursor()
        now = to_iso(get_now())
        try:
            cursor.execute(
                "INSERT INTO tags (name, color, created_at) VALUES (?, ?, ?)",
                (tag.name.strip(), tag.color, now)
            )
        except Exception:
            raise HTTPException(status_code=409, detail=f"Tag '{tag.name}' already exists")

        rows = get_tags_with_counts(conn, cursor.lastrowid)
        return rows[0]


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(tag_id: int, tag: TagUpdate):
    """Update a tag's name or color"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tags WHERE id = ?", (tag_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Tag not found")

        updates = {}
        if tag.name is not None:
            updates['name'] = tag.name.strip()
        if tag.color is not None:
            updates['color'] = tag.color
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        try:
            cursor.execute(
                f"UPDATE tags SET {set_clause} WHERE id = ?",
                (*updates.values(), tag_id)
            )
        except Exception:
            raise HTTPException(status_code=409, detail=f"Tag name already exists")

        rows = get_tags_with_counts(conn, tag_id)
        return rows[0]


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int):
    """Delete a tag (cascades from junction tables)"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tag not found")


@router.post("/{tag_id}/merge")
async def merge_tag(tag_id: int, request: TagMergeRequest):
    """Merge source tag into target tag, then delete source"""
    if tag_id == request.target_tag_id:
        raise HTTPException(status_code=400, detail="Cannot merge a tag into itself")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tags WHERE id IN (?, ?)", (tag_id, request.target_tag_id))
        found = [row[0] for row in cursor.fetchall()]
        if tag_id not in found:
            raise HTTPException(status_code=404, detail="Source tag not found")
        if request.target_tag_id not in found:
            raise HTTPException(status_code=404, detail="Target tag not found")

        # Move job_tags associations (ignore duplicates)
        cursor.execute("""
            INSERT OR IGNORE INTO job_tags (job_id, tag_id)
            SELECT job_id, ? FROM job_tags WHERE tag_id = ?
        """, (request.target_tag_id, tag_id))

        # Move video_tags associations (ignore duplicates)
        cursor.execute("""
            INSERT OR IGNORE INTO video_tags (video_id, tag_id)
            SELECT video_id, ? FROM video_tags WHERE tag_id = ?
        """, (request.target_tag_id, tag_id))

        # Delete source tag (cascades junction rows)
        cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

        rows = get_tags_with_counts(conn, request.target_tag_id)
        return rows[0]

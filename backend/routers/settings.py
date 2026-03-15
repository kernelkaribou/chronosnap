"""
Settings API endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from ..database import get_db, generate_api_key
from ..utils import get_now, to_iso

router = APIRouter()
logger = logging.getLogger(__name__)


class SettingsResponse(BaseModel):
    api_key: str
    updated_at: str


class RegenerateResponse(BaseModel):
    api_key: str
    message: str


@router.get("/api-key", response_model=SettingsResponse)
async def get_api_key():
    """Get the current API key"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value, updated_at FROM settings WHERE key = 'api_key'")
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="API key not found")
            
            return SettingsResponse(api_key=row[0], updated_at=row[1])
    except Exception as e:
        logger.error(f"Error retrieving API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve API key")


@router.post("/api-key/regenerate", response_model=RegenerateResponse)
async def regenerate_api_key():
    """Generate a new API key"""
    try:
        new_key = generate_api_key()
        now = to_iso(get_now())
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'api_key'",
                (new_key, now)
            )
            
            if cursor.rowcount == 0:
                # Insert if it doesn't exist
                cursor.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ('api_key', new_key, now)
                )
        
        logger.info("API key regenerated")
        return RegenerateResponse(
            api_key=new_key,
            message="API key successfully regenerated"
        )
    except Exception as e:
        logger.error(f"Error regenerating API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to regenerate API key")




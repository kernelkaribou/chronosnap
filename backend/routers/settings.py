"""
Settings API endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from ..database import get_db, generate_api_key
from ..utils import get_now, to_iso
from ..services.webhook import send_test_webhook, DEFAULT_PAYLOAD_TEMPLATE

router = APIRouter()
logger = logging.getLogger(__name__)


class SettingsResponse(BaseModel):
    api_key: str
    updated_at: str


class RegenerateResponse(BaseModel):
    api_key: str
    message: str


class WebhookSettings(BaseModel):
    webhook_enabled: bool = False
    webhook_url: str = ''
    webhook_failure_threshold: int = 3
    webhook_payload_template: str = DEFAULT_PAYLOAD_TEMPLATE


class WebhookTestRequest(BaseModel):
    url: str
    payload_template: str = DEFAULT_PAYLOAD_TEMPLATE


class WebhookTestResponse(BaseModel):
    success: bool
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


@router.get("/webhook", response_model=WebhookSettings)
async def get_webhook_settings():
    """Get webhook notification settings"""
    defaults = {
        'webhook_enabled': 'false',
        'webhook_url': '',
        'webhook_failure_threshold': '3',
        'webhook_payload_template': DEFAULT_PAYLOAD_TEMPLATE,
    }
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
                tuple(defaults.keys())
            )
            for row in cursor.fetchall():
                defaults[row[0]] = row[1]

        return WebhookSettings(
            webhook_enabled=defaults['webhook_enabled'] == 'true',
            webhook_url=defaults['webhook_url'],
            webhook_failure_threshold=int(defaults['webhook_failure_threshold']),
            webhook_payload_template=defaults['webhook_payload_template'],
        )
    except Exception as e:
        logger.error(f"Error retrieving webhook settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve webhook settings")


@router.put("/webhook", response_model=WebhookSettings)
async def update_webhook_settings(settings: WebhookSettings):
    """Update webhook notification settings"""
    try:
        now = to_iso(get_now())
        pairs = {
            'webhook_enabled': 'true' if settings.webhook_enabled else 'false',
            'webhook_url': settings.webhook_url,
            'webhook_failure_threshold': str(settings.webhook_failure_threshold),
            'webhook_payload_template': settings.webhook_payload_template,
        }
        with get_db() as conn:
            cursor = conn.cursor()
            for key, value in pairs.items():
                cursor.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
                    (key, value, now, value, now)
                )

        logger.info("Webhook settings updated")
        return settings
    except Exception as e:
        logger.error(f"Error updating webhook settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update webhook settings")


@router.post("/webhook/test", response_model=WebhookTestResponse)
async def test_webhook(request: WebhookTestRequest):
    """Send a test webhook notification"""
    if not request.url:
        return WebhookTestResponse(success=False, message="Webhook URL is required")

    success, message = send_test_webhook(request.url, request.payload_template)
    return WebhookTestResponse(success=success, message=message)


"""
Settings API endpoints
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List
from urllib.parse import urlparse
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
    webhook_payload_template: str = DEFAULT_PAYLOAD_TEMPLATE
    webhook_events: List[str] = []


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


@router.get("/version")
async def get_version(request: Request):
    """Get the application version and check for updates."""
    current = request.app.version
    result = {"version": current, "latest": None, "update_available": False}

    # Check GitHub for latest release (non-blocking, best-effort)
    try:
        import urllib.request
        import json

        req = urllib.request.Request(
            "https://api.github.com/repos/kernelkaribou/chronosnap/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "chronosnap"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest_tag = data.get("tag_name", "").lstrip("v")
            if latest_tag:
                result["latest"] = latest_tag
                result["update_available"] = _is_newer(latest_tag, current)
                result["release_url"] = data.get("html_url", "")
    except Exception:
        pass  # Network unavailable, no releases yet, etc.

    return result


def _is_newer(latest: str, current: str) -> bool:
    """Compare semver strings. Returns True if latest > current."""
    try:
        latest_parts = [int(x) for x in latest.split(".")]
        current_parts = [int(x) for x in current.split(".")]
        return latest_parts > current_parts
    except (ValueError, AttributeError):
        return False


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


@router.get("/naming-pattern")
async def get_naming_pattern():
    """Get default capture naming pattern."""
    from ..services.import_service import get_default_naming_pattern
    return {'naming_pattern': get_default_naming_pattern()}


@router.put("/naming-pattern")
async def update_naming_pattern(body: dict):
    """Update default capture naming pattern."""
    pattern = body.get('naming_pattern', '').strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="naming_pattern must not be empty")

    from ..helpers.template_vars import validate_naming_pattern
    err = validate_naming_pattern(pattern)
    if err:
        raise HTTPException(status_code=400, detail=err)

    try:
        now = to_iso(get_now())
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                ('default_naming_pattern', pattern, now)
            )
        logger.info(f"Default naming pattern updated to: {pattern}")
        return {'naming_pattern': pattern}
    except Exception as e:
        logger.error(f"Error updating naming pattern: {e}")
        raise HTTPException(status_code=500, detail="Failed to update naming pattern")


@router.get("/webhook", response_model=WebhookSettings)
async def get_webhook_settings():
    """Get webhook notification settings"""
    defaults = {
        'webhook_enabled': 'false',
        'webhook_url': '',
        'webhook_payload_template': DEFAULT_PAYLOAD_TEMPLATE,
        'webhook_events': '',
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

        events_str = defaults['webhook_events']
        events_list = [e.strip() for e in events_str.split(',') if e.strip()] if events_str else []

        return WebhookSettings(
            webhook_enabled=defaults['webhook_enabled'] == 'true',
            webhook_url=defaults['webhook_url'],
            webhook_payload_template=defaults['webhook_payload_template'],
            webhook_events=events_list,
        )
    except Exception as e:
        logger.error(f"Error retrieving webhook settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve webhook settings")


def _validate_webhook_url(url: str) -> bool:
    """Check that URL starts with http:// or https:// and has a hostname."""
    if not url.startswith(('http://', 'https://')):
        return False
    parsed = urlparse(url)
    return bool(parsed.hostname)


@router.put("/webhook", response_model=WebhookSettings)
async def update_webhook_settings(settings: WebhookSettings):
    """Update webhook notification settings"""
    if settings.webhook_url and not _validate_webhook_url(settings.webhook_url):
        raise HTTPException(status_code=400, detail="Webhook URL must be a valid http:// or https:// URL")

    try:
        now = to_iso(get_now())
        pairs = {
            'webhook_enabled': 'true' if settings.webhook_enabled else 'false',
            'webhook_url': settings.webhook_url,
            'webhook_payload_template': settings.webhook_payload_template,
            'webhook_events': ','.join(settings.webhook_events),
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

    if not _validate_webhook_url(request.url):
        return WebhookTestResponse(success=False, message="Webhook URL must be a valid http:// or https:// URL")

    success, message = send_test_webhook(request.url, request.payload_template)
    return WebhookTestResponse(success=success, message=message)


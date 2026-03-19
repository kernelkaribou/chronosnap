"""
API authentication middleware
"""
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import APIKeyHeader, APIKeyQuery
from typing import Optional
from urllib.parse import urlparse
import logging

from .database import get_db

logger = logging.getLogger(__name__)

# API key can be provided via header or query parameter
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


def get_stored_api_key() -> Optional[str]:
    """Retrieve the API key from the database"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Error retrieving API key: {e}")
        return None


def is_internal_request(request: Request) -> bool:
    """
    Check if the request is from the internal web UI (same-origin).
    Internal requests are exempt from API key validation.
    """
    referer = request.headers.get("referer", "")
    host = request.headers.get("host", "")
    
    if referer and host:
        parsed = urlparse(referer)
        # Extract host:port from the Referer and compare to the Host header exactly
        referer_host = parsed.hostname or ""
        referer_port = parsed.port
        referer_authority = f"{referer_host}:{referer_port}" if referer_port else referer_host
        if referer_authority == host or referer_host == host:
            return True
    
    # Check for localhost/127.0.0.1 connections (development)
    client_host = request.client.host if request.client else None
    if client_host in ["127.0.0.1", "localhost", "::1"]:
        if not referer:
            return True
        # If there's a referer from localhost, validate it matches host
        if host:
            parsed = urlparse(referer)
            referer_host = parsed.hostname or ""
            referer_port = parsed.port
            referer_authority = f"{referer_host}:{referer_port}" if referer_port else referer_host
            if referer_authority == host or referer_host == host:
                return True
    
    return False


async def verify_api_key(request: Request, api_key_header: Optional[str] = Depends(api_key_header), api_key_query: Optional[str] = Depends(api_key_query)):
    """
    Verify API key for external requests.
    Internal requests (from web UI) are automatically allowed.
    """
    # Check if this is an internal request
    if is_internal_request(request):
        return True
    
    # External request - require API key
    provided_key = api_key_header or api_key_query
    
    if not provided_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide via X-API-Key header or api_key query parameter."
        )
    
    # Verify the API key
    stored_key = get_stored_api_key()
    
    if not stored_key:
        logger.error("No API key configured in database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key not configured"
        )
    
    if provided_key != stored_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    return True

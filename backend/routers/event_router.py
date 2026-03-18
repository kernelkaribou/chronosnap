"""Event log API endpoints."""
from fastapi import APIRouter
from ..services.event_service import get_events

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/")
async def list_events():
    """Get the last 10 significant events (most recent first)."""
    return get_events()

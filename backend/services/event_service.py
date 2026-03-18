"""
Event log service - tracks significant operational events in a JSON file.
Events are ephemeral to the container lifecycle (stored in /tmp).
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

EVENTS_FILE = "/tmp/timelapse-events.json"
MAX_EVENTS = 12
_lock = threading.Lock()


def _read_events() -> List[Dict[str, Any]]:
    """Read events from file."""
    try:
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_events(events: List[Dict[str, Any]]):
    """Write events to file."""
    try:
        with open(EVENTS_FILE, 'w') as f:
            json.dump(events, f)
    except OSError as e:
        logger.warning(f"Failed to write events file: {e}")


def get_events() -> List[Dict[str, Any]]:
    """Get all stored events (most recent first)."""
    with _lock:
        return list(reversed(_read_events()))


def add_event(
    message: str,
    category: str = "info",
    metadata: Optional[Dict[str, Any]] = None
):
    """Add a significant event to the log.
    
    Categories: job, video, import, export, system
    """
    event = {
        "message": message,
        "category": category,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        event["metadata"] = metadata

    with _lock:
        events = _read_events()
        events.append(event)
        # Keep only last MAX_EVENTS
        if len(events) > MAX_EVENTS:
            events = events[-MAX_EVENTS:]
        _write_events(events)
    
    logger.debug(f"Event logged: [{category}] {message}")

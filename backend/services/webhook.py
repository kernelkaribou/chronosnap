"""
Webhook notification service for event-driven alerts
"""
import json
import logging
import threading
from typing import Optional

import urllib.request
import urllib.error

from ..database import get_db

logger = logging.getLogger(__name__)

# Default payload template using Home Assistant-friendly format
DEFAULT_PAYLOAD_TEMPLATE = '{"title": "{title}", "message": "{message}"}'

# Available template variables
TEMPLATE_VARIABLES = {
    '{job_name}': 'Name of the job',
    '{job_id}': 'ID of the job',
    '{event}': 'Event type (warning, completed, recovered)',
    '{failure_count}': 'Number of consecutive failures (warning events)',
    '{error_message}': 'Last error message (warning events)',
    '{title}': 'Auto-generated title',
    '{message}': 'Auto-generated message with details',
}


def _get_webhook_settings() -> dict:
    """Load webhook settings from database."""
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
            rows = cursor.fetchall()
            for row in rows:
                defaults[row[0]] = row[1]
    except Exception as e:
        logger.error(f"Failed to load webhook settings: {e}")
    return defaults


def send_webhook_event(event: str, job_name: str, job_id: int,
                       failure_count: int = 0, error_message: str = ''):
    """Send a webhook notification in a background thread."""
    thread = threading.Thread(
        target=_send_webhook_event_sync,
        args=(event, job_name, job_id, failure_count, error_message),
        daemon=True
    )
    thread.start()


def _send_webhook_event_sync(event: str, job_name: str, job_id: int,
                             failure_count: int, error_message: str):
    """Synchronously send a webhook notification."""
    settings = _get_webhook_settings()

    if settings['webhook_enabled'] != 'true' or not settings['webhook_url']:
        return

    # Check event filter — empty means all events enabled
    allowed_events = settings.get('webhook_events', '')
    if allowed_events:
        allowed = [e.strip() for e in allowed_events.split(',')]
        if event not in allowed:
            return

    # Generate title and message based on event type
    if event == 'warning':
        title = f"Capture Alert: {job_name}"
        message = f"{job_name} has failed {failure_count} consecutive captures. Last error: {error_message}"
    elif event == 'completed':
        title = f"Job Completed: {job_name}"
        message = f"{job_name} has completed its scheduled capture period."
    elif event == 'recovered':
        title = f"Job Recovered: {job_name}"
        message = f"{job_name} has recovered after previous capture failures."
    elif event == 'auto_build_complete':
        title = f"Auto-Build Complete: {job_name}"
        message = f"{job_name} has finished building an automatic timelapse video."
    else:
        title = f"ChronoSnap: {job_name}"
        message = f"Event '{event}' for {job_name}."

    template = settings['webhook_payload_template'] or DEFAULT_PAYLOAD_TEMPLATE

    # Replace template variables
    payload_str = template.replace('{job_name}', _json_safe(job_name))
    payload_str = payload_str.replace('{job_id}', str(job_id))
    payload_str = payload_str.replace('{event}', _json_safe(event))
    payload_str = payload_str.replace('{failure_count}', str(failure_count))
    payload_str = payload_str.replace('{error_message}', _json_safe(error_message))
    payload_str = payload_str.replace('{title}', _json_safe(title))
    payload_str = payload_str.replace('{message}', _json_safe(message))

    try:
        json.loads(payload_str)
    except json.JSONDecodeError:
        logger.error(f"Webhook payload template produced invalid JSON: {payload_str}")
        return

    try:
        req = urllib.request.Request(
            settings['webhook_url'],
            data=payload_str.encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Webhook [{event}] sent for job {job_id} ({job_name}): HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        logger.error(f"Webhook [{event}] failed for job {job_id}: HTTP {e.code} - {e.reason}")
    except Exception as e:
        logger.error(f"Webhook [{event}] failed for job {job_id}: {e}")


def send_test_webhook(url: str, template: str) -> tuple[bool, str]:
    """Send a test webhook notification. Returns (success, message)."""
    title = "Test Alert: ChronoSnap"
    message = "This is a test notification from ChronoSnap webhook configuration."

    payload_str = template.replace('{job_name}', 'Test Job')
    payload_str = payload_str.replace('{job_id}', '0')
    payload_str = payload_str.replace('{event}', 'test')
    payload_str = payload_str.replace('{failure_count}', '3')
    payload_str = payload_str.replace('{error_message}', 'This is a test error message')
    payload_str = payload_str.replace('{title}', _json_safe(title))
    payload_str = payload_str.replace('{message}', _json_safe(message))

    try:
        json.loads(payload_str)
    except json.JSONDecodeError:
        return False, "Payload template produces invalid JSON"

    try:
        req = urllib.request.Request(
            url,
            data=payload_str.encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"Webhook sent successfully (HTTP {resp.status})"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


def _json_safe(value: str) -> str:
    """Escape a string for safe insertion into a JSON template."""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

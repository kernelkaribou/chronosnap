"""
Webhook notification service for capture health alerts
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
    '{failure_count}': 'Number of consecutive failures',
    '{error_message}': 'Last error message',
    '{title}': 'Auto-generated title',
    '{message}': 'Auto-generated message with details',
}


def _get_webhook_settings() -> dict:
    """Load webhook settings from database."""
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
            rows = cursor.fetchall()
            for row in rows:
                defaults[row[0]] = row[1]
    except Exception as e:
        logger.error(f"Failed to load webhook settings: {e}")
    return defaults


def get_failure_threshold() -> int:
    """Get the configured failure threshold."""
    settings = _get_webhook_settings()
    try:
        return int(settings['webhook_failure_threshold'])
    except (ValueError, KeyError):
        return 3


def send_webhook_alert(job_name: str, job_id: int, failure_count: int, error_message: str):
    """Send a webhook alert in a background thread to avoid blocking the scheduler."""
    thread = threading.Thread(
        target=_send_webhook_alert_sync,
        args=(job_name, job_id, failure_count, error_message),
        daemon=True
    )
    thread.start()


def _send_webhook_alert_sync(job_name: str, job_id: int, failure_count: int, error_message: str):
    """Synchronously send a webhook alert."""
    settings = _get_webhook_settings()

    if settings['webhook_enabled'] != 'true' or not settings['webhook_url']:
        return

    title = f"Capture Alert: {job_name}"
    message = f"Job \"{job_name}\" (ID: {job_id}) has failed {failure_count} consecutive captures. Last error: {error_message}"

    template = settings['webhook_payload_template'] or DEFAULT_PAYLOAD_TEMPLATE

    # Replace template variables
    payload_str = template.replace('{job_name}', _json_safe(job_name))
    payload_str = payload_str.replace('{job_id}', str(job_id))
    payload_str = payload_str.replace('{failure_count}', str(failure_count))
    payload_str = payload_str.replace('{error_message}', _json_safe(error_message))
    payload_str = payload_str.replace('{title}', _json_safe(title))
    payload_str = payload_str.replace('{message}', _json_safe(message))

    try:
        # Validate JSON
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
            logger.info(f"Webhook alert sent for job {job_id} ({job_name}): HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        logger.error(f"Webhook alert failed for job {job_id}: HTTP {e.code} - {e.reason}")
    except Exception as e:
        logger.error(f"Webhook alert failed for job {job_id}: {e}")


def send_test_webhook(url: str, template: str) -> tuple[bool, str]:
    """Send a test webhook notification. Returns (success, message)."""
    title = "Test Alert: TimeLapse-Manager"
    message = "This is a test notification from TimeLapse-Manager webhook configuration."

    payload_str = template.replace('{job_name}', 'Test Job')
    payload_str = payload_str.replace('{job_id}', '0')
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

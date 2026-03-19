"""Shared template variable definitions for naming patterns and text overlays.

Single source of truth for all template variables. Both capture naming
patterns and timelapse text overlays import from here.
"""

# ---------------------------------------------------------------------------
# Variable definitions:  (token_name, description)
# Each list is ordered for display in the UI hint text.
# ---------------------------------------------------------------------------

NAMING_PATTERN_VARS = [
    ('job_name',  'Job name'),
    ('count',     'Zero-padded capture count'),
    ('timestamp', 'Compact datetime (YYYYmmdd_HHMMSS)'),
    ('month',     'Month (01-12)'),
    ('day',       'Day of month (01-31)'),
    ('hour',      'Hour, 24-hour (00-23)'),
]

TEXT_OVERLAY_VARS = [
    ('job_name',     'Job name'),
    ('date',         'Date (YYYY-MM-DD)'),
    ('time',         'Time (HH:MM:SS)'),
    ('datetime',     'Full date and time'),
    ('month',        'Month (01-12)'),
    ('day',          'Day of month (01-31)'),
    ('hour',         'Hour, 24-hour (00-23)'),
    ('frame',        'Current frame number'),
    ('total_frames', 'Total frame count'),
]

# Overlay variables that change per-frame (used to decide if re-render is needed)
DYNAMIC_OVERLAY_VARS = [
    'date', 'time', 'datetime', 'month', 'day', 'hour', 'frame', 'total_frames',
]


def build_datetime_vars(dt):
    """Build all datetime-derived template variables from a datetime object.

    Returns a dict usable by both naming patterns and text overlays.
    Callers can pass the full dict to str.format(); unused keys are ignored.
    """
    return {
        'timestamp': dt.strftime('%Y%m%d_%H%M%S'),
        'date':      dt.strftime('%Y-%m-%d'),
        'time':      dt.strftime('%H:%M:%S'),
        'datetime':  dt.strftime('%Y-%m-%d %H:%M:%S'),
        'month':     dt.strftime('%m'),
        'day':       dt.strftime('%d'),
        'hour':      dt.strftime('%H'),
    }


_DATETIME_KEYS = ('timestamp', 'date', 'time', 'datetime', 'month', 'day', 'hour')


def empty_datetime_vars():
    """Return empty strings for all datetime-derived variables (fallback)."""
    return {k: '' for k in _DATETIME_KEYS}

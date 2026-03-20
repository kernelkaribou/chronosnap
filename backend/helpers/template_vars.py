"""Shared template variable definitions for naming patterns and text overlays.

Single source of truth for all template variables. Both capture naming
patterns and timelapse text overlays import from here.
"""

# ---------------------------------------------------------------------------
# Master variable list:  (token_name, description, context)
#   context: 'both'    = available in naming patterns AND text overlays
#            'naming'  = capture naming patterns only
#            'overlay' = text overlays only
# ---------------------------------------------------------------------------

TEMPLATE_VARS = [
    ('job_name',     'Job name',                           'both'),
    ('count',        'Zero-padded capture count',          'naming'),
    ('timestamp',    'Compact datetime (YYYYmmdd_HHMMSS)', 'both'),
    ('date',         'Date (YYYY-MM-DD)',                  'both'),
    ('time',         'Time (HH:MM:SS)',                    'both'),
    ('datetime',     'Full date and time',                 'both'),
    ('month',        'Month (01-12)',                      'both'),
    ('day',          'Day of month (01-31)',               'both'),
    ('hour',         'Hour, 24-hour (00-23)',              'both'),
    ('frame',        'Current frame number',               'overlay'),
    ('total_frames', 'Total frame count',                  'overlay'),
]

# Derived per-context lists
NAMING_PATTERN_VARS = [(n, d) for n, d, c in TEMPLATE_VARS if c in ('both', 'naming')]
TEXT_OVERLAY_VARS   = [(n, d) for n, d, c in TEMPLATE_VARS if c in ('both', 'overlay')]

# Overlay variables that change per-frame (used to decide if re-render is needed)
DYNAMIC_OVERLAY_VARS = [
    'date', 'time', 'datetime', 'timestamp', 'month', 'day', 'hour',
    'frame', 'total_frames',
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

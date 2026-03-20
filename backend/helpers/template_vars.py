"""Shared template variable definitions for naming patterns and text overlays.

Single source of truth for all template variables. Both capture naming
patterns and timelapse text overlays import from here.
"""

import re

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
    ('time',         'Time (HH:MM:SS)',                    'overlay'),
    ('datetime',     'Full date and time',                 'overlay'),
    ('month',        'Month (01-12)',                      'both'),
    ('day',          'Day of month (01-31)',               'both'),
    ('hour',         'Hour, 24-hour (00-23)',              'both'),
    ('minute',       'Minute (00-59)',                     'both'),
    ('second',       'Second (00-59)',                     'both'),
    ('frame',        'Current frame number',               'overlay'),
    ('total_frames', 'Total frame count',                  'overlay'),
]

# Derived per-context lists
NAMING_PATTERN_VARS = [(n, d) for n, d, c in TEMPLATE_VARS if c in ('both', 'naming')]
TEXT_OVERLAY_VARS   = [(n, d) for n, d, c in TEMPLATE_VARS if c in ('both', 'overlay')]

# Overlay variables that change per-frame (used to decide if re-render is needed)
DYNAMIC_OVERLAY_VARS = [
    'date', 'time', 'datetime', 'timestamp', 'month', 'day', 'hour',
    'minute', 'second', 'frame', 'total_frames',
]

# ---------------------------------------------------------------------------
# Naming pattern validation
# ---------------------------------------------------------------------------

# Characters that are unsafe across Windows, macOS, Linux, NFS, SMB
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Valid variable tokens for naming patterns (includes legacy 'num' alias)
_NAMING_VAR_NAMES = {n for n, _, c in TEMPLATE_VARS if c in ('both', 'naming')} | {'num'}

# Matches {var_name} tokens in a pattern
_TOKEN_RE = re.compile(r'\{([^}]+)\}')

# Allowed literal characters: alphanumeric, underscore, hyphen, dot, space
_ALLOWED_LITERAL_RE = re.compile(r'^[A-Za-z0-9 _\-\.{}]+$')


def validate_naming_pattern(pattern: str) -> str | None:
    """Validate a naming pattern for filesystem safety.

    Returns None if valid, or an error message string if invalid.
    """
    if not pattern or not pattern.strip():
        return "Naming pattern must not be empty"

    if len(pattern) > 200:
        return "Naming pattern must be 200 characters or fewer"

    # Check for filesystem-unsafe characters
    # First strip out {var} tokens, then check the remaining literal text
    literal = _TOKEN_RE.sub('', pattern)
    if _UNSAFE_FILENAME_CHARS.search(literal):
        return 'Pattern contains characters not allowed in filenames: < > : " / \\ | ? *'

    if not _ALLOWED_LITERAL_RE.match(pattern):
        bad = set(re.findall(r'[^A-Za-z0-9 _\-\.{}]', literal))
        if bad:
            return f"Pattern contains disallowed characters: {' '.join(sorted(bad))}"

    # Validate that all {tokens} are recognised variable names
    tokens = _TOKEN_RE.findall(pattern)
    # Allow legacy {num:06d} style
    for tok in tokens:
        name = tok.split(':')[0]
        if name not in _NAMING_VAR_NAMES:
            valid = ', '.join(f'{{{n}}}' for n in sorted(_NAMING_VAR_NAMES))
            return f"Unknown variable {{{tok}}}. Valid variables: {valid}"

    return None


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
        'minute':    dt.strftime('%M'),
        'second':    dt.strftime('%S'),
    }


_DATETIME_KEYS = (
    'timestamp', 'date', 'time', 'datetime',
    'month', 'day', 'hour', 'minute', 'second',
)


def empty_datetime_vars():
    """Return empty strings for all datetime-derived variables (fallback)."""
    return {k: '' for k in _DATETIME_KEYS}

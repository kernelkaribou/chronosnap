"""Reusable helpers for handling stream/webhook URLs that may embed
credentials (e.g. rtsp://user:pass@host/...) — never log or surface those
credentials verbatim outside the request that configured them."""
from urllib.parse import urlparse, urlunparse


def redact_url_credentials(url: str) -> str:
    """Return `url` with any embedded userinfo (user:pass@) replaced by
    `***@`. Returns the URL unchanged if it has no credentials, or the
    literal string '(redacted)' if the URL can't be parsed at all (fails
    safe — never risk leaking an unparsed credential-bearing string)."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***@{parsed.hostname}" if parsed.hostname else "***"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "(redacted)"


def scrub_credentials_from_text(text: str, url: str) -> str:
    """Strip `url`'s embedded credentials out of unrelated free-form text
    (e.g. ffmpeg stderr, which sometimes echoes the input URL verbatim on
    connection failure) so a failure log can't leak them even when the
    credential-bearing string appears somewhere other than the URL we
    control directly."""
    if not text or not url:
        return text
    try:
        parsed = urlparse(url)
        if not (parsed.username or parsed.password):
            return text
        userinfo = url.split('://', 1)[-1].split('@', 1)[0]
        if userinfo and f"{userinfo}@" in text:
            text = text.replace(f"{userinfo}@", "***@")
    except Exception:
        pass
    return text

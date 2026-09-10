"""
Unit tests for the "fix soon" audit item: camera/stream credentials
leaking into logs.

Two leak vectors were fixed:
  1. The /jobs/test-url endpoint accepted the stream URL as a query
     parameter on a POST request, so it landed in the request line/query
     string (and therefore in access logs). Now a JSON body (TestUrlRequest).
  2. image_capture.py logged raw stream URLs and raw ffmpeg stderr (which
     can echo the URL back) on capture failure -- URLs may embed inline
     login credentials in their userinfo component. Now redacted via
     backend.helpers.url_helpers.
"""
import sys
import logging
import subprocess
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, "/app")

from backend.helpers.url_helpers import redact_url_credentials, scrub_credentials_from_text  # noqa: E402
from backend.models import TestUrlRequest as UrlTestRequest  # noqa: E402 (aliased: avoid pytest misidentifying a Test*-prefixed Pydantic model as a test class)
from backend.services import image_capture  # noqa: E402


# ---------------------------------------------------------------------------
# redact_url_credentials()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("rtsp://admin:supersecret@192.168.1.50:554/stream1", "rtsp://***@192.168.1.50:554/stream1"),
    ("http://cam-user:pw123@10.0.0.5/mjpeg", "http://***@10.0.0.5/mjpeg"),
    ("rtsp://admin:supersecret@camera.local/stream1", "rtsp://***@camera.local/stream1"),
    # No credentials -- must be returned unchanged
    ("rtsp://192.168.1.50:554/stream1", "rtsp://192.168.1.50:554/stream1"),
    ("http://camera.local/mjpeg", "http://camera.local/mjpeg"),
    ("", ""),
])
def test_redact_url_credentials(url, expected):
    assert redact_url_credentials(url) == expected


def test_redact_url_credentials_never_leaves_password_substring():
    url = "rtsp://admin:supersecret@192.168.1.50:554/stream1"
    redacted = redact_url_credentials(url)
    assert "supersecret" not in redacted
    assert "admin" not in redacted


def test_redact_url_credentials_username_only_no_password():
    url = "rtsp://admin@192.168.1.50:554/stream1"
    redacted = redact_url_credentials(url)
    assert "admin" not in redacted
    assert redacted == "rtsp://***@192.168.1.50:554/stream1"


def test_redact_url_credentials_handles_unparseable_input_safely():
    # Something url-shaped enough to have survived earlier validation but
    # that urlparse chokes on -- must fail safe, never echo raw input back.
    weird = "http://[::not-valid-ipv6/foo"
    result = redact_url_credentials(weird)
    assert result == "(redacted)" or "not-valid-ipv6" not in result


# ---------------------------------------------------------------------------
# scrub_credentials_from_text()
# ---------------------------------------------------------------------------

def test_scrub_credentials_from_text_removes_echoed_userinfo():
    url = "rtsp://admin:supersecret@192.168.1.50:554/stream1"
    stderr_text = "Connection to rtsp://admin:supersecret@192.168.1.50:554/stream1 failed: timed out"
    scrubbed = scrub_credentials_from_text(stderr_text, url)
    assert "supersecret" not in scrubbed
    assert "admin:supersecret@" not in scrubbed
    assert "192.168.1.50:554/stream1" in scrubbed  # host/path preserved for debugging


def test_scrub_credentials_from_text_no_credentials_in_url_is_noop():
    url = "rtsp://192.168.1.50:554/stream1"
    text = "Connection refused"
    assert scrub_credentials_from_text(text, url) == text


def test_scrub_credentials_from_text_handles_empty_inputs():
    assert scrub_credentials_from_text("", "rtsp://admin:pw@host/x") == ""
    assert scrub_credentials_from_text("some text", "") == "some text"


# ---------------------------------------------------------------------------
# image_capture.py: failure-path logging must never contain credentials
# ---------------------------------------------------------------------------

def _mock_failed_subprocess(stderr_text: bytes):
    result = MagicMock()
    result.returncode = 1
    result.stderr = stderr_text
    return result


def test_capture_rtsp_failure_log_does_not_leak_credentials(caplog):
    url = "rtsp://admin:supersecret@192.168.1.50:554/stream1"
    stderr = f"Connection to {url} failed: Connection refused".encode()
    with patch("subprocess.run", return_value=_mock_failed_subprocess(stderr)):
        with caplog.at_level(logging.ERROR):
            success, _ = image_capture._capture_rtsp(url, "/tmp/out.jpg")
    assert success is False
    assert "supersecret" not in caplog.text
    assert "admin:supersecret@" not in caplog.text
    # Host should still be present so the log is actually useful for debugging
    assert "192.168.1.50" in caplog.text


def test_capture_rtsp_timeout_log_does_not_leak_credentials(caplog):
    url = "rtsp://admin:supersecret@192.168.1.50:554/stream1"
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)):
        with caplog.at_level(logging.ERROR):
            success, _ = image_capture._capture_rtsp(url, "/tmp/out.jpg")
    assert success is False
    assert "supersecret" not in caplog.text
    assert "admin:supersecret@" not in caplog.text


def test_capture_http_failure_log_does_not_leak_credentials(caplog):
    url = "http://cam-user:pw123@10.0.0.5/mjpeg"
    stderr = f"Failed to open {url}: 401 Unauthorized".encode()
    with patch("subprocess.run", return_value=_mock_failed_subprocess(stderr)):
        with caplog.at_level(logging.ERROR):
            success, _ = image_capture._capture_http(url, "/tmp/out.jpg")
    assert success is False
    assert "pw123" not in caplog.text
    assert "cam-user:pw123@" not in caplog.text
    assert "10.0.0.5" in caplog.text


def test_capture_http_timeout_log_does_not_leak_credentials(caplog):
    url = "http://cam-user:pw123@10.0.0.5/mjpeg"
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)):
        with caplog.at_level(logging.ERROR):
            success, _ = image_capture._capture_http(url, "/tmp/out.jpg")
    assert success is False
    assert "pw123" not in caplog.text
    assert "cam-user:pw123@" not in caplog.text


def test_capture_rtsp_no_credentials_log_unaffected(caplog):
    """Sanity check: the redaction logic must not mangle logs for the
    (much more common) case of a URL with no embedded credentials."""
    url = "rtsp://192.168.1.50:554/stream1"
    stderr = b"Connection refused"
    with patch("subprocess.run", return_value=_mock_failed_subprocess(stderr)):
        with caplog.at_level(logging.ERROR):
            image_capture._capture_rtsp(url, "/tmp/out.jpg")
    assert "Connection refused" in caplog.text


# ---------------------------------------------------------------------------
# /jobs/test-url: request shape must be a JSON body, not query params
# ---------------------------------------------------------------------------

def test_test_url_request_is_a_pydantic_body_model():
    """A single Pydantic BaseModel parameter is bound by FastAPI as the
    JSON request body, not query params -- this is what actually closes the
    access-log leak vector (the URL can no longer appear in the request
    line). Guard against a future regression back to individual query
    params by asserting the route's only parameter is this body model."""
    import inspect
    from backend.routers.jobs import test_url

    sig = inspect.signature(test_url)
    params = list(sig.parameters.values())
    assert len(params) == 1, f"expected exactly one body-model parameter, got: {params}"
    assert params[0].annotation is UrlTestRequest


def test_test_url_route_accepts_body_shaped_request():
    """Functional check: calling the real route handler with a UrlTestRequest
    (the shape FastAPI will construct from a JSON body) still works end to
    end, with test_stream_url mocked out."""
    import asyncio
    from backend.routers import jobs as jobs_router
    from backend.models import TestUrlResponse

    fake_response = TestUrlResponse(success=True, message="ok")

    async def fake_test_stream_url(url, stream_type, quality, resolution):
        assert url == "http://camera.local/snapshot.jpg"
        assert stream_type == "http"
        assert quality == "high"
        assert resolution == "native"
        return fake_response

    request = UrlTestRequest(url="http://camera.local/snapshot.jpg", stream_type="http", quality="high")
    with patch.object(jobs_router, "test_stream_url", fake_test_stream_url):
        result = asyncio.run(jobs_router.test_url(request))
    assert result is fake_response


def test_test_url_request_rejects_invalid_quality():
    with pytest.raises(Exception):
        UrlTestRequest(url="http://camera.local/snapshot.jpg", quality="ultra-max")

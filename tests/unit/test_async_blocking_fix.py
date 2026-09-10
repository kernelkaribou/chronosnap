"""
Regression tests for: blocking sync work running directly inside async route
handlers, stalling the asyncio event loop for every other concurrent
request/poll.

Two call sites were fixed:
  - POST /jobs/test-url         -> services/url_tester.py's test_stream_url()
  - POST /settings/webhook/test -> routers/settings.py's test_webhook()

Both now offload their blocking work (subprocess.run / urllib.request.urlopen)
to a worker thread via asyncio.to_thread(), so the single-worker event loop
stays responsive while a stream test or webhook test is in flight.

The core technique: run the route handler concurrently with a "heartbeat"
coroutine that ticks every 10ms. If the route handler blocks the event loop,
the heartbeat cannot make progress while it runs; if it's properly offloaded,
the heartbeat keeps ticking throughout.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.routers import settings as settings_router  # noqa: E402
from backend.routers.settings import WebhookTestRequest  # noqa: E402
from backend.services import url_tester  # noqa: E402


async def _run_with_heartbeat(coro):
    """Run `coro` concurrently with a heartbeat coroutine; return
    (result, heartbeat_tick_count)."""
    ticks = []

    async def heartbeat():
        for _ in range(40):
            await asyncio.sleep(0.005)
            ticks.append(time.monotonic())

    hb_task = asyncio.create_task(heartbeat())
    result = await coro
    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass
    return result, len(ticks)


# ---------------------------------------------------------------------------
# POST /settings/webhook/test
# ---------------------------------------------------------------------------

def test_webhook_test_route_does_not_block_event_loop():
    """A slow (blocking) send_test_webhook() must not stall the event loop --
    the heartbeat coroutine should keep ticking throughout the call."""

    def slow_blocking_send(url, template):
        time.sleep(0.2)
        return True, "Webhook sent successfully (HTTP 200)"

    request = WebhookTestRequest(url="http://example.com/hook", payload_template="{}")

    with patch.object(settings_router, "send_test_webhook", slow_blocking_send):
        result, tick_count = asyncio.run(_run_with_heartbeat(settings_router.test_webhook(request)))

    assert result.success is True
    # A blocked event loop would produce ~0 ticks during the 0.2s sleep;
    # a properly-offloaded call lets most/all of the 40 ticks land.
    assert tick_count >= 25, f"event loop appears to have stalled (only {tick_count} heartbeat ticks)"


def test_webhook_test_route_rejects_invalid_url_without_calling_send():
    """Invalid URLs should short-circuit before ever touching the (now
    thread-offloaded) blocking call."""
    request = WebhookTestRequest(url="not-a-url", payload_template="{}")

    with patch.object(settings_router, "send_test_webhook") as mock_send:
        result = asyncio.run(settings_router.test_webhook(request))

    mock_send.assert_not_called()
    assert result.success is False


def test_webhook_test_route_still_returns_failure_message_on_failure():
    """Functional correctness: a failed send still propagates its message."""
    def failing_send(url, template):
        return False, "Connection refused"

    request = WebhookTestRequest(url="http://example.com/hook", payload_template="{}")

    with patch.object(settings_router, "send_test_webhook", failing_send):
        result = asyncio.run(settings_router.test_webhook(request))

    assert result.success is False
    assert result.message == "Connection refused"


# ---------------------------------------------------------------------------
# POST /jobs/test-url  (services/url_tester.py's test_stream_url)
# ---------------------------------------------------------------------------

def test_stream_url_does_not_block_event_loop():
    """A slow (blocking) _test_stream_url_sync() must not stall the event
    loop -- the heartbeat coroutine should keep ticking throughout."""
    from backend.models import TestUrlResponse

    def slow_blocking_capture(url, stream_type, quality, resolution):
        time.sleep(0.2)
        return TestUrlResponse(success=True, message="ok")

    with patch.object(url_tester, "_test_stream_url_sync", slow_blocking_capture):
        result, tick_count = asyncio.run(
            _run_with_heartbeat(url_tester.test_stream_url("http://camera.local/snap.jpg"))
        )

    assert result.success is True
    assert tick_count >= 25, f"event loop appears to have stalled (only {tick_count} heartbeat ticks)"


def test_stream_url_async_wrapper_delegates_all_args_to_sync_impl():
    """Functional correctness: the async wrapper must pass every argument
    through to the sync implementation unchanged."""
    from backend.models import TestUrlResponse

    captured_args = {}

    def fake_sync(url, stream_type, quality, resolution):
        captured_args.update(url=url, stream_type=stream_type, quality=quality, resolution=resolution)
        return TestUrlResponse(success=True, message="ok")

    with patch.object(url_tester, "_test_stream_url_sync", fake_sync):
        result = asyncio.run(
            url_tester.test_stream_url("rtsp://cam/stream", stream_type="rtsp", quality="high", resolution="1280x720")
        )

    assert captured_args == {
        "url": "rtsp://cam/stream",
        "stream_type": "rtsp",
        "quality": "high",
        "resolution": "1280x720",
    }
    assert result.success is True


def test_stream_url_sync_impl_still_works_end_to_end_on_bad_url():
    """The renamed sync implementation still behaves correctly when called
    directly (e.g. with an unreachable URL, which fails fast without a real
    network/ffmpeg dependency being required for the test to make sense --
    this only checks that the function executes and returns a well-formed
    failure response, not that ffmpeg itself is invoked correctly)."""
    result = url_tester._test_stream_url_sync("/dev/video99", stream_type="device")
    assert result.success is False
    assert "not found" in result.message.lower()


def test_stream_url_sync_impl_redacts_credentials_from_generic_exception_log(caplog):
    """The generic Exception handler's log line must not leak URL
    credentials (tightly coupled to the earlier credential-redaction fix --
    this code path was missed there since it's a distinct route)."""
    url = "rtsp://admin:s3cr3t@camera.local/stream1"

    with patch("backend.services.url_tester.tempfile.NamedTemporaryFile", side_effect=OSError(f"boom near {url}")):
        with caplog.at_level(logging.ERROR):
            result = url_tester._test_stream_url_sync(url, stream_type="rtsp")

    assert result.success is False
    assert "s3cr3t" not in caplog.text
    assert "admin:s3cr3t@" not in caplog.text
    assert "camera.local" in caplog.text  # host retained for debuggability

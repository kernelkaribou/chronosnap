"""
Webhook delivery: the runner spins up a tiny stdlib HTTP listener on the
Compose network and points POST /api/settings/webhook/test at it — fully
local, no external service dependency for this scenario. Note: manual
capture/video-build actions don't trigger webhooks in the real app (only the
scheduler and auto-builder do) — this uses the dedicated test-send endpoint,
which sends synchronously and doesn't require any job/event to exist.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LISTEN_PORT = 9105


class _CapturingHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.received.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence default stderr logging
        pass


def run(ctx):
    client = ctx["client"]
    _CapturingHandler.received = []

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = client.post(
            "/api/settings/webhook/test",
            json_body={"url": f"http://test-runner:{LISTEN_PORT}/hook"},
            expect=200,
        )
        assert body.get("success") is True, f"webhook test-send reported failure: {body}"

        deadline = time.time() + 10
        while not _CapturingHandler.received and time.time() < deadline:
            time.sleep(0.2)
        assert _CapturingHandler.received, "listener never received the webhook POST"

        payload = json.loads(_CapturingHandler.received[0])
        assert "ChronoSnap" in payload.get("title", ""), f"unexpected payload: {payload}"
    finally:
        server.shutdown()
        thread.join(timeout=5)

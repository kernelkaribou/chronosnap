"""
Shared helpers for the ChronoSnap E2E test suite. Standard library only — no
new dependencies (see .github/instructions/testing.instructions.md).
"""
import json
import sqlite3
import time
from urllib import request as urlrequest, error as urlerror


class ApiClient:
    """Minimal JSON HTTP client for talking to the app under test."""

    def __init__(self, base_url, api_key=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(self, method, path, json_body=None, headers=None, expect=None, raw=False, timeout=30):
        url = self.base_url + path
        data = None
        hdrs = {}
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if self.api_key:
            hdrs["X-API-Key"] = self.api_key
        if headers:
            hdrs.update(headers)
        req = urlrequest.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body = resp.read()
        except urlerror.HTTPError as e:
            status = e.code
            body = e.read()
        if expect is not None and status != expect:
            raise AssertionError(
                f"{method} {path} -> expected {expect}, got {status}: {body[:500]!r}"
            )
        if raw:
            return status, body
        if not body:
            return status, None
        try:
            return status, json.loads(body)
        except ValueError:
            return status, body

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, json_body=None, **kw):
        return self.request("POST", path, json_body=json_body, **kw)

    def put(self, path, json_body=None, **kw):
        return self.request("PUT", path, json_body=json_body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


def wait_for_health(base_url, timeout=60):
    """Poll /health until it returns 200, or raise after `timeout` seconds."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urlrequest.urlopen(base_url + "/health", timeout=5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read())
        except Exception as e:  # noqa: BLE001 - broad on purpose, we retry regardless
            last_err = e
        time.sleep(1)
    raise RuntimeError(f"App never became healthy within {timeout}s: {last_err}")


def get_api_key(db_path, timeout=30):
    """
    Read the auto-generated API key directly from the shared SQLite DB
    volume. The connection itself is opened with the `mode=ro` URI flag
    (SQLITE_OPEN_READONLY), so no application data can be written from here
    — but the volume mount must NOT be read-only: WAL mode needs to open the
    `-shm` index file in read-write mode even for a read-only connection
    (confirmed by testing; a `:ro` mount fails with "unable to open database
    file"). See docker-compose.test.yml's `chronosnap` data volume mount.
    """
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                cur = conn.cursor()
                cur.execute("SELECT value FROM settings WHERE key = 'api_key'")
                row = cur.fetchone()
                if row and row[0]:
                    return row[0]
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(1)
    raise RuntimeError(f"Could not read API key from {db_path}: {last_err}")

"""
/api/* requires an API key unless the request looks same-origin/localhost.
Traffic from this test-runner container arrives at the app over the Compose
network (not literally 127.0.0.1) and sends no Referer, so it exercises the
real auth boundary, not the same-origin bypass.
"""


def run(ctx):
    unauth = ctx["unauthenticated_client"]
    status, body = unauth.get("/api/jobs/")
    assert status == 401, f"expected 401 without an API key, got {status}: {body}"

    client = ctx["client"]
    status, body = client.get("/api/jobs/", expect=200)
    assert isinstance(body, list), f"expected a list of jobs, got: {body}"

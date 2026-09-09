"""GET /health requires no auth and reports app + scheduler status."""


def run(ctx):
    client = ctx["unauthenticated_client"]
    status, body = client.get("/health", expect=200)
    assert body.get("status") == "healthy", f"unexpected health body: {body}"
    assert "scheduler" in body, f"missing scheduler field: {body}"

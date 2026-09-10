"""Synchronous GIF generation from the completed video (two-pass FFmpeg palette)."""


def run(ctx):
    client = ctx["client"]
    video_id = ctx["video_id"]

    status, gif_bytes = client.get(f"/api/videos/{video_id}/gif", raw=True, expect=200, timeout=60)
    assert len(gif_bytes) > 100, "downloaded GIF is suspiciously small"
    assert gif_bytes[:6] in (b"GIF87a", b"GIF89a"), (
        f"missing GIF signature in downloaded file header: {gif_bytes[:6]!r}"
    )

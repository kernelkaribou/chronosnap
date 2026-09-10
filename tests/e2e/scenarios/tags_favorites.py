"""Tag creation + assignment, and favorite toggling, on the built video."""


def run(ctx):
    client = ctx["client"]
    video_id = ctx["video_id"]

    status, tag = client.post(
        "/api/tags/", json_body={"name": "e2e-test-tag", "color": "#00ff00"}, expect=201
    )
    tag_id = tag["id"]
    ctx["tag_id"] = tag_id

    status, body = client.put(
        f"/api/videos/{video_id}/tags", json_body={"tag_ids": [tag_id]}, expect=200
    )

    status, video = client.get(f"/api/videos/{video_id}", expect=200)
    tag_names = [t["name"] for t in video.get("tags", [])]
    assert "e2e-test-tag" in tag_names, f"tag not reflected on video: {video}"

    status, body = client.post(
        "/api/videos/favorite", json_body={"ids": [video_id], "is_favorite": True}, expect=200
    )
    assert body.get("updated") == 1, f"expected 1 video updated, got: {body}"

    status, video = client.get(f"/api/videos/{video_id}", expect=200)
    assert video.get("is_favorite") is True, f"favorite flag not reflected: {video}"

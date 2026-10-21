"""Random draft batch (library -> random unused asset -> AI -> draft). AI is faked; nothing leaves the machine."""

from pathlib import Path

from app.services.claude_client import GenerationError

FIX = Path(__file__).parent / "fixtures"
URL = "/api/v1/content/generate-random"
IG = {"platform": "instagram"}


def upload(client, name="tiny.png", tags="", data=None, mime="image/png"):
    body = data if data is not None else (FIX / name).read_bytes()
    r = client.post("/api/v1/assets/upload", files={"file": (name, body, mime)}, data={"tags": tags})
    assert r.status_code == 201, r.text
    return r.json()


def two_images(client, tags_png="halloween"):
    return upload(client, "tiny.png", tags_png), upload(client, "tiny.gif", "", mime="image/gif")


def test_picks_only_unused_assets(client, fake_claude):
    a, b = two_images(client)
    client.post("/api/v1/posts", json=dict(profile_id=1, platform="instagram", content_type="feed_post",
                                           campaign="lifestyle", caption="x", asset_id=a["id"]))
    r = client.post(URL, json={**IG, "count": 1})
    assert r.status_code == 201, r.text
    assert [p["media_asset_id"] for p in r.json()["posts"]] == [b["id"]]


def test_count_larger_than_pool_creates_what_exists(client, fake_claude):
    a, b = two_images(client)
    r = client.post(URL, json={**IG, "count": 5}).json()
    assert sorted(p["media_asset_id"] for p in r["posts"]) == sorted([a["id"], b["id"]])
    assert r["available"] == 2 and all(p["status"] == "draft" for p in r["posts"])
    assert len(fake_claude.last_images) == 1  # the AI was shown the asset


def test_no_eligible_assets_is_409(client, fake_claude):
    r = client.post(URL, json=IG)
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_assets"
    assert fake_claude.calls == 0


def test_dropbox_links_and_placeholder_videos_are_skipped(client, fake_claude):
    client.post("/api/v1/assets", json=dict(filename="All videos", media_type="video", storage_type="external_url",
                                            source_ref="https://www.dropbox.com/scl/fo/abc/xyz"))
    tiny_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12  # 20 bytes: a failed-upload placeholder
    upload(client, "clip.mp4", data=tiny_mp4, mime="video/mp4")
    r = client.post(URL, json=IG)
    assert r.status_code == 409 and fake_claude.calls == 0


def test_content_type_follows_media_and_platform(client, fake_claude):
    upload(client, "tiny.png")
    ig = client.post(URL, json=IG).json()["posts"][0]
    tt = client.post(URL, json={"platform": "tiktok", "include_used": True}).json()["posts"][0]
    assert (ig["content_type"], tt["content_type"]) == ("feed_post", "photo_post")


def test_auto_campaign_uses_asset_category_else_lifestyle(client, fake_claude):
    a, b = two_images(client)  # png tagged halloween, gif untagged
    posts = client.post(URL, json={**IG, "count": 2}).json()["posts"]
    by_asset = {p["media_asset_id"]: p["campaign"] for p in posts}
    assert by_asset[a["id"]] == "halloween" and by_asset[b["id"]] == "lifestyle"


def test_explicit_campaign_and_include_used(client, fake_claude):
    upload(client, "tiny.png")
    client.post(URL, json={**IG, "campaign": "playful"})
    assert client.post(URL, json=IG).status_code == 409  # now used
    r = client.post(URL, json={**IG, "include_used": True, "campaign": "playful"})
    assert r.status_code == 201 and r.json()["posts"][0]["campaign"] == "playful"


def test_resubmitted_batch_does_not_duplicate(client, fake_claude):
    two_images(client)
    first = client.post(URL, json={**IG, "batch_key": "abc"}).json()["posts"][0]["id"]
    again = client.post(URL, json={**IG, "batch_key": "abc"}).json()["posts"][0]["id"]
    assert first == again and fake_claude.calls == 1
    assert client.get("/api/v1/posts").json()["total"] == 1


def test_provider_error_with_nothing_created_surfaces_code(client, fake_claude):
    upload(client, "tiny.png")
    fake_claude.error = GenerationError("rate_limited", "slow down", 429)
    r = client.post(URL, json=IG)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_error_midway_keeps_earlier_drafts(client, fake_claude):
    two_images(client)
    real = fake_claude.generate
    state = {"n": 0}

    def flaky(system, user, images=None):
        state["n"] += 1
        if state["n"] == 2:
            raise GenerationError("timeout", "too slow", 504)
        return real(system, user, images)

    fake_claude.generate = flaky
    r = client.post(URL, json={**IG, "count": 2})
    assert r.status_code == 201
    body = r.json()
    assert len(body["posts"]) == 1 and body["errors"][0]["code"] == "timeout"


def test_invalid_count_rejected(client, fake_claude):
    assert client.post(URL, json={**IG, "count": 6}).status_code == 422
    assert client.post(URL, json={"platform": "x"}).status_code == 422


def test_dashboard_random_button_creates_draft_and_redirects(client, fake_claude):
    upload(client, "tiny.png")
    page = client.get("/generate").text
    assert "Surprise me" in page and "/generate/random" in page
    r = client.post("/generate/random", data={"platform": "instagram", "campaign": "auto", "count": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/posts/")


def test_dashboard_random_with_no_assets_shows_message(client, fake_claude):
    r = client.post("/generate/random", data={"platform": "tiktok", "count": "1"}, follow_redirects=True)
    assert r.status_code == 200 and "No matching unused" in r.text

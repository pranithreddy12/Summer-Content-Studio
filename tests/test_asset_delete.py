"""Permanent asset deletion: uploads/links yes, synced Dropbox files never, in-use needs force."""

from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def upload(client, name="tiny.png", mime="image/png"):
    r = client.post("/api/v1/assets/upload", files={"file": (name, (FIX / name).read_bytes(), mime)})
    assert r.status_code == 201, r.text
    return r.json()


def link(client):
    r = client.post("/api/v1/assets", json=dict(filename="All videos", media_type="video", storage_type="external_url",
                                                source_ref="https://www.dropbox.com/scl/fo/abc/xyz"))
    assert r.status_code == 201, r.text
    return r.json()


def use(client, asset_id, status_after=None):
    p = client.post("/api/v1/posts", json=dict(profile_id=1, platform="instagram", content_type="feed_post",
                                               campaign="lifestyle", caption="keep me", asset_id=asset_id)).json()
    if status_after == "archive":
        client.post(f"/api/v1/posts/{p['id']}/archive")
    return p


def test_default_delete_still_archives(client):
    a = upload(client)
    r = client.delete(f"/api/v1/assets/{a['id']}")
    assert r.status_code == 200 and r.json()["is_active"] is False


def test_permanent_delete_removes_row_and_uploaded_file(client, settings):
    a = upload(client)
    files_before = list(Path(settings.MEDIA_UPLOAD_DIR).glob("*.png"))
    assert files_before
    r = client.delete(f"/api/v1/assets/{a['id']}?permanent=true")
    assert r.status_code == 200 and r.json()["deleted"] == a["id"] and r.json()["file_removed"] is True
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 404
    assert len(list(Path(settings.MEDIA_UPLOAD_DIR).glob("*.png"))) == len(files_before) - 1


def test_dropbox_link_can_be_deleted_no_file(client):
    a = link(client)
    r = client.delete(f"/api/v1/assets/{a['id']}?permanent=true")
    assert r.status_code == 200 and r.json()["file_removed"] is False
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 404


def test_unknown_asset_is_404(client):
    assert client.delete("/api/v1/assets/9999?permanent=true").status_code == 404


def test_in_use_requires_force_then_post_survives(client):
    a = upload(client)
    p = use(client, a["id"])
    r = client.delete(f"/api/v1/assets/{a['id']}?permanent=true")
    assert r.status_code == 409 and r.json()["error"]["code"] == "asset_in_use"
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 200  # untouched

    ok = client.delete(f"/api/v1/assets/{a['id']}?permanent=true&force=true")
    assert ok.status_code == 200 and ok.json()["posts_detached"] == 1
    kept = client.get(f"/api/v1/posts/{p['id']}").json()
    assert kept["caption"] == "keep me" and kept["media_asset_id"] is None and kept["status"] == "draft"


def test_asset_used_only_by_archived_posts_deletes_without_force(client):
    a = link(client)
    p = use(client, a["id"], status_after="archive")
    assert client.delete(f"/api/v1/assets/{a['id']}?permanent=true").status_code == 200
    assert client.get(f"/api/v1/posts/{p['id']}").json()["media_asset_id"] is None


def test_synced_dropbox_file_is_never_deleted(client, settings):
    f = Path(settings.MEDIA_SYNC_DIR) / "synced-keep.png"
    f.write_bytes((FIX / "tiny.png").read_bytes())
    a = client.post("/api/v1/assets", json=dict(filename="synced-keep.png", media_type="image",
                                                storage_type="local_sync", local_relpath="synced-keep.png"))
    assert a.status_code == 201, a.text
    r = client.delete(f"/api/v1/assets/{a.json()['id']}?permanent=true&force=true")
    assert r.status_code == 409 and r.json()["error"]["code"] == "synced_asset"
    assert f.exists()  # the Dropbox-side file is untouched
    # archiving is the supported way to hide it
    assert client.delete(f"/api/v1/assets/{a.json()['id']}").json()["is_active"] is False


def test_dashboard_delete_button_and_redirect(client):
    a = upload(client)
    page = client.get(f"/assets/{a['id']}").text
    assert "Delete permanently" in page and f"/assets/{a['id']}/delete" in page
    r = client.post(f"/assets/{a['id']}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/assets?")
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 404


def test_dashboard_in_use_asset_sends_force_and_synced_has_no_delete(client, settings):
    a = upload(client)
    use(client, a["id"])
    assert 'name="force"' in client.get(f"/assets/{a['id']}").text
    r = client.post(f"/assets/{a['id']}/delete", data={"force": "true"}, follow_redirects=False)
    assert r.status_code == 303 and client.get(f"/api/v1/assets/{a['id']}").status_code == 404

    f = Path(settings.MEDIA_SYNC_DIR) / "s2.png"
    f.write_bytes((FIX / "tiny.gif").read_bytes())
    s = client.post("/api/v1/assets", json=dict(filename="s2.png", media_type="image", storage_type="local_sync",
                                                local_relpath="s2.png")).json()
    assert "Delete permanently" not in client.get(f"/assets/{s['id']}").text


def test_dashboard_delete_without_force_on_used_asset_shows_error(client):
    a = upload(client)
    use(client, a["id"])
    r = client.post(f"/assets/{a['id']}/delete", follow_redirects=True)
    assert r.status_code == 200 and "used by 1 post" in r.text
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 200

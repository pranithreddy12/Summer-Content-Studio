"""Dropbox folder link: pull random unused media on demand, never twice, purge after posting. Fake Dropbox only."""

import json
from pathlib import Path

import httpx

from app import platforms
from app.config import get_settings
from app.db import session_scope
from app.models import ContentPost, MediaAsset
from app.services import dropbox_link

FIX = Path(__file__).parent / "fixtures"
URL = "/api/v1/content/generate-random"
FOLDER = "https://www.dropbox.com/scl/fo/abc/xyz?rlkey=k"


class FakeDropbox:
    """Serves a folder tree {path: bytes} through MockTransport; records the downloaded paths."""

    def __init__(self, files):
        self.files, self.downloads = files, []

    def client(self):
        def handler(req: httpx.Request):
            if req.url.path == "/oauth2/token":
                return httpx.Response(200, json={"access_token": "AT"})
            if req.url.path == "/2/files/list_folder":
                body = json.loads(req.content)
                assert body["shared_link"]["url"] == FOLDER and req.headers["authorization"] == "Bearer AT"
                parent = body["path"]
                names = {p[len(parent) + 1:].split("/")[0]: "/" in p[len(parent) + 1:]
                         for p in self.files if p.startswith(parent + "/")}
                return httpx.Response(200, json={"entries": [{".tag": "folder" if sub else "file", "name": n}
                                                             for n, sub in names.items()], "has_more": False})
            if req.url.path == "/2/sharing/get_shared_link_file":
                arg = json.loads(req.headers["dropbox-api-arg"])
                assert arg["url"] == FOLDER
                self.downloads.append(arg["path"])
                return httpx.Response(200, content=self.files[arg["path"]])
            return httpx.Response(404)
        return httpx.Client(transport=httpx.MockTransport(handler))


def setup(env, monkeypatch, files):
    env(DROPBOX_FOLDER_URL=FOLDER, DROPBOX_APP_KEY="key", DROPBOX_APP_SECRET="secret", DROPBOX_REFRESH_TOKEN="rt")
    fake = FakeDropbox(files)
    monkeypatch.setattr(platforms, "http_client", lambda *a, **k: fake.client())
    return fake


PNG, GIF = (FIX / "tiny.png").read_bytes(), (FIX / "tiny.gif").read_bytes()


def test_not_configured_by_default(app):
    assert not dropbox_link.configured()


def test_pull_downloads_only_needed_files_and_never_repeats(app, env, monkeypatch):
    fake = setup(env, monkeypatch, {"/a.png": PNG, "/sub/b.gif": GIF, "/notes.txt": b"x"})
    with session_scope() as db:
        out = dropbox_link.pull(db, 1)
        assert len(out["assets"]) == 1 and len(fake.downloads) == 1
        a = out["assets"][0]
        assert a.storage_type == "upload" and a.source_ref.startswith("dropbox:/") and a.sha256
        assert (get_settings().MEDIA_UPLOAD_DIR / a.local_relpath).is_file()
        out2 = dropbox_link.pull(db, 5)  # the other image only; .txt is ignored, the first is remembered
        assert len(out2["assets"]) == 1 and len(set(fake.downloads)) == 2
        assert dropbox_link.pull(db, 5)["assets"] == []


def test_bad_file_is_skipped_and_reported(app, env, monkeypatch):
    setup(env, monkeypatch, {"/fake.png": b"not really a png at all......", "/ok.png": PNG})
    with session_scope() as db:
        out = dropbox_link.pull(db, 2)
        assert [a.filename for a in out["assets"]] == ["ok.jpg"]
        assert out["errors"][0]["path"] == "/fake.png"
        assert not any(p.name.endswith(".png") and p.stat().st_size == 0 for p in get_settings().MEDIA_UPLOAD_DIR.iterdir())


def test_png_is_stored_as_publishable_jpeg(app, env, monkeypatch):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (40, 30), (255, 0, 0, 0)).save(buf, "PNG")  # fully transparent: must flatten to white
    gif = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(gif, "GIF")
    setup(env, monkeypatch, {"/shot.png": buf.getvalue(), "/anim.gif": gif.getvalue()})
    with session_scope() as db:
        out = dropbox_link.pull(db, 5)
        by_name = {a.filename: a for a in out["assets"]}
        assert set(by_name) == {"shot.jpg", "anim.gif"}  # only PNG/WebP are converted
        a = by_name["shot.jpg"]
        path = get_settings().MEDIA_UPLOAD_DIR / a.local_relpath
        assert a.mime_type == "image/jpeg" and path.suffix == ".jpg" and a.source_ref == "dropbox:/shot.png"
        assert path.read_bytes()[:3] == b"\xff\xd8\xff" and a.size_bytes == path.stat().st_size
        with Image.open(path) as im:
            assert im.getpixel((5, 5)) == (255, 255, 255)
        assert not path.with_suffix(".png").exists()  # the PNG original is gone


def test_random_draft_pulls_when_library_is_empty(client, fake_claude, env, monkeypatch):
    fake = setup(env, monkeypatch, {"/a.png": PNG})
    r = client.post(URL, json={"platform": "instagram", "count": 1})
    assert r.status_code == 201, r.text
    assert len(fake.downloads) == 1 and r.json()["posts"][0]["status"] == "draft"


def test_random_draft_does_not_pull_when_library_has_enough(client, fake_claude, env, monkeypatch):
    fake = setup(env, monkeypatch, {"/a.png": PNG})
    client.post("/api/v1/assets/upload", files={"file": ("g.gif", GIF, "image/gif")})
    assert client.post(URL, json={"platform": "instagram", "count": 1}).status_code == 201
    assert fake.downloads == []


def test_dropbox_failure_surfaces_when_nothing_to_draw(client, fake_claude, env, monkeypatch):
    env(DROPBOX_FOLDER_URL=FOLDER, DROPBOX_APP_KEY="key", DROPBOX_APP_SECRET="secret", DROPBOX_REFRESH_TOKEN="rt")
    monkeypatch.setattr(platforms, "http_client", lambda *a, **k: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(401))))
    r = client.post(URL, json={"platform": "instagram", "count": 1})
    assert r.status_code == 502 and r.json()["error"]["code"] == "dropbox_error" and fake_claude.calls == 0


def test_purge_removes_file_only_when_all_posts_are_done(app, env, monkeypatch):
    setup(env, monkeypatch, {"/a.png": PNG})
    with session_scope() as db:
        asset = dropbox_link.pull(db, 1)["assets"][0]
        path = get_settings().MEDIA_UPLOAD_DIR / asset.local_relpath
        assert dropbox_link.purge_posted(db) == 0 and path.exists()  # unused: keep
        post = ContentPost(model_profile_id=1, platform="instagram", content_type="feed_post", campaign="lifestyle",
                           caption="x", media_asset_id=asset.id, status="approved")
        db.add(post)
        db.commit()
        assert dropbox_link.purge_posted(db) == 0 and path.exists()  # waiting to post: keep
        post.status = "published"
        db.commit()
        assert dropbox_link.purge_posted(db) == 1 and not path.exists()
        row = db.get(MediaAsset, asset.id)
        assert row.is_active is False and row.source_ref.startswith("dropbox:")  # remembered, never pulled again
        assert dropbox_link.pull(db, 5)["assets"] == []

"""Signed public media route /m/{token} (PLAN_PHASE2 §11): the only unauthenticated file route."""
from datetime import timedelta

import pytest

from app.db import get_session_factory, utcnow
from app.models import MediaAsset
from app.services.media_urls import make_media_token, media_url_for, verify_media_token

import base64

TINY = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def upload_with(client, auth=None, name="pic.png"):
    r = client.post("/api/v1/assets/upload", files={"file": (name, TINY, "image/png")}, data={"description": "d"},
                    auth=auth)
    assert r.status_code == 201, r.text
    return r.json()["id"], TINY


def upload(client):
    return upload_with(client)


@pytest.fixture
def basic_auth(env):
    """Switch Basic auth on; request it BEFORE `client` so create_app() sees it."""
    env(DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="pw-for-tests")


def test_valid_token_serves_bytes_without_credentials(phase2, client):
    asset_id, data = upload(client)
    r = client.get(f"/m/{make_media_token(asset_id, utcnow())}")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "image/png"


def test_head_request_works(phase2, client):
    asset_id, _ = upload(client)
    assert client.head(f"/m/{make_media_token(asset_id, utcnow())}").status_code == 200


def test_media_route_skips_basic_auth_but_everything_else_needs_it(phase2, basic_auth, client):
    creds = ("lee", "pw-for-tests")
    asset_id, data = upload_with(client, creds)
    token = make_media_token(asset_id, utcnow())
    assert client.get(f"/m/{token}").content == data                    # no credentials needed
    assert client.get(f"/api/v1/assets/{asset_id}/file").status_code == 401   # the normal file route is protected
    assert client.get("/api/v1/profiles").status_code == 401
    assert client.post(f"/m/{token}").status_code == 401                # only GET/HEAD skip auth


def test_expired_tampered_and_garbage_tokens_are_plain_404(phase2, client):
    asset_id, _ = upload(client)
    now = utcnow()
    good = make_media_token(asset_id, now)
    expired = make_media_token(asset_id, now - timedelta(hours=2))
    payload, sig = good.split(".")
    for token in (expired, f"{payload}.{sig[:-2]}xx", f"x{payload}.{sig}", "garbage", "a.b", "."):
        r = client.get(f"/m/{token}")
        assert r.status_code == 404


def test_token_is_bound_to_its_asset(phase2, client):
    a1, _ = upload(client)
    t = make_media_token(a1, utcnow())
    payload, sig = t.split(".")
    from app.services.media_urls import _b64
    forged = _b64(f"{a1 + 1}.{int((utcnow() + timedelta(hours=1)).timestamp())}".encode())
    assert client.get(f"/m/{forged}.{sig}").status_code == 404   # signature does not cover the forged payload
    assert verify_media_token(t, utcnow()) == a1


def test_archived_asset_is_404(phase2, client):
    asset_id, _ = upload(client)
    token = make_media_token(asset_id, utcnow())
    assert client.get(f"/m/{token}").status_code == 200
    client.delete(f"/api/v1/assets/{asset_id}")
    assert client.get(f"/m/{token}").status_code == 404


def test_missing_asset_and_missing_file_are_404(phase2, client):
    assert client.get(f"/m/{make_media_token(9999, utcnow())}").status_code == 404
    asset_id, _ = upload(client)
    from app.config import get_settings
    (get_settings().MEDIA_UPLOAD_DIR / get_session_factory()().get(MediaAsset, asset_id).local_relpath).unlink()
    assert client.get(f"/m/{make_media_token(asset_id, utcnow())}").status_code == 404


def test_not_configured_means_404_for_every_token(client):
    assert client.get("/m/anything.at-all").status_code == 404
    assert media_url_for(1, utcnow()) is None


def test_media_url_for_builds_https_url_that_verifies(phase2, client):
    asset_id, _ = upload(client)
    url = media_url_for(asset_id, utcnow())
    assert url.startswith("https://studio.example.com/m/")
    assert verify_media_token(url.rsplit("/m/", 1)[1], utcnow()) == asset_id


def test_token_does_not_validate_under_another_secret(phase2, client, env):
    asset_id, _ = upload(client)
    token = make_media_token(asset_id, utcnow())
    env(MEDIA_URL_SECRET="a-different-secret")
    assert verify_media_token(token, utcnow()) is None

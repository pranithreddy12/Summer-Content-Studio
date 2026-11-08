"""Accounts + OAuth (PLAN_PHASE2 §12/§15): state handling, encrypted storage, refresh,
disconnect, no token ever exposed. Platform HTTP is MockTransport only."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.api.dependencies import get_platform_http
from app.db import get_session_factory, utcnow
from app.models import NotificationLog, OAuthState, PlatformAccount
from app.platforms import PlatformError
from app.services import AppError, accounts


def db():
    return get_session_factory()()


def fake_refresh(monkeypatch, error=None):
    """Instagram has no real refresh any more (it asks for a reconnect), so the account service's own refresh
    logic is exercised through a stand-in platform refresh."""
    from app.platforms import TokenSet, instagram

    def refresh(http, settings, account):
        if error:
            raise error
        return TokenSet("REFRESHED-TOKEN", None, utcnow() + timedelta(days=60), None, [], "999", "summerh_03")
    monkeypatch.setattr(instagram, "refresh", refresh)


def ig_http(calls=None, refresh_status=200):
    """Instagram token endpoints (refresh_status is kept for callers; refresh now always asks to reconnect)."""
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url.path))
        if req.url.path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "LONG-TOKEN-SECRET", "expires_in": 5184000})
        if req.url.path.endswith("/me/assigned_instagram_accounts"):
            return httpx.Response(200, json={"data": [{"id": "999", "username": "summerh_03"}]})
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def web(phase2, client, app):
    app.dependency_overrides[get_platform_http] = lambda: ig_http()
    yield client
    app.dependency_overrides.pop(get_platform_http, None)


def new_state(platform="instagram", age=timedelta(0)):
    d = db()
    d.add(OAuthState(state="STATE-" + platform + str(age.total_seconds()), platform=platform, model_profile_id=1,
                     created_at=utcnow() - age))
    d.commit()
    return "STATE-" + platform + str(age.total_seconds())


# ---- start -----------------------------------------------------------------

def test_start_requires_configuration_and_names_missing_env_only(app, client):
    r = client.get("/oauth/instagram/start?profile_id=1", follow_redirects=False)
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"
    msg = r.json()["error"]["message"]
    assert "INSTAGRAM_APP_ID" in msg and "INSTAGRAM_CONFIG_ID" in msg and "PUBLIC_BASE_URL" in msg and "TOKEN_ENCRYPTION_KEY" in msg


def test_start_redirects_with_state_and_stores_it(phase2, client):
    r = client.get("/oauth/instagram/start?profile_id=1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://www.facebook.com/v27.0/dialog/oauth?")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    assert db().get(OAuthState, state).platform == "instagram"
    assert len(state) >= 40  # secrets.token_urlsafe(32)


def test_start_unknown_platform_and_profile(phase2, client):
    assert client.get("/oauth/x/start?profile_id=1", follow_redirects=False).status_code == 404
    assert client.get("/oauth/instagram/start?profile_id=999", follow_redirects=False).status_code == 404


def test_start_with_invalid_encryption_key_is_503(phase2, client, env):
    env(TOKEN_ENCRYPTION_KEY="not-a-fernet-key")
    r = client.get("/oauth/instagram/start?profile_id=1", follow_redirects=False)
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_old_states_are_purged_on_start(phase2, client):
    old = new_state(age=timedelta(minutes=30))
    client.get("/oauth/instagram/start?profile_id=1", follow_redirects=False)
    assert db().get(OAuthState, old) is None


# ---- callback --------------------------------------------------------------

def test_callback_stores_encrypted_tokens_and_redirects(web):
    state = new_state()
    r = web.get(f"/oauth/instagram/callback?code=CODE123&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/accounts?msg=")
    assert "kind=ok" in r.headers["location"]
    acct = db().query(PlatformAccount).one()
    assert (acct.platform, acct.username, acct.external_user_id, acct.status) == ("instagram", "summerh_03", "999", "connected")
    assert "LONG-TOKEN-SECRET" not in acct.access_token_enc            # encrypted at rest
    assert accounts.decrypt(acct.access_token_enc) == "LONG-TOKEN-SECRET"
    assert acct.token_expires_at is not None
    assert db().get(OAuthState, state) is None                          # single use


@pytest.mark.parametrize("state", ["", "unknown-state"])
def test_callback_bad_state_is_400(web, state):
    r = web.get(f"/oauth/instagram/callback?code=C&state={state}", follow_redirects=False)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_state"
    assert db().query(PlatformAccount).count() == 0


def test_callback_expired_state_is_400_and_burned(web):
    state = new_state(age=timedelta(minutes=11))
    assert web.get(f"/oauth/instagram/callback?code=C&state={state}", follow_redirects=False).status_code == 400
    assert db().get(OAuthState, state) is None
    assert db().query(PlatformAccount).count() == 0


def test_callback_state_cannot_be_reused(web):
    state = new_state()
    assert web.get(f"/oauth/instagram/callback?code=C&state={state}", follow_redirects=False).status_code == 302
    assert web.get(f"/oauth/instagram/callback?code=C&state={state}", follow_redirects=False).status_code == 400


def test_callback_state_must_match_platform(web):
    state = new_state("tiktok")
    assert web.get(f"/oauth/instagram/callback?code=C&state={state}", follow_redirects=False).status_code == 400
    assert db().get(OAuthState, state) is None


def test_callback_user_cancelled_burns_state_and_never_echoes_input(web):
    state = new_state()
    r = web.get(f"/oauth/instagram/callback?error=access_denied&error_description=EVIL-TEXT&state={state}",
                follow_redirects=False)
    assert r.status_code == 302 and "EVIL-TEXT" not in r.headers["location"] and "kind=error" in r.headers["location"]
    assert db().get(OAuthState, state) is None


def test_reconnect_updates_the_same_row(web):
    for _ in range(2):
        web.get(f"/oauth/instagram/callback?code=C&state={new_state(age=timedelta(seconds=len(db().query(OAuthState).all())))}",
                follow_redirects=False)
    assert db().query(PlatformAccount).count() == 1


def test_exchange_failure_redirects_with_error_not_500(phase2, client, app):
    def bad(req):
        return httpx.Response(400, json={"error": {"message": "Invalid code", "code": 100}})
    app.dependency_overrides[get_platform_http] = lambda: httpx.Client(transport=httpx.MockTransport(bad))
    try:
        r = client.get(f"/oauth/instagram/callback?code=BADCODE&state={new_state()}", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_platform_http, None)
    assert r.status_code == 302 and "kind=error" in r.headers["location"]
    assert "BADCODE" not in r.headers["location"]
    assert db().query(PlatformAccount).count() == 0


# ---- API / security --------------------------------------------------------

def test_accounts_api_never_exposes_tokens(web):
    web.get(f"/oauth/instagram/callback?code=C&state={new_state()}", follow_redirects=False)
    r = web.get("/api/v1/accounts")
    assert r.status_code == 200
    body = r.text
    assert "LONG-TOKEN-SECRET" not in body and "token_enc" not in body and "access_token" not in body
    assert set(r.json()[0]) == {"id", "model_profile_id", "platform", "username", "status", "token_expires_at",
                                "scopes", "last_error"}


def test_disconnect_wipes_tokens(web):
    web.get(f"/oauth/instagram/callback?code=C&state={new_state()}", follow_redirects=False)
    r = web.post("/api/v1/accounts/1/disconnect")
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    acct = db().get(PlatformAccount, 1)
    assert acct.access_token_enc == "" and acct.refresh_token_enc is None and acct.token_expires_at is None
    assert web.post("/api/v1/accounts/999/disconnect").status_code == 404


def test_decrypt_with_other_key_fails_cleanly(phase2, env):
    from cryptography.fernet import Fernet

    enc = accounts.encrypt("secret-value")
    env(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
    with pytest.raises(AppError) as e:
        accounts.decrypt(enc)
    assert e.value.code == "not_configured" and "secret-value" not in e.value.message


def test_missing_key_means_not_configured(app):
    with pytest.raises(AppError) as e:
        accounts.encrypt("x")
    assert (e.value.code, e.value.status) == ("not_configured", 503)


def test_sanitize_strips_tokens_and_query_fragments():
    out = accounts.sanitize("bad https://x?access_token=ABC123&y=1 token SECRETVAL", "SECRETVAL")
    assert "ABC123" not in out and "SECRETVAL" not in out
    assert len(accounts.sanitize("x" * 900)) == 500


# ---- refresh ---------------------------------------------------------------

def make_account(expires_in=timedelta(days=30), status="connected"):
    d = db()
    a = PlatformAccount(model_profile_id=1, platform="instagram", external_user_id="999", username="summerh_03",
                        access_token_enc=accounts.encrypt("OLD-TOKEN"), refresh_token_enc=None,
                        token_expires_at=utcnow() + expires_in, scopes=["instagram_basic"], status=status)
    d.add(a)
    d.commit()
    return a.id


def test_valid_token_is_not_refreshed_when_far_from_expiry(phase2, app):
    calls = []
    d = db()
    acct = d.get(PlatformAccount, make_account())
    assert accounts.get_valid_token(d, acct, ig_http(calls)) == "OLD-TOKEN" and calls == []


def test_token_close_to_expiry_is_refreshed_and_stored_encrypted(phase2, app, monkeypatch):
    fake_refresh(monkeypatch)
    d = db()
    acct = d.get(PlatformAccount, make_account(expires_in=timedelta(minutes=2)))
    assert accounts.get_valid_token(d, acct, ig_http()) == "REFRESHED-TOKEN"
    d2 = db()
    stored = d2.get(PlatformAccount, acct.id)
    assert "REFRESHED-TOKEN" not in stored.access_token_enc
    assert accounts.decrypt(stored.access_token_enc) == "REFRESHED-TOKEN"
    assert stored.token_expires_at > utcnow() + timedelta(days=30)


def test_refresh_rejected_marks_expired_and_pushes_once(phase2, app):
    d = db()
    acct = d.get(PlatformAccount, make_account(expires_in=timedelta(minutes=2)))
    with pytest.raises(PlatformError) as e:
        accounts.get_valid_token(d, acct, ig_http(refresh_status=400))
    assert e.value.code == "token_expired"
    stored = db().get(PlatformAccount, acct.id)
    assert stored.status == "expired" and stored.last_error
    assert "OLD-TOKEN" not in stored.last_error
    rows = db().query(NotificationLog).filter(NotificationLog.event == "account_attention").all()
    assert len(rows) == 1
    # an expired account refuses further use until reconnected
    with pytest.raises(PlatformError) as e2:
        accounts.get_valid_token(db(), db().get(PlatformAccount, acct.id), ig_http())
    assert e2.value.code == "account_not_connected"


def test_real_instagram_refresh_asks_to_reconnect(phase2, app):
    d = db()
    acct = d.get(PlatformAccount, make_account(expires_in=timedelta(minutes=2)))
    with pytest.raises(PlatformError) as e:
        accounts.get_valid_token(d, acct, ig_http())
    assert e.value.code == "token_expired" and db().get(PlatformAccount, acct.id).status == "expired"


def test_token_without_expiry_is_used_as_is(phase2, app):
    d = db()
    acct = d.get(PlatformAccount, make_account())
    acct.token_expires_at = None
    d.commit()
    assert accounts.get_valid_token(d, acct, ig_http()) == "OLD-TOKEN"
    assert accounts.accounts_expiring(db(), utcnow(), timedelta(days=3650)) == []


def test_accounts_expiring_query(phase2, app):
    make_account(expires_in=timedelta(days=3))
    d = db()
    d.add(PlatformAccount(model_profile_id=1, platform="tiktok", external_user_id="o", username="t",
                          access_token_enc=accounts.encrypt("T"), token_expires_at=utcnow() + timedelta(days=50),
                          scopes=[], status="connected"))
    d.commit()
    soon = accounts.accounts_expiring(db(), utcnow(), timedelta(days=7))
    assert [a.platform for a in soon] == ["instagram"]

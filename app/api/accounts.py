"""Accounts API (/api/v1/accounts) and the OAuth connect routes (/oauth/{platform}/...).

The OAuth routes live outside /api/v1 because the redirect URI registered with the
platform is `{PUBLIC_BASE_URL}/oauth/{platform}/callback`. They are browser routes:
Basic auth still applies, and they only ever redirect to fixed dashboard paths.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_platform_http
from app.db import get_db
from app.schemas import AccountOut
from app.services import AppError
from app.services import accounts as svc

router = APIRouter(prefix="/accounts", tags=["accounts"])
oauth_router = APIRouter(prefix="/oauth", tags=["oauth"])


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return svc.list_accounts(db)


@router.post("/{account_id}/disconnect", response_model=AccountOut)
def disconnect_account(account_id: int, db: Session = Depends(get_db)):
    return svc.disconnect(db, account_id)


def _back(msg: str, kind: str = "ok") -> RedirectResponse:
    """Redirect to a FIXED dashboard path with a fixed message (never echoes request input)."""
    return RedirectResponse(f"/accounts?msg={quote(msg)}&kind={kind}", status_code=302)


@oauth_router.get("/{platform}/start")
def oauth_start(platform: str, profile_id: int = Query(...), db: Session = Depends(get_db)):
    return RedirectResponse(svc.start_oauth(db, platform, profile_id), status_code=302)


@oauth_router.get("/{platform}/callback")
def oauth_callback(platform: str, code: str = "", state: str = "", error: str = "",
                   db: Session = Depends(get_db), http=Depends(get_platform_http)):
    if platform not in svc.PLATFORMS:
        raise AppError("not_found", "Unknown platform.", status=404)
    if error or not code:
        # user cancelled / platform error: burn the state, say nothing from the query string
        row = db.get(svc.OAuthState, state) if state else None
        if row is not None:
            db.delete(row)
            db.commit()
        return _back("Connection was cancelled or refused by the platform.", "error")
    try:
        account = svc.complete_oauth(db, platform, code, state, http)
    except AppError as e:
        if e.code == "invalid_state":
            raise
        return _back(f"Could not connect {platform}: {e.message}", "error")
    return _back(f"Connected {platform} account {account.username}.")

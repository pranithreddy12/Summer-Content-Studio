"""GET /m/{token}: signed, expiring public media (PLAN_PHASE2 §11).

The only unauthenticated file route (besides /health). Every failure is a bare 404.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.models import MediaAsset
from app.security import resolve_asset_file
from app.services.media_urls import verify_media_token

router = APIRouter(tags=["media"])


@router.api_route("/m/{token}", methods=["GET", "HEAD"])
def public_media(token: str, db: Session = Depends(get_db)):
    asset_id = verify_media_token(token, utcnow())
    asset = db.get(MediaAsset, asset_id) if asset_id is not None else None
    if asset is None or not asset.is_active:
        raise HTTPException(status_code=404)
    try:
        path = resolve_asset_file(asset)  # existing containment check
    except HTTPException:
        raise HTTPException(status_code=404) from None
    return FileResponse(path, media_type=asset.mime_type)

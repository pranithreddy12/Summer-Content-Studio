"""Assets API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import AssetCreate, AssetOut, AssetUpdate, TagOut
from app.security import _check_upload_rate_limit
from app.services import assets as assets_service

router = APIRouter(prefix="/assets", tags=["assets"])


def _asset_out(db: Session, asset) -> AssetOut:
    """Serialize an asset with tags, preview_url and usage_count."""
    out = AssetOut.model_validate(asset)
    out.preview_url = assets_service.get_preview_url(asset)
    out.usage_count = assets_service.get_usage_count(db, asset.id)
    return out


@router.get("")
async def list_assets_endpoint(
    q: Optional[str] = None,
    tag: Optional[list[str]] = Query(None),
    media_type: Optional[str] = None,
    storage_type: Optional[str] = None,
    unused: bool = False,
    used: bool = False,
    include_inactive: bool = False,
    limit: int = Query(50, le=100, ge=1),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List assets with filters."""
    result = assets_service.list_assets(
        db,
        q=q,
        tags=tag,
        media_type=media_type,
        storage_type=storage_type,
        unused=unused,
        used=used,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    items = []
    for asset in result["items"]:
        items.append(_asset_out(db, asset).model_dump(mode="json"))
    return {"items": items, "total": result["total"]}


@router.post("", status_code=201)
async def register_asset_endpoint(req: AssetCreate, db: Session = Depends(get_db)):
    """Register an asset without a file (external_url / local_sync)."""
    asset = assets_service.register_asset(db, req)
    asset = assets_service.get_asset(db, asset.id)
    return _asset_out(db, asset).model_dump(mode="json")


@router.post("/upload", status_code=201)
async def upload_asset_endpoint(
    request: Request,
    file: UploadFile = File(...),
    description: str = Form(""),
    tags: str = Form(""),
    model_profile_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload a file and register it as an asset (multipart)."""
    _check_upload_rate_limit(request)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    asset = await assets_service.upload_asset(
        db, file, description=description, tags=tag_list, model_profile_id=model_profile_id
    )
    asset = assets_service.get_asset(db, asset.id)
    return _asset_out(db, asset).model_dump(mode="json")


@router.post("/sync")
def sync_assets_endpoint(db: Session = Depends(get_db)):
    """Register new files from MEDIA_SYNC_DIR (read-only)."""
    return assets_service.sync_media_dir(db)


@router.get("/{asset_id}")
async def get_asset_endpoint(asset_id: int, db: Session = Depends(get_db)):
    asset = assets_service.get_asset(db, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _asset_out(db, asset).model_dump(mode="json")


@router.get("/{asset_id}/file")
async def get_asset_file_endpoint(asset_id: int, db: Session = Depends(get_db)):
    """Serve the asset file (supports range requests for video)."""
    asset = assets_service.get_asset(db, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = assets_service.resolve_asset_file(asset)
    return FileResponse(path, media_type=asset.mime_type or "application/octet-stream")


@router.patch("/{asset_id}")
async def update_asset_endpoint(asset_id: int, req: AssetUpdate, db: Session = Depends(get_db)):
    from fastapi import status as fastapi_status

    try:
        asset = assets_service.update_asset(
            db,
            asset_id,
            filename=req.filename,
            description=req.description,
            tags=req.tags,
            model_profile_id=req.model_profile_id,
            is_active=req.is_active,
        )
    except HTTPException:
        raise
    asset = assets_service.get_asset(db, asset.id)
    return _asset_out(db, asset).model_dump(mode="json")


@router.delete("/{asset_id}")
async def archive_asset_endpoint(
    asset_id: int, permanent: bool = False, force: bool = False, db: Session = Depends(get_db),
):
    """Archive an asset (default). `?permanent=true` deletes it for good (uploads/links only);
    `&force=true` is needed when non-archived posts use it."""
    if permanent:
        return assets_service.delete_asset_permanently(db, asset_id, force=force)
    asset = assets_service.archive_asset(db, asset_id)
    asset = assets_service.get_asset(db, asset.id)
    return _asset_out(db, asset).model_dump(mode="json")

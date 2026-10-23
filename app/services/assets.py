"""Assets service - register, upload, sync, filter, archive, file resolution."""

import hashlib
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile, HTTPException, status
from sqlalchemy import select, func, desc, and_, exists
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import utcnow
from app.models import MediaAsset, Tag, ContentPost, MediaType, StorageType, PostStatus
from app.schemas import AssetCreate, AssetUpdate
from app.security import (
    safe_relpath, validate_file_extension, validate_magic_bytes,
    compute_sha256, get_or_create_tags,
)

# =========================================================================
# File validation constants
# =========================================================================

ALLOWED_EXTENSIONS = {
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".png": ("image/png", "image"),
    ".webp": ("image/webp", "image"),
    ".gif": ("image/gif", "image"),
    ".mp4": ("video/mp4", "video"),
    ".mov": ("video/quicktime", "video"),
    ".webm": ("video/webm", "video"),
}


def _magic_check(first_chunk: bytes, ext: str) -> bool:
    """Check magic bytes against extension."""
    if len(first_chunk) < 16:
        return False

    ext = ext.lower()

    # Image types
    if ext in (".jpg", ".jpeg"):
        return first_chunk[:3] == b"\xFF\xD8\xFF"
    if ext == ".png":
        return first_chunk[:4] == b"\x89PNG"
    if ext == ".gif":
        return first_chunk[:4] == b"GIF8"
    if ext == ".webp":
        return first_chunk[:4] == b"RIFF" and first_chunk[8:12] == b"WEBP"

    # Video types (ftyp box at offset 4)
    if ext in (".mp4", ".mov"):
        return first_chunk[4:8] == b"ftyp"
    if ext == ".webm":
        return first_chunk[:4] == b"\x1A\x45\xDF\xA3"

    return False


# =========================================================================
# Registration (JSON, no file - for external_url and local_sync)
# =========================================================================

def register_asset(db: Session, req: AssetCreate) -> MediaAsset:
    """Register an asset without a file (external_url or local_sync)."""
    settings = get_settings()

    # Validate storage type
    if req.storage_type == StorageType.upload:
        raise HTTPException(status_code=400, detail="Use upload endpoint for uploads")

    # Validate relpath for local_sync
    local_relpath = None
    if req.storage_type == StorageType.local_sync:
        try:
            local_relpath = safe_relpath(req.local_relpath)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid path: {str(e)}")

        # Check uniqueness
        existing = db.execute(
            select(MediaAsset).where(
                MediaAsset.storage_type == StorageType.local_sync.value,
                MediaAsset.local_relpath == local_relpath,
                MediaAsset.is_active == True,
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"Asset already registered with id {existing.id}")

    # Create asset
    asset = MediaAsset(
        model_profile_id=req.model_profile_id,
        filename=req.filename[:255],
        media_type=req.media_type.value,
        mime_type=ALLOWED_EXTENSIONS.get(Path(req.filename).suffix.lower(), (None, None))[0],
        storage_type=req.storage_type.value,
        source_ref=req.source_ref,
        local_relpath=local_relpath,
        description=req.description,
        is_active=True,
        imported_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    # Tags
    if req.tags:
        asset.tags = get_or_create_tags(db, req.tags)

    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


# =========================================================================
# Upload (multipart file)
# =========================================================================

async def upload_asset(
    db: Session,
    file: UploadFile,
    description: str = "",
    tags: Optional[list[str]] = None,
    model_profile_id: Optional[int] = None,
) -> MediaAsset:
    """Upload a file and register it as an asset."""
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=422, detail="Missing filename")

    # Check extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {ext}")

    mime_type, media_type = ALLOWED_EXTENSIONS[ext]

    # Size limit
    max_bytes = (settings.MAX_IMAGE_MB if media_type == "image" else settings.MAX_VIDEO_MB) * 1024 * 1024

    # Stream to temp file, compute hash, check magic bytes
    settings.MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_name = f"tmp-{uuid.uuid4().hex}"
    tmp_path = settings.MEDIA_UPLOAD_DIR / tmp_name

    sha256_hash = hashlib.sha256()
    size = 0
    first_chunk = b""

    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break

                if not first_chunk:
                    first_chunk = chunk

                size += len(chunk)
                if size > max_bytes:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_IMAGE_MB if media_type == 'image' else settings.MAX_VIDEO_MB} MB limit")

                sha256_hash.update(chunk)
                out.write(chunk)

        if size == 0:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Empty file")

        # Magic byte check
        if not _magic_check(first_chunk, ext):
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=415, detail="File content does not match extension")

        sha256 = sha256_hash.hexdigest()

        # Check duplicate
        existing = db.execute(
            select(MediaAsset).where(MediaAsset.sha256 == sha256, MediaAsset.is_active == True)
        ).scalar_one_or_none()
        if existing:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"Duplicate asset with id {existing.id}")

        # Rename to final name
        final_name = uuid.uuid4().hex + ext
        final_path = settings.MEDIA_UPLOAD_DIR / final_name
        tmp_path.rename(final_path)

    except HTTPException:
        raise
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))

    # Sanitize original filename
    safe_filename = Path(file.filename).name[:255]

    asset = MediaAsset(
        model_profile_id=model_profile_id,
        filename=safe_filename,
        media_type=media_type,
        mime_type=mime_type,
        storage_type=StorageType.upload.value,
        source_ref=None,
        local_relpath=final_name,
        sha256=sha256,
        size_bytes=size,
        description=description or "",
        is_active=True,
        imported_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    if tags:
        asset.tags = get_or_create_tags(db, tags)

    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


# =========================================================================
# Local sync (from MEDIA_SYNC_DIR)
# =========================================================================

def ensure_free_disk(db: Session, root, min_free_gb: float) -> None:
    """Refuse to register (and so hash/pull) media when the disk is nearly full (PLAN §11a): the rclone
    copy that feeds MEDIA_SYNC_DIR fills this disk, and a full disk also breaks SQLite. Sends one push per day."""
    import shutil

    from app.services import AppError

    free_gb = shutil.disk_usage(root).free / 1e9
    if free_gb >= min_free_gb:
        return
    from app.db import utcnow
    from app.services.notifications import notify

    notify(db, "disk_low", f"disk:{utcnow().date().isoformat()}", "Server disk is almost full",
           f"{free_gb:.1f} GB free (minimum {min_free_gb:g} GB). Media sync is paused until space is freed.", url="/assets")
    raise AppError("disk_low", f"Only {free_gb:.1f} GB free (minimum {min_free_gb:g} GB); media sync refused.", status=507)


def sync_media_dir(db: Session) -> dict:
    """Sync files from MEDIA_SYNC_DIR - register new files, skip existing."""
    settings = get_settings()

    if not settings.MEDIA_SYNC_DIR:
        raise HTTPException(status_code=400, detail="MEDIA_SYNC_DIR not configured")

    root = settings.MEDIA_SYNC_DIR
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"Sync directory does not exist: {root}")
    ensure_free_disk(db, root, settings.MIN_FREE_DISK_GB)

    added = 0
    skipped = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip hidden files
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            skipped += 1
            continue

        # Skip Dropbox placeholders (size 0)
        if path.stat().st_size == 0:
            skipped += 1
            continue

        ext = path.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped += 1
            continue

        relpath = path.relative_to(root).as_posix()

        # Check if already registered
        existing = db.execute(
            select(MediaAsset).where(
                MediaAsset.storage_type == StorageType.local_sync.value,
                MediaAsset.local_relpath == relpath,
            )
        ).scalar_one_or_none()
        if existing:
            skipped += 1
            continue

        # Compute sha256
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha256_hash.update(chunk)
        sha256 = sha256_hash.hexdigest()

        mime_type, media_type = ALLOWED_EXTENSIONS[ext]

        asset = MediaAsset(
            model_profile_id=None,
            filename=path.name[:255],
            media_type=media_type,
            mime_type=mime_type,
            storage_type=StorageType.local_sync.value,
            source_ref=None,
            local_relpath=relpath,
            sha256=sha256,
            size_bytes=path.stat().st_size,
            description="",
            is_active=True,
            imported_at=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        # Auto-tag by folder name
        folder_parts = path.relative_to(root).parts[:-1]
        for part in folder_parts:
            folder_slug = part.lower().replace(" ", "-")
            tag = db.execute(select(Tag).where(Tag.slug == folder_slug)).scalar_one_or_none()
            if tag:
                asset.tags.append(tag)

        db.add(asset)
        added += 1

    db.commit()
    return {"added": added, "skipped": skipped}


# =========================================================================
# List / Filter
# =========================================================================

def list_assets(
    db: Session,
    q: Optional[str] = None,
    tags: Optional[list[str]] = None,
    media_type: Optional[str] = None,
    storage_type: Optional[str] = None,
    unused: bool = False,
    include_inactive: bool = False,
    limit: int = 50,
    offset: int = 0,
    used: bool = False,
) -> dict:
    """List assets with filters."""
    query = select(MediaAsset).options(joinedload(MediaAsset.tags))

    if not include_inactive:
        query = query.where(MediaAsset.is_active == True)

    if q:
        query = query.where(
            (MediaAsset.filename.ilike(f"%{q}%")) |
            (MediaAsset.description.ilike(f"%{q}%"))
        )

    if tags:
        # Asset must have ALL given tags
        for tag_slug in tags:
            query = query.join(MediaAsset.tags).where(Tag.slug == tag_slug)

    if media_type:
        query = query.where(MediaAsset.media_type == media_type)

    if storage_type:
        query = query.where(MediaAsset.storage_type == storage_type)

    if unused or used:
        # used = referenced by at least one non-archived post
        used_subq = select(ContentPost.media_asset_id).where(
            ContentPost.media_asset_id.isnot(None),
            ContentPost.status != PostStatus.archived.value,
        )
        query = query.where(MediaAsset.id.in_(used_subq) if used else ~MediaAsset.id.in_(used_subq))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar() or 0

    # Order and paginate
    query = query.order_by(desc(MediaAsset.imported_at)).limit(limit).offset(offset)
    items = db.execute(query).unique().scalars().all()

    return {"items": items, "total": total}


# =========================================================================
# Update / Archive
# =========================================================================

def get_asset(db: Session, asset_id: int) -> Optional[MediaAsset]:
    """Get asset by ID with tags."""
    return db.execute(
        select(MediaAsset)
        .options(joinedload(MediaAsset.tags), joinedload(MediaAsset.posts))
        .where(MediaAsset.id == asset_id)
    ).unique().scalar_one_or_none()


def update_asset(
    db: Session,
    asset_id: int,
    filename: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
    model_profile_id: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> MediaAsset:
    """Update asset metadata."""
    asset = get_asset(db, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    if filename is not None:
        asset.filename = filename[:255]
    if description is not None:
        asset.description = description
    if model_profile_id is not None:
        asset.model_profile_id = model_profile_id
    if is_active is not None:
        asset.is_active = is_active
    if tags is not None:
        asset.tags = get_or_create_tags(db, tags)

    asset.updated_at = utcnow()
    db.commit()
    db.refresh(asset)
    return asset


def archive_asset(db: Session, asset_id: int) -> MediaAsset:
    """Archive an asset (soft delete)."""
    asset = get_asset(db, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset.is_active = False
    asset.updated_at = utcnow()
    db.commit()
    db.refresh(asset)
    return asset


def delete_asset_permanently(db: Session, asset_id: int, force: bool = False) -> dict:
    """Permanently delete an uploaded or Dropbox-link asset (and its uploaded file).

    - Synced Dropbox files are never deleted: the folder is read-only to this app, and a deleted row
      would simply be re-imported by the next Sync. Archive them instead.
    - An asset used by non-archived posts needs force=True; those posts keep their text and lose the media.
    """
    from sqlalchemy import update

    from app.models import GenerationLog
    from app.services import AppError

    asset = db.get(MediaAsset, asset_id)
    if not asset:
        raise AppError("not_found", "Asset not found", 404)
    if asset.storage_type == StorageType.local_sync.value:
        raise AppError(
            "synced_asset",
            "Synced Dropbox files can't be deleted here: your Dropbox folder is never changed, and the file "
            "would come back on the next Sync. Archive it instead (it stays hidden).",
            409,
        )
    uses = get_usage_count(db, asset_id)
    if uses and not force:
        raise AppError(
            "asset_in_use",
            f"This asset is used by {uses} post(s). Deleting it keeps those posts but removes their media. "
            "Confirm to delete anyway.",
            409,
        )

    path = None
    if asset.storage_type == StorageType.upload.value and asset.local_relpath:
        try:
            path = resolve_asset_file(asset)  # containment-checked path inside MEDIA_UPLOAD_DIR
        except HTTPException:
            path = None

    db.execute(update(ContentPost).where(ContentPost.media_asset_id == asset_id).values(media_asset_id=None))
    db.execute(update(GenerationLog).where(GenerationLog.media_asset_id == asset_id).values(media_asset_id=None))
    db.delete(asset)
    db.commit()

    removed = False
    if path:
        try:
            path.unlink()
            removed = True
        except OSError:
            logger.warning("asset %s deleted but its file could not be removed", asset_id)
    return {"deleted": asset_id, "file_removed": removed, "posts_detached": uses}


# =========================================================================
# File resolution / preview URL
# =========================================================================

def resolve_asset_file(asset: MediaAsset) -> Path:
    """Resolve asset file path with containment check. Only way files are served."""
    settings = get_settings()

    if asset.storage_type == StorageType.upload.value:
        root = settings.MEDIA_UPLOAD_DIR
    elif asset.storage_type == StorageType.local_sync.value:
        if not settings.MEDIA_SYNC_DIR:
            raise HTTPException(status_code=404, detail="Sync directory not configured")
        root = settings.MEDIA_SYNC_DIR
    else:
        raise HTTPException(status_code=404, detail="Asset not available as local file")

    if not asset.local_relpath:
        raise HTTPException(status_code=404, detail="Asset has no local path")

    try:
        p = (root / asset.local_relpath).resolve()
        if not p.is_relative_to(root.resolve()):
            raise HTTPException(status_code=404, detail="Asset path outside allowed directory")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="Asset file not found")
        return p
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Asset file not found")


def get_preview_url(asset: MediaAsset) -> Optional[str]:
    """Get preview URL for asset, or None for external_url."""
    if asset.storage_type == StorageType.external_url.value:
        return None
    return f"/api/v1/assets/{asset.id}/file"


def get_usage_counts(db: Session, asset_ids: list[int]) -> dict[int, int]:
    """Non-archived post counts for many assets in one query."""
    if not asset_ids:
        return {}
    rows = db.execute(
        select(ContentPost.media_asset_id, func.count(ContentPost.id)).where(
            ContentPost.media_asset_id.in_(asset_ids),
            ContentPost.status != PostStatus.archived.value,
        ).group_by(ContentPost.media_asset_id)
    ).all()
    return {a: n for a, n in rows}


def get_usage_count(db: Session, asset_id: int) -> int:
    """Count non-archived posts using this asset."""
    return db.execute(
        select(func.count(ContentPost.id)).where(
            ContentPost.media_asset_id == asset_id,
            ContentPost.status != PostStatus.archived.value,
        )
    ).scalar() or 0
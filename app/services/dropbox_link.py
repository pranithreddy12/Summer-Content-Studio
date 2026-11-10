"""Pull random media from a Dropbox shared FOLDER link, only when drafts need it, and delete it after posting.

No bulk sync: a file is downloaded into MEDIA_UPLOAD_DIR as a normal `upload` asset (so publishing, previews and
Claude's image input work unchanged); its Dropbox path is remembered in `source_ref` ("dropbox:/path") so it is never
picked twice. Once every post using it is done, `purge_posted` deletes the file and archives the row.

Dropbox calls (list_folder + sharing/get_shared_link_file with a shared link) are recorded in
docs/PLATFORM_API_NOTES.md; the shared-link parameter shapes come from developer-forum posts, not the official
reference (UNVERIFIED until the first live run).
"""

import hashlib
import json
import logging
import random
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import platforms
from app.config import get_settings
from app.db import utcnow
from app.models import ContentPost, MediaAsset, PostStatus, StorageType
from app.services import AppError
from app.services.assets import ALLOWED_EXTENSIONS, _magic_check, ensure_free_disk

logger = logging.getLogger("app.services.dropbox_link")

API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"
PREFIX = "dropbox:"
DONE = (PostStatus.published.value, PostStatus.sent_to_drafts.value, PostStatus.archived.value)


def configured(settings=None) -> bool:
    s = settings or get_settings()
    return bool(s.DROPBOX_FOLDER_URL and s.DROPBOX_APP_KEY and s.DROPBOX_APP_SECRET and s.DROPBOX_REFRESH_TOKEN)


def _check(resp: httpx.Response, what: str) -> dict:
    if resp.status_code >= 400:
        raise AppError("dropbox_error", f"Dropbox {what} failed ({resp.status_code}).", 502)
    return platforms.json_or_empty(resp)


def _access_token(http, s) -> str:
    resp = platforms.send(http, "POST", f"{API}/oauth2/token", data={
        "grant_type": "refresh_token", "refresh_token": s.DROPBOX_REFRESH_TOKEN.get_secret_value(),
        "client_id": s.DROPBOX_APP_KEY, "client_secret": s.DROPBOX_APP_SECRET.get_secret_value()})
    token = _check(resp, "sign-in").get("access_token")
    if not token:
        raise AppError("dropbox_error", "Dropbox returned no access token.", 502)
    return token


def _list_files(http, token: str, url: str) -> list[str]:
    """Paths (relative to the shared folder) of every file, walking sub-folders. Non-recursive calls only:
    recursive listing is unsupported with shared links."""
    hdr = {"Authorization": f"Bearer {token}"}
    files, todo = [], [""]
    while todo:
        parent = todo.pop()
        body, endpoint = {"path": parent, "shared_link": {"url": url}}, "list_folder"
        while True:
            data = _check(platforms.send(http, "POST", f"{API}/2/files/{endpoint}", headers=hdr, json=body), "listing")
            for e in data.get("entries", []):
                path = f"{parent}/{e['name']}"
                if e.get(".tag") == "folder":
                    todo.append(path)
                elif e.get(".tag") == "file":
                    files.append(path)
            if not data.get("has_more"):
                break
            body, endpoint = {"cursor": data["cursor"]}, "list_folder/continue"
    return files


def _download(http, token: str, url: str, path: str, dest: Path, max_bytes: int) -> tuple[str, int]:
    """Stream one file to `dest`; returns (sha256, size). Raises AppError on a bad or oversized file."""
    arg = json.dumps({"url": url, "path": path})  # ensure_ascii: header-safe
    digest, size, first = hashlib.sha256(), 0, b""
    try:
        with http.stream("POST", f"{CONTENT}/2/sharing/get_shared_link_file",
                         headers={"Authorization": f"Bearer {token}", "Dropbox-API-Arg": arg}) as resp:
            if resp.status_code >= 400:
                raise AppError("dropbox_error", f"Dropbox download failed ({resp.status_code}).", 502)
            with open(dest, "wb") as out:
                for chunk in resp.iter_bytes(1024 * 1024):
                    first = first or chunk
                    size += len(chunk)
                    if size > max_bytes:
                        raise AppError("too_large", "File is larger than the allowed size.", 413)
                    digest.update(chunk)
                    out.write(chunk)
    except httpx.HTTPError as e:
        raise AppError("dropbox_error", "Could not reach Dropbox.", 502) from e
    if not size or not _magic_check(first, dest.suffix):
        raise AppError("bad_file", "File content does not match its extension.", 415)
    return digest.hexdigest(), size


def pull(db: Session, count: int, media_type: str | None = None, http=None) -> dict:
    """Download up to `count` random not-yet-used files. Returns {"assets": [...], "errors": [...]}."""
    s = get_settings()
    if not configured(s):
        raise AppError("not_configured", "Dropbox folder link is not configured.", 400)
    s.MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ensure_free_disk(db, s.MEDIA_UPLOAD_DIR, s.MIN_FREE_DISK_GB)
    http = http or platforms.http_client(s)
    url = s.DROPBOX_FOLDER_URL
    token = _access_token(http, s)

    seen = set(db.execute(select(MediaAsset.source_ref).where(MediaAsset.source_ref.like(PREFIX + "%"))).scalars())
    pool = [p for p in _list_files(http, token, url)
            if PREFIX + p not in seen and ALLOWED_EXTENSIONS.get(Path(p).suffix.lower())
            and (media_type is None or ALLOWED_EXTENSIONS[Path(p).suffix.lower()][1] == media_type)]
    random.shuffle(pool)

    assets, errors = [], []
    for path in pool:
        if len(assets) >= count:
            break
        ext = Path(path).suffix.lower()
        mime, kind = ALLOWED_EXTENSIONS[ext]
        name = uuid.uuid4().hex + ext
        dest = s.MEDIA_UPLOAD_DIR / name
        try:
            sha, size = _download(http, token, url, path, dest,
                                  (s.MAX_IMAGE_MB if kind == "image" else s.MAX_VIDEO_MB) * 1024 * 1024)
        except (AppError, platforms.PlatformError) as e:
            dest.unlink(missing_ok=True)
            errors.append({"path": path, "message": getattr(e, "message", str(e))})
            continue
        if db.execute(select(MediaAsset.id).where(MediaAsset.sha256 == sha, MediaAsset.is_active == True)).first():  # noqa: E712
            dest.unlink(missing_ok=True)  # same bytes already in the library
            db.add(MediaAsset(filename=Path(path).name[:255], media_type=kind, mime_type=mime, storage_type=StorageType.upload.value,
                              source_ref=PREFIX + path, local_relpath=None, is_active=False, description="duplicate of an existing asset"))
            db.commit()  # remember the path so it is not downloaded again
            continue
        asset = MediaAsset(filename=Path(path).name[:255], media_type=kind, mime_type=mime, storage_type=StorageType.upload.value,
                           source_ref=PREFIX + path, local_relpath=name, sha256=sha, size_bytes=size, description="",
                           is_active=True, imported_at=utcnow(), created_at=utcnow(), updated_at=utcnow())
        db.add(asset)
        db.commit()
        assets.append(asset)
    return {"assets": assets, "errors": errors}


def purge_posted(db: Session) -> int:
    """Delete the file of every pulled asset whose posts are all done (published / sent to drafts / archived),
    then archive the row (it keeps source_ref, so the Dropbox file is never pulled again). Returns files removed."""
    s = get_settings()
    removed = 0
    rows = db.execute(select(MediaAsset).where(MediaAsset.source_ref.like(PREFIX + "%"), MediaAsset.is_active == True,  # noqa: E712
                                               MediaAsset.local_relpath.isnot(None))).scalars().all()
    for asset in rows:
        statuses = db.execute(select(ContentPost.status).where(ContentPost.media_asset_id == asset.id)).scalars().all()
        if not statuses or any(st not in DONE for st in statuses):
            continue  # unused yet, or a post still needs the file
        path = (s.MEDIA_UPLOAD_DIR / asset.local_relpath).resolve()
        if path.is_relative_to(s.MEDIA_UPLOAD_DIR.resolve()):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                logger.warning("could not remove %s", asset.local_relpath)
                continue
        asset.is_active = False
        asset.updated_at = utcnow()
    db.commit()
    return removed

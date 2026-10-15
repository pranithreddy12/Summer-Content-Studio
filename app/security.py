"""Security utilities: basic auth, origin check, rate limiter, safe path helpers."""

import hashlib
import secrets
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.db import utcnow
from app.models import MediaType, StorageType


# =========================================================================
# Basic Auth
# =========================================================================

security = HTTPBasic(auto_error=False)


async def get_basic_auth_credentials(
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> Optional[HTTPBasicCredentials]:
    """Get basic auth credentials if provided."""
    return credentials


def require_basic_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(get_basic_auth_credentials),
) -> None:
    """Dependency that enforces basic auth when enabled."""
    settings = get_settings()
    if not settings.basic_auth_enabled:
        return

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    correct_username = secrets.compare_digest(credentials.username, settings.DASHBOARD_USERNAME or "")
    correct_password = secrets.compare_digest(
        credentials.password, settings.DASHBOARD_PASSWORD.get_secret_value() if settings.DASHBOARD_PASSWORD else ""
    )

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# =========================================================================
# Origin/Referer Check (CSRF mitigation for dashboard)
# =========================================================================

def check_origin(request: Request) -> None:
    """Check Origin/Referer header for non-API unsafe methods."""
    # Only check dashboard routes (not /api)
    if request.url.path.startswith("/api"):
        return

    # Only check unsafe methods
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    # If no origin/referer, allow (some clients don't send them)
    if not origin and not referer:
        return

    host = request.headers.get("host")
    if not host:
        return

    # Check origin
    if origin:
        try:
            from urllib.parse import urlparse
            origin_host = urlparse(origin).netloc
            if origin_host != host:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid origin",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # Check referer as fallback
    if referer:
        try:
            from urllib.parse import urlparse
            referer_host = urlparse(referer).netloc
            if referer_host != host:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid referer",
                )
        except HTTPException:
            raise
        except Exception:
            pass


# =========================================================================
# Rate Limiter (in-memory, single process)
# =========================================================================

# In-memory sliding window: {client_ip: [(timestamp, count), ...]}
_rate_limit_store: dict[str, list[tuple[float, int]]] = defaultdict(list)


def check_rate_limit(
    request: Request,
    limit: int,
    window_seconds: int = 60,
) -> None:
    """Check rate limit for client IP (in-memory sliding window)."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - window_seconds

    # Clean old entries
    entries = _rate_limit_store[client_ip]
    entries[:] = [(ts, count) for ts, count in entries if ts > window_start]

    # Count requests in window
    total = sum(count for _, count in entries)

    if total >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    # Add current request
    entries.append((now, 1))


def generation_rate_limit(request: Request) -> None:
    """Rate limit for generation endpoints."""
    settings = get_settings()
    check_rate_limit(request, settings.GENERATION_RATE_LIMIT_PER_MINUTE)


# =========================================================================
# Safe Path Helpers
# =========================================================================

def safe_relpath(path: str) -> str:
    """
    Normalize and validate a relative path.
    Rejects absolute paths, drive letters, directory traversal, NUL bytes.
    Returns normalized posix-style relative path.
    """
    if not path or not path.strip():
        raise ValueError("Path cannot be empty")

    # Reject NUL bytes
    if "\x00" in path:
        raise ValueError("Path contains null byte")

    # Convert backslashes to forward slashes
    path = path.replace("\\", "/")

    # Reject absolute paths
    if path.startswith("/"):
        raise ValueError("Absolute paths not allowed")

    # Reject Windows drive letters (C:, D:, etc.)
    if len(path) >= 2 and path[1] == ":":
        raise ValueError("Drive letters not allowed")

    # Reject UNC paths (\\server\share)
    if path.startswith("//"):
        raise ValueError("UNC paths not allowed")

    # Split and check for directory traversal
    parts = path.split("/")
    for part in parts:
        if part == "..":
            raise ValueError("Directory traversal not allowed")
        if part == "" and part != parts[-1]:  # Allow trailing slash
            raise ValueError("Empty path segment not allowed")

    # Normalize: remove trailing slash, resolve .
    normalized_parts = [p for p in parts if p != "."]
    return "/".join(normalized_parts)


def resolve_asset_file(asset, settings=None) -> Path:
    """
    Resolve an asset's file path with containment check.
    Only way files are served - no endpoint accepts user-supplied paths directly.
    """
    if settings is None:
        settings = get_settings()

    if asset.storage_type == StorageType.upload.value:
        root = settings.MEDIA_UPLOAD_DIR
    elif asset.storage_type == StorageType.local_sync.value:
        if not settings.MEDIA_SYNC_DIR:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sync directory not configured",
            )
        root = settings.MEDIA_SYNC_DIR
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not available as local file",
        )

    if not asset.local_relpath:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset has no local path",
        )

    # Resolve path and check containment
    try:
        asset_path = (root / asset.local_relpath).resolve()
        root_resolved = root.resolve()

        if not asset_path.is_relative_to(root_resolved):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset path outside allowed directory",
            )

        if not asset_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset file not found",
            )

        return asset_path
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset file not found",
        )


# =========================================================================
# File Validation
# =========================================================================

# Allowed extensions -> (mime_type, media_type)
ALLOWED_EXTENSIONS = {
    ".jpg": ("image/jpeg", MediaType.image),
    ".jpeg": ("image/jpeg", MediaType.image),
    ".png": ("image/png", MediaType.image),
    ".webp": ("image/webp", MediaType.image),
    ".gif": ("image/gif", MediaType.image),
    ".mp4": ("video/mp4", MediaType.video),
    ".mov": ("video/quicktime", MediaType.video),
    ".webm": ("video/webm", MediaType.video),
}

# Magic bytes for file type verification
MAGIC_BYTES = {
    "jpeg": b"\xFF\xD8\xFF",
    "png": b"\x89\x50\x4E\x47",
    "gif": b"GIF8",
    "webp": b"RIFF",
    "mp4": b"\x00\x00\x00",  # ftyp at offset 4
    "mov": b"\x00\x00\x00",  # ftyp at offset 4
    "webm": b"\x1A\x45\xDF\xA3",
}


def validate_file_extension(filename: str) -> tuple[str, MediaType]:
    """Validate file extension and return mime_type and media_type."""
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}")
    return ALLOWED_EXTENSIONS[ext]


def validate_magic_bytes(file_bytes: bytes, expected_media_type: MediaType) -> None:
    """Validate file magic bytes match expected type."""
    if len(file_bytes) < 16:
        raise ValueError("File too small for magic byte check")

    if expected_media_type == MediaType.image:
        # Check image types
        if file_bytes[:3] == MAGIC_BYTES["jpeg"]:
            return
        if file_bytes[:4] == MAGIC_BYTES["png"]:
            return
        if file_bytes[:4] == MAGIC_BYTES["gif"]:
            return
        if file_bytes[:4] == MAGIC_BYTES["webp"] and file_bytes[8:12] == b"WEBP":
            return
        raise ValueError("File content does not match image type")

    elif expected_media_type == MediaType.video:
        # Check video types (ftyp at offset 4 for mp4/mov)
        if file_bytes[4:8] == b"ftyp":
            return
        if file_bytes[:4] == MAGIC_BYTES["webm"]:
            return
        raise ValueError("File content does not match video type")


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def slugify(text: str) -> str:
    """Convert text to slug (lowercase, alphanumeric + hyphens, max 60 chars)."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60]


def get_or_create_tags(db, tag_names: list[str]) -> list:
    """Get or create tags by name (slugified). Returns list of Tag objects."""
    from app.models import Tag
    from sqlalchemy import select

    tags = []
    for name in tag_names:
        if not name or not name.strip():
            continue
        slug = slugify(name)
        if not slug:
            continue

        existing = db.execute(select(Tag).where(Tag.slug == slug)).scalar_one_or_none()
        if existing:
            tags.append(existing)
        else:
            tag = Tag(name=name.strip(), slug=slug, is_category=False, created_at=utcnow())
            db.add(tag)
            db.flush()
            tags.append(tag)

    return tags
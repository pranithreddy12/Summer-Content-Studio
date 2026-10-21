"""Turn an asset into JPEG images the AI can look at.

Uses ffmpeg (on PATH or FFMPEG_PATH) for video frames and for shrinking images - no Python
dependency. Dropbox links (external_url) are never fetched, so they yield no images.
"""

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from app.config import Settings, get_settings
from app.models import MediaType, StorageType
from app.security import resolve_asset_file

logger = logging.getLogger("app.services.frames")

RAW_MAX_BYTES = 3_500_000  # without ffmpeg, only send small originals (provider limit ~5 MB)
RAW_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
FRAME_POINTS = (0.2, 0.5, 0.8)  # fractions of the video duration


@dataclass
class ImageInput:
    media_type: str  # e.g. "image/jpeg"
    data: bytes


def _ffmpeg(settings: Settings) -> Optional[str]:
    return settings.FFMPEG_PATH or shutil.which("ffmpeg")


def _jpeg_frame(ffmpeg: str, path, seek: Optional[float]) -> Optional[bytes]:
    """One downscaled (max 1024px wide) JPEG via ffmpeg, read from stdout. No shell, no temp files."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if seek is not None:
        cmd += ["-ss", f"{seek:.2f}"]
    cmd += ["-i", str(path), "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2",
            "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 and r.stdout else None


def _duration(ffmpeg: str, path) -> Optional[float]:
    try:
        r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def get_asset_images(asset, settings: Optional[Settings] = None) -> list[ImageInput]:
    """Images to show the AI for this asset ([] when none are available)."""
    settings = settings or get_settings()
    if asset is None or not settings.ENABLE_IMAGE_INPUT:
        return []
    if asset.storage_type not in (StorageType.upload.value, StorageType.local_sync.value):
        return []
    try:
        path = resolve_asset_file(asset, settings)  # containment-checked; the only way files are read
    except HTTPException:
        return []

    ffmpeg = _ffmpeg(settings)
    if asset.media_type == MediaType.image.value:
        if ffmpeg:
            data = _jpeg_frame(ffmpeg, path, None)
            if data:
                return [ImageInput("image/jpeg", data)]
        if asset.mime_type in RAW_TYPES and path.stat().st_size <= RAW_MAX_BYTES:
            return [ImageInput(asset.mime_type, path.read_bytes())]
        return []

    if not ffmpeg:
        logger.warning("ffmpeg not found - video frames unavailable (set FFMPEG_PATH)")
        return []
    duration = _duration(ffmpeg, path)
    points = [duration * f for f in FRAME_POINTS] if duration else [0.5]
    frames = (_jpeg_frame(ffmpeg, path, t) for t in points[: settings.MAX_FRAMES])
    return [ImageInput("image/jpeg", d) for d in frames if d]

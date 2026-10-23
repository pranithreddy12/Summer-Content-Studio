"""Pre-flight media rules per platform (docs/PLATFORM_API_NOTES.md) + publisher lookup."""
from types import SimpleNamespace

import pytest

from app.platforms import PlatformError, publisher
from app.services.publishing import asset_is_publishable


def p(platform, content_type, media_type, mime):
    asset = SimpleNamespace(is_active=True, storage_type="upload", media_type=media_type, mime_type=mime)
    return SimpleNamespace(platform=platform, content_type=content_type, media_asset_id=1, asset=asset)


@pytest.mark.parametrize("post,ok", [
    (p("instagram", "feed_post", "image", "image/jpeg"), True),
    (p("instagram", "feed_post", "image", "image/png"), False),   # Instagram: JPEG only
    (p("instagram", "story", "image", "image/webp"), False),
    (p("instagram", "reel", "video", "video/mp4"), True),
    (p("instagram", "feed_post", "video", "video/quicktime"), True),
    (p("tiktok", "video", "video", "video/mp4"), True),
    (p("tiktok", "photo_post", "image", "image/jpeg"), False),    # TikTok inbox: video only
])
def test_asset_is_publishable_per_platform(post, ok):
    result, code = asset_is_publishable(post)
    assert result is ok and (ok or code == "media_not_publishable")


def test_publisher_lookup():
    assert publisher("instagram").__name__ == "app.platforms.instagram"
    assert publisher("tiktok").__name__ == "app.platforms.tiktok"
    with pytest.raises(PlatformError) as e:
        publisher("x")
    assert e.value.code == "unsupported"

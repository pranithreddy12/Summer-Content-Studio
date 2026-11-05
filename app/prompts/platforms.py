"""Platform rules and content types for prompts."""

from app.models import Platform, ContentType

CONTENT_TYPES = {
    Platform.instagram: [ContentType.feed_post, ContentType.reel, ContentType.story],
    Platform.tiktok: [ContentType.video, ContentType.photo_post],
    Platform.fanvue: [ContentType.fanvue_post],
}

HASHTAG_CAP = {
    Platform.instagram: 15,
    Platform.tiktok: 8,
    Platform.fanvue: 0,  # Fanvue captions carry no hashtags
}

PLATFORM_RULES = {
    Platform.instagram: {
        ContentType.feed_post: (
            "Instagram feed photo caption. First line is the hook (visible before 'more'). "
            "1–4 short lines total. 3–10 relevant hashtags returned separately, mix of niche and broad."
        ),
        ContentType.reel: (
            "Instagram Reel caption. Very short punchy hook that works with the video. "
            "1–3 short lines. 3–8 relevant hashtags."
        ),
        ContentType.story: (
            "Instagram Story text overlay idea + short caption. Keep it under 120 characters. 0–3 hashtags."
        ),
    },
    Platform.tiktok: {
        ContentType.video: (
            "TikTok caption. Hook-first, casual, lowercase-friendly, under 150 characters before hashtags. "
            "3–6 hashtags; trend-style tags only if relevant. content_notes: suggest an on-screen text hook for the first 2 seconds."
        ),
        ContentType.photo_post: (
            "TikTok photo-mode caption. Short and playful. 3–6 hashtags."
        ),
    },
    Platform.fanvue: {
        ContentType.fanvue_post: (
            "Fanvue post caption for paying subscribers. Personal and warm, written to people who already follow her. "
            "Under 1,000 characters. No hashtags (return an empty list). Describe only what is visible in the media; "
            "never invent explicit details."
        ),
    },
}

GENERAL_PLATFORM_RULES = [
    "Content must stay within Instagram and TikTok community guidelines: suggestive and flirty is fine, explicit is not.",
    "Never mention or link adult-subscription sites by name on Instagram or TikTok; use 'link in bio' style CTAs.",
]
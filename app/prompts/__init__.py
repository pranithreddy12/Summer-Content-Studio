"""Prompt rendering functions."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, PackageLoader, StrictUndefined

from app.models import ModelProfile, MediaAsset, Platform, ContentType
from app.prompts.platforms import PLATFORM_RULES, GENERAL_PLATFORM_RULES

PROMPT_VERSION = "v2"  # v2 adds image-aware accuracy rules (v1 files kept for history)

# Jinja2 environment for prompt templates (autoescape off for prompts)
_prompt_env = Environment(
    loader=PackageLoader("app", "prompts"),
    autoescape=False,
    undefined=StrictUndefined,
)


def render_system(
    profile: ModelProfile,
    platform: Platform,
    content_type: ContentType,
    image_count: int = 0,
) -> str:
    """Render the system prompt."""
    template = _prompt_env.get_template(f"system_{PROMPT_VERSION}.j2")

    platform_rule = PLATFORM_RULES.get(platform, {}).get(content_type, "")

    return template.render(
        profile=profile,
        platform=platform.value,
        content_type=content_type.value,
        platform_rule=platform_rule,
        general_rules=GENERAL_PLATFORM_RULES,
        image_count=image_count,
    )


def render_user(
    platform: Platform,
    content_type: ContentType,
    campaign_name: str,
    asset: Optional[MediaAsset] = None,
    asset_context: str = "",
    seasonal_context: str = "",
    tone_variation: str = "",
    cta_request: str = "",
    extra_restrictions: Optional[list[str]] = None,
    avoid_repeating: str = "",
    extra_instructions: str = "",
    image_count: int = 0,
) -> str:
    """Render the user prompt."""
    template = _prompt_env.get_template(f"user_{PROMPT_VERSION}.j2")

    return template.render(
        platform=platform.value,
        content_type=content_type.value,
        campaign_name=campaign_name,
        asset=asset,
        asset_context=asset_context,
        seasonal_context=seasonal_context,
        tone_variation=tone_variation,
        cta_request=cta_request,
        extra_restrictions=extra_restrictions or [],
        avoid_repeating=avoid_repeating,
        extra_instructions=extra_instructions,
        image_count=image_count,
    )


REPLY_PROMPT_VERSION = "reply_v1"


def render_reply_system(profile: ModelProfile) -> str:
    """System prompt for drafting a reply to a comment (persona + restricted topics reused)."""
    return _prompt_env.get_template("reply_v1.j2").render(profile=profile)


def render_reply_user(post_caption: str, author: str, comment: str, extra_instructions: str = "") -> str:
    """User prompt: the stranger's comment goes inside a tag as data, angle brackets neutralised."""
    import re

    def clean(text: str, limit: int) -> str:
        return re.sub(r"[<>]", " ", text or "")[:limit].strip()

    name = re.sub(r"[^A-Za-z0-9_.-]", "", author or "")[:30] or "unknown"
    lines = [
        f"<post_caption>{clean(post_caption, 400)}</post_caption>",
        f'<comment author="{name}">{clean(comment, 500)}</comment>',
    ]
    if extra_instructions:
        lines.append(f"<extra_instructions>{clean(extra_instructions, 300)}</extra_instructions>")
    lines.append("Write one reply.")
    return "\n".join(lines)


FAN_REPLY_PROMPT_VERSION = "fan_reply_v1"


def render_fan_reply_system(profile: ModelProfile) -> str:
    """System prompt for a private Fanvue chat reply (persona + Lee's fan rules, H5)."""
    rules = profile.fan_rules or {}
    return _prompt_env.get_template("fan_reply_v1.j2").render(
        profile=profile, never_say=list(rules.get("never_say") or []), notes=str(rules.get("notes") or "")[:1000])


def render_fan_reply_user(messages: list, extra_instructions: str = "") -> str:
    """The last messages, oldest first, as data. Angle brackets neutralised so a fan cannot close a tag."""
    import re

    def clean(text: str, limit: int) -> str:
        return re.sub(r"[<>]", " ", text or "")[:limit].strip()

    lines = ["<conversation>"]
    for who, text in messages:
        lines.append(f'<message from="{"fan" if who == "in" else "you"}">{clean(text, 600)}</message>')
    lines.append("</conversation>")
    if extra_instructions:
        lines.append(f"<extra_instructions>{clean(extra_instructions, 300)}</extra_instructions>")
    lines.append("Write one reply to the fan's latest message, or escalate.")
    return "\n".join(lines)

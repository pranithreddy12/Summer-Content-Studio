"""Prompt rendering functions."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, PackageLoader, StrictUndefined

from app.models import ModelProfile, MediaAsset, Platform, ContentType
from app.prompts.platforms import PLATFORM_RULES, GENERAL_PLATFORM_RULES

PROMPT_VERSION = "v1"

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
) -> str:
    """Render the system prompt."""
    template = _prompt_env.get_template("system_v1.j2")

    platform_rule = PLATFORM_RULES.get(platform, {}).get(content_type, "")

    return template.render(
        profile=profile,
        platform=platform.value,
        content_type=content_type.value,
        platform_rule=platform_rule,
        general_rules=GENERAL_PLATFORM_RULES,
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
) -> str:
    """Render the user prompt."""
    template = _prompt_env.get_template("user_v1.j2")

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
    )
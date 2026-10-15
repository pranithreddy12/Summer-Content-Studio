"""Database seeding functions."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Tag, ModelProfile
from app.security import slugify


# Category tags to seed
CATEGORY_TAGS = [
    {"slug": "halloween", "name": "Halloween"},
    {"slug": "lifestyle", "name": "General lifestyle"},
    {"slug": "fashion", "name": "Fashion & appearance"},
    {"slug": "playful", "name": "Playful / teasing"},
    {"slug": "reels", "name": "Reels & short-form video"},
    {"slug": "promo", "name": "Promotional"},
]


def slugify(text: str) -> str:
    """Convert text to slug (lowercase, alphanumeric + hyphens)."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60]


def seed_categories(db: Session) -> int:
    """Seed category tags (idempotent upsert by slug)."""
    added = 0
    for cat in CATEGORY_TAGS:
        existing = db.execute(select(Tag).where(Tag.slug == cat["slug"])).scalar_one_or_none()
        if existing:
            # Update name if changed
            if existing.name != cat["name"]:
                existing.name = cat["name"]
            continue

        tag = Tag(
            name=cat["name"],
            slug=cat["slug"],
            is_category=True,
            created_at=utcnow(),
        )
        db.add(tag)
        added += 1

    if added:
        db.flush()
    return added


def seed_profiles(db: Session) -> int:
    """Seed model profiles from JSON files (idempotent by slug)."""
    seed_dir = Path(__file__).parent / "seed"
    added = 0

    for json_file in seed_dir.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        slug = data.get("slug")
        if not slug:
            continue

        existing = db.execute(select(ModelProfile).where(ModelProfile.slug == slug)).scalar_one_or_none()
        if existing:
            continue  # Never overwrite existing profiles

        profile = ModelProfile(
            slug=slug,
            name=data["name"],
            handles=data.get("handles", {}),
            persona_description=data["persona_description"],
            tone_style=data["tone_style"],
            communication_rules=data["communication_rules"],
            restricted_topics=data["restricted_topics"],
            style_notes=data.get("style_notes", ""),
            cta_guidelines=data.get("cta_guidelines", ""),
            is_active=data.get("is_active", True),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(profile)
        added += 1

    if added:
        db.flush()
    return added


def seed_all(db: Session) -> dict:
    """Run all seeders."""
    return {
        "categories": seed_categories(db),
        "profiles": seed_profiles(db),
    }
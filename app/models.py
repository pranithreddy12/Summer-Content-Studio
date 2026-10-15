"""SQLAlchemy ORM models and enums."""

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


# =========================================================================
# Enums (stored as String in DB, not native DB enums)
# =========================================================================

class Platform(str, enum.Enum):
    instagram = "instagram"
    tiktok = "tiktok"
    # x = "x"  # Phase 2


class ContentType(str, enum.Enum):
    feed_post = "feed_post"
    reel = "reel"
    story = "story"
    video = "video"
    photo_post = "photo_post"


class PostStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"
    archived = "archived"


class MediaType(str, enum.Enum):
    image = "image"
    video = "video"


class StorageType(str, enum.Enum):
    upload = "upload"
    local_sync = "local_sync"
    external_url = "external_url"


class PostSource(str, enum.Enum):
    ai = "ai"
    manual = "manual"


# Valid platform/content-type combos (enforced in service layer)
VALID_COMBOS = {
    Platform.instagram: {ContentType.feed_post, ContentType.reel, ContentType.story},
    Platform.tiktok: {ContentType.video, ContentType.photo_post},
}


# =========================================================================
# Association Tables
# =========================================================================

asset_tags = Table(
    "asset_tags",
    Base.metadata,
    Column("asset_id", Integer, ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("content_posts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


# =========================================================================
# Models
# =========================================================================

class ModelProfile(Base):
    __tablename__ = "model_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    handles: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    persona_description: Mapped[str] = mapped_column(Text, nullable=False)
    tone_style: Mapped[str] = mapped_column(Text, nullable=False)
    communication_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    restricted_topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    style_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cta_guidelines: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    assets: Mapped[List["MediaAsset"]] = relationship("MediaAsset", back_populates="profile", lazy="selectin")
    posts: Mapped[List["ContentPost"]] = relationship("ContentPost", back_populates="profile", lazy="selectin")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    is_category: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    # Relationships
    assets: Mapped[List["MediaAsset"]] = relationship("MediaAsset", secondary=asset_tags, back_populates="tags", lazy="selectin")
    posts: Mapped[List["ContentPost"]] = relationship("ContentPost", secondary=post_tags, back_populates="tags", lazy="selectin")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    local_relpath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Constraints
    __table_args__ = (
        UniqueConstraint("storage_type", "local_relpath", name="uq_asset_storage_relpath"),
    )

    # Relationships
    profile: Mapped[Optional["ModelProfile"]] = relationship("ModelProfile", back_populates="assets", lazy="selectin")
    tags: Mapped[List["Tag"]] = relationship("Tag", secondary=asset_tags, back_populates="assets", lazy="selectin")
    posts: Mapped[List["ContentPost"]] = relationship("ContentPost", back_populates="asset", lazy="selectin")


class ContentPost(Base):
    __tablename__ = "content_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    media_asset_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    campaign: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    hook: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    call_to_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PostStatus.draft.value, index=True)
    planned_publish_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default=PostSource.ai.value)
    generation_input: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Indexes
    __table_args__ = (
        Index("ix_content_posts_status_platform", "status", "platform"),
    )

    # Relationships
    profile: Mapped["ModelProfile"] = relationship("ModelProfile", back_populates="posts", lazy="selectin")
    asset: Mapped[Optional["MediaAsset"]] = relationship("MediaAsset", back_populates="posts", lazy="selectin")
    tags: Mapped[List["Tag"]] = relationship("Tag", secondary=post_tags, back_populates="posts", lazy="selectin")
    variants: Mapped[List["ContentVariant"]] = relationship(
        "ContentVariant",
        back_populates="post",
        lazy="selectin",
        order_by="ContentVariant.created_at.desc()",
        cascade="all, delete-orphan",
    )
    generation_logs: Mapped[List["GenerationLog"]] = relationship("GenerationLog", back_populates="post", lazy="selectin")

    @property
    def variant_count(self) -> int:
        """Number of variants (used by PostOut serialization)."""
        return len(self.variants)


class ContentVariant(Base):
    __tablename__ = "content_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(Integer, ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    hook: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    call_to_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_format: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_log_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("generation_logs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    # Relationships
    post: Mapped["ContentPost"] = relationship("ContentPost", back_populates="variants", lazy="selectin")
    generation_log: Mapped[Optional["GenerationLog"]] = relationship("GenerationLog", lazy="selectin")


class GenerationLog(Base):
    __tablename__ = "generation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False)
    media_asset_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    post_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("content_posts.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="anthropic")
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # generate / regenerate
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    # Relationships
    post: Mapped[Optional["ContentPost"]] = relationship("ContentPost", back_populates="generation_logs", lazy="selectin")
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
    fanvue = "fanvue"  # Phase 3
    # x = "x"  # deferred


class ContentType(str, enum.Enum):
    feed_post = "feed_post"
    reel = "reel"
    story = "story"
    video = "video"
    photo_post = "photo_post"
    fanvue_post = "fanvue_post"


class PostStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    scheduled = "scheduled"
    publishing = "publishing"      # transient claim state (Phase 2)
    published = "published"
    sent_to_drafts = "sent_to_drafts"  # TikTok inbox: uploaded, human must post in app
    failed = "failed"
    archived = "archived"


class AccountStatus(str, enum.Enum):
    connected = "connected"
    expired = "expired"
    revoked = "revoked"
    error = "error"


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
    Platform.fanvue: {ContentType.fanvue_post},
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
    # Phase 3 (H5): {"never_say": [...], "escalate_topics": [...], "notes": "..."} for private fan chats
    fan_rules: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
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
    # Phase 3 (H7): Fanvue-only media can never be posted to Instagram/TikTok
    fanvue_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fanvue_media_uuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # set after first upload
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

    # ---- Phase 2 publishing columns ----
    platform_account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("platform_accounts.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    published_via: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # api | manual
    external_post_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    external_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    publish_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_publish_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    tiktok_privacy_level: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    planned_by: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # planner | user
    group_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    reminded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    # ---- Phase 3 Fanvue columns (null = defaults: audience 'subscribers', free post) ----
    fanvue_audience: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    fanvue_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

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


# =========================================================================
# Phase 2 models (PLAN_PHASE2 §6)
# =========================================================================

class PlatformAccount(Base):
    """A connected platform account. Tokens are Fernet-encrypted at rest."""

    __tablename__ = "platform_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(100), nullable=False)  # IG user id / TikTok open_id
    username: Mapped[str] = mapped_column(String(100), nullable=False)  # display only
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    refresh_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AccountStatus.connected.value)
    last_error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # sanitized, no tokens
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("model_profile_id", "platform", name="uq_account_profile_platform"),
    )

    profile: Mapped["ModelProfile"] = relationship("ModelProfile", lazy="selectin")


class OAuthState(Base):
    """Single-use CSRF state for the OAuth connect flow. Deleted on use; >10 min invalid."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    model_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False
    )
    code_verifier: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # PKCE
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class PublishAttempt(Base):
    """One publish attempt for a post. Append-only."""

    __tablename__ = "publish_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("platform_accounts.id", ondelete="SET NULL"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # schedule/manual/retry/dry_run
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # null while running + for dry runs
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # sanitized
    platform_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # container/publish id
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    post: Mapped["ContentPost"] = relationship("ContentPost", lazy="selectin")


class PostMetric(Base):
    """Instagram metric snapshot for a published post. Append-only."""

    __tablename__ = "post_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    likes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    post: Mapped["ContentPost"] = relationship("ContentPost", lazy="selectin")


class Comment(Base):
    """A platform comment on a published post. Reply drafts are human-sent (PLAN §10)."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    author_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    commented_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reply_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")  # none/drafted/sent/ignored
    reply_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reply_external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    post: Mapped["ContentPost"] = relationship("ContentPost", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_comment_platform_external"),
    )


class WorkerHeartbeat(Base):
    """Single row (id=1): the worker proves it is alive here."""

    __tablename__ = "worker_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class PostingRule(Base):
    """Weekly posting rule for one (profile, platform) — data, not env (PLAN §5/§6)."""

    __tablename__ = "posting_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    posts_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    min_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    window_start: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00")  # local HH:MM
    window_end: Mapped[str] = mapped_column(String(5), nullable=False, default="21:00")
    enforce_window: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_gap_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="Europe/London")  # IANA
    auto_topup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("model_profile_id", "platform", name="uq_rule_profile_platform"),
    )

    profile: Mapped["ModelProfile"] = relationship("ModelProfile", lazy="selectin")


class NotificationLog(Base):
    """Delivery log only: no message bodies with secrets (PLAN §6)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(30), nullable=False)
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


# =========================================================================
# Phase 3: Fanvue (PLAN_PHASE3 §5)
# =========================================================================

class FanChat(Base):
    """One Fanvue fan conversation. Only uuid/handle/display name are stored about the fan (H8)."""

    __tablename__ = "fan_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fan_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    handle: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="needs_reply", index=True)  # needs_reply/escalated/replied/ignored
    escalation_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    draft_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_generated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    messages: Mapped[List["FanMessage"]] = relationship(
        "FanMessage", back_populates="chat", lazy="selectin", cascade="all, delete-orphan",
        order_by="FanMessage.sent_at",
    )

    __table_args__ = (
        UniqueConstraint("model_profile_id", "fan_uuid", name="uq_fan_chat_profile_fan"),
    )


class FanMessage(Base):
    """A message in a fan chat. Text is purged after FAN_DATA_RETENTION_DAYS."""

    __tablename__ = "fan_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fan_chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fanvue_message_uuid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # in | out
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # Fanvue `type`
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    ppv_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    media_asset_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    sent_via: Mapped[str] = mapped_column(String(10), nullable=False, default="fanvue")  # fanvue | studio
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    chat: Mapped["FanChat"] = relationship("FanChat", back_populates="messages")


class TrackingLink(Base):
    """A Fanvue tracking link (bio/campaign attribution). Counts are refreshed by the worker."""

    __tablename__ = "tracking_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fanvue_uuid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # Fanvue externalSocialPlatform
    link_url: Mapped[str] = mapped_column(String(500), nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acquired_subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acquired_followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    earnings_net_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

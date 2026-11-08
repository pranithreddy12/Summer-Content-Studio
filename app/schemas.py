"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    Platform,
    ContentType,
    PostStatus,
    MediaType,
    StorageType,
    PostSource,
    VALID_COMBOS,
)


# =========================================================================
# Base configurations
# =========================================================================

# Database ids: positive and inside SQLite's integer range (0 / negative / 2**70 would be an FK error or overflow = 500)
Id = Annotated[int, Field(ge=1, le=2_147_483_647)]


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        extra="forbid",
    )

    @field_validator("idempotency_key", "batch_key", check_fields=False)
    @classmethod
    def _blank_key_is_no_key(cls, v: Optional[str]) -> Optional[str]:
        return v or None   # "" would collide on the unique index after the first use


class NonNullUpdate(BaseModel):
    """Update schemas: an explicit null is only accepted for fields listed in `nullable`
    (everything else maps to a NOT NULL column and would end in a 500)."""
    nullable: ClassVar[frozenset] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bad = sorted(k for k, v in data.items() if v is None and k not in cls.nullable)
            if bad:
                raise ValueError(f"{', '.join(bad)} cannot be null")
        return data


# =========================================================================
# Profile schemas
# =========================================================================

class ProfileOut(BaseSchema):
    id: int
    slug: str
    name: str
    handles: Dict[str, str]
    persona_description: str
    tone_style: str
    communication_rules: List[str]
    restricted_topics: List[str]
    style_notes: str
    cta_guidelines: str
    is_active: bool
    fan_rules: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("handles", "communication_rules", "restricted_topics", mode="before")
    @classmethod
    def deserialize_json_fields(cls, v):
        """Deserialize JSON strings from DB into dict/list."""
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


PROFILE_TEXT_MAX = 4000

FanWord = Annotated[str, Field(min_length=1, max_length=100)]


class FanRules(BaseSchema):
    """Lee's Fanvue chat rules (PLAN_PHASE3 H5). The fixed safety escalations always apply on top."""
    never_say: List[FanWord] = Field(default_factory=list, max_length=50)        # a draft containing one is refused
    escalate_topics: List[FanWord] = Field(default_factory=list, max_length=50)  # a fan message mentioning one goes to Lee
    notes: str = Field("", max_length=1000)                                      # extra guidance in the AI prompt


class ProfileUpdate(NonNullUpdate, BaseSchema):
    nullable: ClassVar[frozenset] = frozenset({"fan_rules"})
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    # these go into every AI prompt: capped so a paste can't blow up the cost (audit L10)
    persona_description: Optional[str] = Field(None, max_length=PROFILE_TEXT_MAX)
    tone_style: Optional[str] = Field(None, max_length=PROFILE_TEXT_MAX)
    communication_rules: Optional[List[str]] = Field(None, max_length=30)
    restricted_topics: Optional[List[str]] = Field(None, max_length=30)
    style_notes: Optional[str] = Field(None, max_length=PROFILE_TEXT_MAX)
    cta_guidelines: Optional[str] = Field(None, max_length=PROFILE_TEXT_MAX)
    is_active: Optional[bool] = None
    fan_rules: Optional[FanRules] = None

    @field_validator("communication_rules", "restricted_topics", mode="before")
    @classmethod
    def validate_list_items(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("must be a list of strings")
        for item in v:
            if not isinstance(item, str) or not (1 <= len(item) <= 300):
                raise ValueError("Each item must be 1-300 characters")
        return v


# =========================================================================
# Tag schemas
# =========================================================================

class TagOut(BaseSchema):
    id: int
    name: str
    slug: str
    is_category: bool
    created_at: datetime


# =========================================================================
# Asset schemas
# =========================================================================

class AssetCreate(BaseSchema):
    filename: str = Field(..., min_length=1, max_length=255)
    media_type: MediaType
    storage_type: StorageType
    source_ref: Optional[str] = None
    local_relpath: Optional[str] = None
    description: str = Field("", max_length=2000)
    tags: List[str] = Field(default_factory=list, max_length=20)
    model_profile_id: Optional[Id] = None

    @model_validator(mode="after")
    def validate_storage_fields(self) -> "AssetCreate":
        if self.storage_type == StorageType.external_url:
            if not self.source_ref:
                raise ValueError("source_ref required for external_url")
            if not self.source_ref.startswith("https://"):
                raise ValueError("source_ref must start with https://")
        elif self.storage_type == StorageType.local_sync:
            if not self.local_relpath:
                raise ValueError("local_relpath required for local_sync")
        elif self.storage_type == StorageType.upload:
            raise ValueError("Use /assets/upload endpoint for uploads")
        return self


class AssetUpdate(NonNullUpdate, BaseSchema):
    nullable: ClassVar[frozenset] = frozenset({"model_profile_id"})

    filename: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    tags: Optional[List[str]] = Field(None, max_length=20)
    model_profile_id: Optional[Id] = None
    is_active: Optional[bool] = None
    fanvue_only: Optional[bool] = None   # Phase 3 H7: never on Instagram/TikTok


class AssetOut(BaseSchema):
    id: int
    model_profile_id: Optional[int]
    filename: str
    media_type: str
    mime_type: Optional[str]
    storage_type: str
    source_ref: Optional[str]
    local_relpath: Optional[str]
    sha256: Optional[str]
    size_bytes: Optional[int]
    description: str
    is_active: bool
    fanvue_only: bool = False
    imported_at: datetime
    created_at: datetime
    updated_at: datetime
    tags: List[TagOut] = []
    preview_url: Optional[str] = None
    usage_count: int = 0


# =========================================================================
# Generation schemas
# =========================================================================

class GenerateRequest(BaseSchema):
    profile_id: Id
    platform: Platform
    content_type: ContentType
    campaign: str = Field(..., min_length=1, max_length=60)
    asset_id: Optional[Id] = None
    asset_context: str = Field("", max_length=1000)
    seasonal_context: str = Field("", max_length=500)
    tone_variation: str = Field("", max_length=200)
    cta_request: str = Field("", max_length=200)
    extra_restrictions: List[str] = Field(default_factory=list, max_length=10)
    idempotency_key: Optional[str] = Field(None, max_length=64)

    @model_validator(mode="after")
    def validate_platform_content_type(self) -> "GenerateRequest":
        valid_types = VALID_COMBOS.get(self.platform, set())
        if self.content_type not in valid_types:
            raise ValueError(
                f"Invalid content_type '{self.content_type}' for platform '{self.platform}'. "
                f"Valid: {', '.join(t.value for t in valid_types)}"
            )
        return self


class RegenerateRequest(BaseSchema):
    tone_variation: Optional[str] = Field(None, max_length=200)
    extra_instructions: str = Field("", max_length=500)


class GeneratedContent(BaseSchema):
    """Schema for Claude's JSON output (validated post-parse)."""
    content_format: str
    hook: str
    caption: str
    hashtags: List[str]
    call_to_action: str
    content_notes: str


# =========================================================================
# Post schemas
# =========================================================================

class PostCreate(BaseSchema):
    profile_id: Id
    platform: Platform
    content_type: ContentType
    campaign: str = Field(..., min_length=1, max_length=60)
    asset_id: Optional[Id] = None
    hook: str = Field("", max_length=500)
    caption: str = Field(..., min_length=1, max_length=2200)
    hashtags: List[str] = Field(default_factory=list, max_length=30)
    call_to_action: str = Field("", max_length=500)
    content_notes: str = Field("", max_length=2000)
    tags: List[str] = Field(default_factory=list, max_length=20)
    planned_publish_at: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(None, max_length=64)

    @model_validator(mode="after")
    def validate_platform_content_type(self) -> "PostCreate":
        valid_types = VALID_COMBOS.get(self.platform, set())
        if self.content_type not in valid_types:
            raise ValueError(
                f"Invalid content_type '{self.content_type}' for platform '{self.platform}'. "
                f"Valid: {', '.join(t.value for t in valid_types)}"
            )
        return self

    @model_validator(mode="after")
    def validate_planned_publish_at(self) -> "PostCreate":
        if self.planned_publish_at is not None and self.planned_publish_at.tzinfo is None:
            raise ValueError("planned_publish_at must be timezone-aware")
        return self


FanvueAudience = Literal["subscribers", "followers-and-subscribers"]


class PostUpdate(NonNullUpdate, BaseSchema):
    nullable: ClassVar[frozenset] = frozenset({"asset_id", "planned_publish_at", "fanvue_price_cents"})

    hook: Optional[str] = Field(None, max_length=500)
    caption: Optional[str] = Field(None, min_length=1, max_length=2200)
    hashtags: Optional[List[str]] = Field(None, max_length=30)
    call_to_action: Optional[str] = Field(None, max_length=500)
    content_notes: Optional[str] = Field(None, max_length=2000)
    campaign: Optional[str] = Field(None, min_length=1, max_length=60)
    tags: Optional[List[str]] = Field(None, max_length=20)
    asset_id: Optional[Id] = None
    planned_publish_at: Optional[Union[datetime, None]] = None  # explicit null = clear
    # Fanvue posts only (PLAN_PHASE3 §6); explicit null price = free post
    fanvue_audience: Optional[FanvueAudience] = None
    fanvue_price_cents: Optional[int] = Field(None, ge=1, le=1_000_000)

    @field_validator("planned_publish_at", mode="before")
    @classmethod
    def validate_planned_publish_at(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, str):
            from datetime import datetime as _dt
            v = _dt.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("planned_publish_at must be timezone-aware")
        return v


class VariantOut(BaseSchema):
    id: int
    post_id: int
    hook: str
    caption: str
    hashtags: List[str]
    call_to_action: str
    content_notes: str
    content_format: str
    generation_log_id: Optional[int]
    created_at: datetime

    @field_validator("hashtags", mode="before")
    @classmethod
    def deserialize_hashtags(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class PostOut(BaseSchema):
    id: int
    model_profile_id: int
    media_asset_id: Optional[int]
    platform: str
    content_type: str
    campaign: str
    hook: str
    caption: str
    hashtags: List[str]
    call_to_action: str
    content_notes: str
    status: str
    planned_publish_at: Optional[datetime]
    source: str
    generation_input: Optional[dict]
    idempotency_key: Optional[str]
    approved_at: Optional[datetime]
    archived_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    # ---- Phase 2 (PLAN §6.3) ----
    published_at: Optional[datetime] = None
    published_via: Optional[str] = None
    external_post_id: Optional[str] = None
    external_url: Optional[str] = None
    publish_attempt_count: int = 0
    next_attempt_at: Optional[datetime] = None
    last_publish_error: Optional[str] = None
    planned_by: Optional[str] = None
    group_key: Optional[str] = None
    reminded_at: Optional[datetime] = None
    # ---- Phase 3 ----
    fanvue_audience: Optional[str] = None
    fanvue_price_cents: Optional[int] = None
    tags: List[TagOut] = []
    asset: Optional[AssetOut] = None
    variant_count: int = 0

    @field_validator("hashtags", "generation_input", mode="before")
    @classmethod
    def deserialize_json_fields(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


# =========================================================================
# Error schemas
# =========================================================================

class AccountOut(BaseSchema):
    """A connected platform account. Deliberately has NO token fields (PLAN §15)."""

    id: int
    model_profile_id: int
    platform: str
    username: str
    status: str
    token_expires_at: Optional[datetime] = None
    scopes: List[str] = []
    last_error: Optional[str] = None


class AttemptOut(BaseSchema):
    id: int
    post_id: int
    attempt_no: int
    trigger: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    success: Optional[bool] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    platform_ref: Optional[str] = None
    http_status: Optional[int] = None


class PublishRequest(BaseSchema):
    override_window: bool = False


class CommentOut(BaseSchema):
    id: int
    post_id: int
    platform: str
    external_id: str
    author_username: Optional[str] = None
    text: str
    commented_at: Optional[datetime] = None
    reply_status: str
    reply_text: str = ""
    reply_external_id: Optional[str] = None


class DraftReplyOut(BaseSchema):
    comment: CommentOut
    suggest_ignore: bool = False


class DraftReplyRequest(BaseSchema):
    instructions: str = Field("", max_length=300)


class SendReplyRequest(BaseSchema):
    text: str = Field(..., min_length=1, max_length=1000)


class MetricOut(BaseSchema):
    id: int
    post_id: int
    fetched_at: datetime
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    views: Optional[int] = None
    saves: Optional[int] = None


class MarkPostedRequest(BaseSchema):
    """Body for POST /posts/{id}/mark-posted - external URL is optional."""

    external_url: Optional[str] = Field(None, max_length=500)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorDetail


class RandomDraftRequest(BaseSchema):
    """Pick random unused library assets and draft one post for each (max 5)."""
    platform: Platform
    campaign: str = Field("auto", min_length=1, max_length=60)  # "auto" = asset's category tag, else lifestyle
    count: int = Field(1, ge=1, le=5)
    media_type: Optional[MediaType] = None
    tag: Optional[str] = Field(None, max_length=60)
    include_used: bool = False
    profile_id: Optional[Id] = None
    seasonal_context: str = Field("", max_length=500)
    batch_key: Optional[str] = Field(None, max_length=40)  # makes a resubmitted batch idempotent
    both_platforms: bool = False  # videos -> Instagram + TikTok pair sharing group_key


class RandomDraftOut(BaseSchema):
    posts: List[PostOut]
    errors: List[Dict[str, Any]] = []
    available: int


# =========================================================================
# Phase 3: fan inbox (PLAN_PHASE3 §11)
# =========================================================================

class FanMessageOut(BaseSchema):
    id: int
    direction: str
    text: Optional[str]
    message_type: Optional[str]
    sent_at: Optional[datetime]
    ppv_price_cents: Optional[int]
    media_asset_id: Optional[int]
    sent_via: str


class FanChatOut(BaseSchema):
    id: int
    model_profile_id: int
    fan_uuid: str
    handle: Optional[str]
    display_name: Optional[str]
    last_message_at: Optional[datetime]
    unread_count: int
    status: str
    escalation_reason: Optional[str]
    draft_text: Optional[str]
    draft_generated_at: Optional[datetime]


class FanChatDetailOut(FanChatOut):
    messages: List[FanMessageOut] = []


class FanDraftRequest(BaseSchema):
    instructions: str = Field("", max_length=300)


class FanSendRequest(BaseSchema):
    text: str = Field(..., min_length=1, max_length=1000)
    asset_id: Optional[Id] = None
    price_cents: Optional[int] = Field(None, ge=1, le=1_000_000)


class TrackingLinkCreate(BaseSchema):
    name: str = Field(..., min_length=3, max_length=60)
    platform: str = Field(..., max_length=20)


class TrackingLinkOut(BaseSchema):
    id: int
    name: str
    platform: str
    link_url: str
    clicks: int
    acquired_subscribers: int
    acquired_followers: int
    earnings_net_cents: Optional[int]
    synced_at: Optional[datetime]

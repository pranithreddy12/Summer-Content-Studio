"""Planner API (PLAN_PHASE2 §12): rules CRUD (validated), plan triggers,
week view."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.models import PostingRule, ContentPost, ModelProfile, PostStatus
from app.schemas import Id, NonNullUpdate
from app.services import AppError
from app.services.planner import plan_week, validate_rule_values

router = APIRouter(prefix="/planner", tags=["planner"])


class RuleUpdate(NonNullUpdate):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    posts_per_week: Optional[int] = None
    min_per_day: Optional[int] = None
    max_per_day: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    min_gap_minutes: Optional[int] = None
    timezone: Optional[str] = None
    auto_topup: Optional[bool] = None
    is_active: Optional[bool] = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_profile_id: int
    platform: str
    posts_per_week: int
    min_per_day: int
    max_per_day: int
    window_start: str
    window_end: str
    min_gap_minutes: int
    timezone: str
    auto_topup: bool
    is_active: bool


class PlanRequest(BaseModel):
    profile_id: Id
    platform: str
    start_day: Optional[str] = None  # ISO date, default: tomorrow (rule tz)


def _get_rule(db: Session, rule_id: int) -> PostingRule:
    rule = db.get(PostingRule, rule_id)
    if not rule:
        raise AppError("not_found", "Posting rule not found", status=404)
    return rule


def _validate_update(db: Session, rule: PostingRule, update: RuleUpdate) -> PostingRule:
    """Apply allowed fields, validating the resulting rule (PLAN §9a)."""
    data = update.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(rule, field, value)
    rule.updated_at = utcnow()
    validate_rule_values(
        rule.posts_per_week, rule.min_per_day, rule.max_per_day,
        rule.window_start, rule.window_end, rule.min_gap_minutes, rule.timezone,
    )
    return rule


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(profile_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = select(PostingRule).order_by(PostingRule.platform)
    if profile_id is not None:
        query = query.where(PostingRule.model_profile_id == profile_id)
    return db.execute(query).scalars().all()


@router.put("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, update: RuleUpdate, db: Session = Depends(get_db)):
    rule = _get_rule(db, rule_id)
    _validate_update(db, rule, update)
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/plan")
async def plan_endpoint(req: PlanRequest, db: Session = Depends(get_db)):
    rule = db.execute(
        select(PostingRule).where(
            PostingRule.model_profile_id == req.profile_id,
            PostingRule.platform == req.platform,
        )
    ).scalar_one_or_none()
    if not rule:
        raise AppError("not_found", "Posting rule not found", status=404)
    if not rule.is_active:
        raise AppError("rule_inactive", "The posting rule is inactive.", status=409)

    start_day: Optional[date] = None
    if req.start_day:
        try:
            start_day = date.fromisoformat(req.start_day)
        except ValueError:
            raise AppError("validation_error", "start_day must be an ISO date (YYYY-MM-DD)", status=422)

    now = utcnow()
    result = plan_week(db, rule, now, start_day=start_day)
    return {
        "slots": len(result.slots),
        "filled": len(result.filled),
        "unfilled": len(result.unfilled),
        "topup_drafts": len(result.topup_drafts),
    }


@router.get("/week")
async def week_endpoint(profile_id: int, platform: str, start: Optional[str] = None,
                        db: Session = Depends(get_db)):
    rule = db.execute(
        select(PostingRule).where(
            PostingRule.model_profile_id == profile_id,
            PostingRule.platform == platform,
        )
    ).scalar_one_or_none()
    if not rule:
        raise AppError("not_found", "Posting rule not found", status=404)

    tz_name = rule.timezone
    if start:
        try:
            start_day = date.fromisoformat(start)
        except ValueError:
            raise AppError("validation_error", "start must be an ISO date (YYYY-MM-DD)", status=422)
    else:
        from zoneinfo import ZoneInfo

        start_day = utcnow().astimezone(ZoneInfo(tz_name)).date()

    # Slots + assigned posts for the 7-day window
    from zoneinfo import ZoneInfo

    tzinfo = ZoneInfo(tz_name)
    week_start_local = datetime.combine(start_day, time(0, 0), tzinfo=tzinfo)
    week_end_local = week_start_local + timedelta(days=7)
    week_start = week_start_local.astimezone(timezone.utc)
    week_end = week_end_local.astimezone(timezone.utc)

    posts = db.execute(
        select(ContentPost).where(
            ContentPost.model_profile_id == profile_id,
            ContentPost.platform == platform,
            ContentPost.status.in_([PostStatus.scheduled.value, PostStatus.published.value,
                                    PostStatus.sent_to_drafts.value, PostStatus.publishing.value]),
            ContentPost.planned_publish_at.isnot(None),
            ContentPost.planned_publish_at >= week_start,
            ContentPost.planned_publish_at < week_end,
        ).order_by(ContentPost.planned_publish_at.asc())
    ).scalars().all()

    return {
        "start_day": start_day.isoformat(),
        "timezone": tz_name,
        "items": [
            {
                "post_id": p.id,
                "planned_publish_at": p.planned_publish_at.isoformat() if p.planned_publish_at else None,
                "local_day": p.planned_publish_at.astimezone(tzinfo).date().isoformat() if p.planned_publish_at else None,
                "local_time": p.planned_publish_at.astimezone(tzinfo).strftime("%H:%M") if p.planned_publish_at else None,
                "planned_by": p.planned_by,
                "status": p.status,
                "group_key": p.group_key,
                "caption": (p.caption or "")[:80],
            }
            for p in posts
        ],
    }

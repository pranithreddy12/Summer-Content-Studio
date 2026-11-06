"""Dashboard web routes."""

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from app.api.dependencies import get_claude_client
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_claude_client, get_platform_http
from app.config import get_settings
from app.db import get_db, utcnow
from app.models import (
    ModelProfile, Tag, MediaAsset, ContentPost, ContentVariant, PostStatus, PostSource
)
from app.schemas import GenerateRequest, PostCreate, PostUpdate, RandomDraftRequest, RegenerateRequest
from app.security import generation_rate_limit, get_or_create_tags, safe_relpath
from app.services.posts import (
    list_posts, get_post, approve_post, archive_post, restore_post,
    apply_variant, stats, update_post, mark_posted
)
from app.services.generation import generate_draft, regenerate
from app.services.publishing import render_caption
from app.services.random_draft import eligible_assets

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


def local_time(value, tz_name: Optional[str] = None):
    """UTC datetime (naive = UTC) -> aware datetime in the display zone (APP_TIMEZONE or `tz_name`)."""
    from zoneinfo import ZoneInfo
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(tz_name or get_settings().APP_TIMEZONE))


templates.env.filters["local"] = local_time

# One source for every platform/content-type picker (Fanvue included since Phase 3)
from app.prompts.platforms import CONTENT_TYPES as _CONTENT_TYPES  # noqa: E402

UI_CONTENT_TYPES = {p.value: [c.value for c in types] for p, types in _CONTENT_TYPES.items()}
UI_PLATFORMS = list(UI_CONTENT_TYPES)


def _template_response(template_name: str, context: dict, status_code: int = 200):
    """New-style TemplateResponse wrapper (starlette >= 1.0 signature)."""
    request = context.get("request")
    context.setdefault("settings", get_settings())  # mode badge / banners in every page
    return templates.TemplateResponse(
        request=request, name=template_name, context=context, status_code=status_code
    )


# =========================================================================
# Helper functions
# =========================================================================

def get_flash(request: Request) -> Optional[dict]:
    """Extract flash message from query params."""
    msg = request.query_params.get("msg")
    kind = request.query_params.get("kind", "ok")
    if msg:
        return {"message": msg, "kind": kind}
    return None


def redirect_with_flash(url: str, message: str, kind: str = "ok") -> RedirectResponse:
    """Create redirect with flash message in query params."""
    from urllib.parse import quote
    if isinstance(message, dict):  # HTTPException(detail={"code", "message"})
        message = str(message.get("message") or message.get("code") or "Request failed")
    return RedirectResponse(f"{url}?msg={quote(str(message))}&kind={kind}", status_code=status.HTTP_303_SEE_OTHER)


def get_settings_dep():
    return get_settings()


# =========================================================================
# Overview / Dashboard
# =========================================================================

def _unanswered(db) -> int:
    from app.services.engagement import unanswered_count

    return unanswered_count(db)


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    db: Session = Depends(get_db),
):
    """Dashboard overview page."""
    dashboard_stats = stats(db)
    recent_posts = db.execute(
        select(ContentPost)
        .options(joinedload(ContentPost.asset), joinedload(ContentPost.profile))
        .where(ContentPost.status != PostStatus.archived.value)
        .order_by(desc(ContentPost.created_at))
        .limit(10)
    ).scalars().all()

    upcoming = db.execute(
        select(ContentPost)
        .options(joinedload(ContentPost.asset), joinedload(ContentPost.profile))
        .where(
            ContentPost.status == PostStatus.scheduled.value,
            ContentPost.planned_publish_at.isnot(None),
            ContentPost.planned_publish_at > utcnow()
        )
        .order_by(ContentPost.planned_publish_at)
        .limit(7)
    ).scalars().all()

    from app.services.scheduler import worker_status

    worker_last_seen, worker_ok = worker_status(db)

    return _template_response("overview.html", {
        "request": request,
        "stats": dashboard_stats,
        "recent_posts": recent_posts,
        "upcoming": upcoming,
        "flash": get_flash(request),
        "worker_last_seen": worker_last_seen,
        "worker_ok": worker_ok,
        "unanswered": _unanswered(db),
    })


# =========================================================================
# Media Library
# =========================================================================

@router.get("/assets", response_class=HTMLResponse)
async def assets_list(
    request: Request,
    q: str = "",
    tag: str = "",
    media_type: str = "",
    storage_type: str = "",
    unused: bool = False,
    usage: str = "",
    include_inactive: bool = False,
    page: int = 1,
    db: Session = Depends(get_db),
):
    """Media library page with filters."""
    from app.services.assets import list_assets, get_usage_counts

    limit = 50
    offset = (page - 1) * limit
    unused = unused or usage == "unused"  # legacy ?unused=true still works

    result = list_assets(
        db,
        q=q or None,
        tags=[tag] if tag else None,
        media_type=media_type or None,
        storage_type=storage_type or None,
        unused=unused,
        used=(usage == "used"),
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    counts = get_usage_counts(db, [a.id for a in result["items"]])
    for a in result["items"]:
        a.usage_count = counts.get(a.id, 0)  # plain attribute read by assets.html

    # Get all category tags for filter dropdown
    category_tags = db.execute(
        select(Tag).where(Tag.is_category == True).order_by(Tag.name)
    ).scalars().all()

    return _template_response("assets.html", {
        "request": request,
        "assets": result["items"],
        "total": result["total"],
        "page": page,
        "pages": (result["total"] + limit - 1) // limit,
        "filters": {
            "q": q, "tag": tag, "media_type": media_type,
            "storage_type": storage_type, "usage": "unused" if unused else usage,
            "include_inactive": include_inactive,
        },
        "category_tags": category_tags,
        "flash": get_flash(request),
    })


@router.get("/assets/new", response_class=HTMLResponse)
async def asset_new_form(
    request: Request,
    db: Session = Depends(get_db),
):
    """New asset form page."""
    profiles = db.execute(select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.name)).scalars().all()
    category_tags = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()
    return _template_response("asset_new.html", {
        "request": request,
        "profiles": profiles,
        "category_tags": category_tags,
        "flash": get_flash(request),
    })


@router.post("/assets/new")
async def asset_create(
    request: Request,
    # Upload form
    file: Optional[UploadFile] = File(None),
    filename: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    model_profile_id: Optional[int] = Form(None),
    # External URL form
    external_url: str = Form(""),
    external_filename: str = Form(""),
    external_media_type: str = Form(""),
    external_description: str = Form(""),
    external_tags: str = Form(""),
    external_model_profile_id: Optional[int] = Form(None),
    # Local sync form
    local_relpath: str = Form(""),
    local_filename: str = Form(""),
    local_media_type: str = Form(""),
    local_description: str = Form(""),
    local_tags: str = Form(""),
    local_model_profile_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Create asset via upload, external URL, or local sync registration."""
    from app.services.assets import upload_asset, register_asset

    form_data = await request.form()
    form_type = form_data.get("form_type", "upload")

    try:
        if form_type == "upload":
            upload_file = form_data.get("file")
            if not upload_file or not hasattr(upload_file, "filename") or not upload_file.filename:
                return redirect_with_flash("/assets/new", "No file selected", "error")

            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            asset = await upload_asset(
                db, upload_file, description, tag_list, model_profile_id
            )
            return redirect_with_flash(f"/assets/{asset.id}", "Asset uploaded successfully")

        elif form_type == "external_url":
            from app.schemas import AssetCreate
            from app.models import MediaType, StorageType

            tag_list = [t.strip() for t in external_tags.split(",") if t.strip()]
            req = AssetCreate(
                filename=external_filename,
                media_type=MediaType(external_media_type),
                storage_type=StorageType.external_url,
                source_ref=external_url,
                description=external_description,
                tags=tag_list,
                model_profile_id=external_model_profile_id,
            )
            asset = register_asset(db, req)
            return redirect_with_flash(f"/assets/{asset.id}", "Asset registered successfully")

        elif form_type == "local_sync":
            from app.schemas import AssetCreate
            from app.models import MediaType, StorageType

            tag_list = [t.strip() for t in local_tags.split(",") if t.strip()]
            req = AssetCreate(
                filename=local_filename,
                media_type=MediaType(local_media_type),
                storage_type=StorageType.local_sync,
                local_relpath=local_relpath,
                description=local_description,
                tags=tag_list,
                model_profile_id=local_model_profile_id,
            )
            asset = register_asset(db, req)
            return redirect_with_flash(f"/assets/{asset.id}", "Asset registered successfully")

    except HTTPException as e:
        return redirect_with_flash("/assets/new", e.detail, "error")
    except ValueError as e:
        return redirect_with_flash("/assets/new", str(e), "error")
    except Exception as e:
        return redirect_with_flash("/assets/new", str(e), "error")

    return redirect_with_flash("/assets/new", "Unknown form type", "error")


@router.get("/assets/{asset_id:int}", response_class=HTMLResponse)
async def asset_detail(
    request: Request,
    asset_id: int,
    db: Session = Depends(get_db),
):
    """Asset detail page."""
    asset = db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Load related posts
    posts = db.execute(
        select(ContentPost)
        .options(joinedload(ContentPost.profile))
        .where(ContentPost.media_asset_id == asset_id)
        .order_by(desc(ContentPost.created_at))
    ).scalars().all()

    category_tags = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()
    all_tags = db.execute(select(Tag).order_by(Tag.name)).scalars().all()
    profiles = db.execute(select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.name)).scalars().all()

    return _template_response("asset_detail.html", {
        "request": request,
        "asset": asset,
        "posts": posts,
        "category_tags": category_tags,
        "all_tags": all_tags,
        "profiles": profiles,
        "flash": get_flash(request),
    })


@router.post("/assets/{asset_id:int}")
async def asset_update(
    request: Request,
    asset_id: int,
    filename: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    model_profile_id: Optional[int] = Form(None),
    is_active: bool = Form(False),
    fanvue_only: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Update asset metadata."""
    from app.services.assets import update_asset

    asset = db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    try:
        update_asset(db, asset_id, filename, description, tag_list, model_profile_id, is_active, fanvue_only)
        return redirect_with_flash(f"/assets/{asset_id}", "Asset updated successfully")
    except Exception as e:
        return redirect_with_flash(f"/assets/{asset_id}", str(e), "error")


@router.post("/assets/{asset_id}/archive")
async def asset_archive(
    asset_id: int,
    db: Session = Depends(get_db),
):
    """Archive (soft delete) an asset."""
    from app.services.assets import archive_asset

    asset = db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    archive_asset(db, asset_id)
    return redirect_with_flash("/assets", "Asset archived")


@router.post("/assets/{asset_id}/delete")
def asset_delete(
    asset_id: int,
    force: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Permanently delete an uploaded/link asset (synced Dropbox files can only be archived)."""
    from app.services import AppError
    from app.services.assets import delete_asset_permanently

    try:
        out = delete_asset_permanently(db, asset_id, force=force)
    except AppError as e:
        target = "/assets" if e.status == 404 else f"/assets/{asset_id}"
        return redirect_with_flash(target, e.message, "error")
    note = " and its file" if out["file_removed"] else ""
    return redirect_with_flash("/assets", f"Asset deleted{note}")


@router.post("/assets/sync")
def assets_sync(
    db: Session = Depends(get_db),
):
    """Sync media from MEDIA_SYNC_DIR."""
    from app.services.assets import sync_media_dir

    result = sync_media_dir(db)
    return redirect_with_flash("/assets", f"Sync complete: {result['added']} added, {result['skipped']} skipped")


# =========================================================================
# Content Generator
# =========================================================================

@router.get("/generate", response_class=HTMLResponse)
async def generate_form(
    request: Request,
    asset_id: Optional[int] = None,
    tag: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Content generator page."""
    profiles = db.execute(select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.name)).scalars().all()

    # Build asset query
    asset_query = select(MediaAsset).where(MediaAsset.is_active == True)
    if asset_id:
        asset_query = asset_query.where(MediaAsset.id == asset_id)
    elif tag:
        asset_query = asset_query.join(MediaAsset.tags).where(Tag.slug == tag)
    asset_query = asset_query.order_by(desc(MediaAsset.imported_at)).limit(50)

    assets = db.execute(asset_query).scalars().all()

    # Campaigns (category tags)
    campaigns = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()

    # Preselect asset if asset_id provided
    selected_asset = None
    if asset_id:
        selected_asset = db.get(MediaAsset, asset_id)

    return _template_response("generate.html", {
        "request": request,
        "profiles": profiles,
        "assets": assets,
        "campaigns": campaigns,
        "selected_asset": selected_asset,
        "platforms": UI_PLATFORMS,
        "content_types": UI_CONTENT_TYPES,
        "batch_key": uuid.uuid4().hex[:20],
        "eligible_count": len(eligible_assets(db)),
        "flash": get_flash(request),
    })


@router.post("/generate")
def generate_content(
    request: Request,
    profile_id: int = Form(...),
    platform: str = Form(...),
    content_type: str = Form(...),
    campaign: str = Form(...),
    asset_id: Optional[int] = Form(None),
    asset_context: str = Form(""),
    seasonal_context: str = Form(""),
    tone_variation: str = Form(""),
    cta_request: str = Form(""),
    extra_restrictions: str = Form(""),
    idempotency_key: str = Form(""),
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """Generate content via service (same code path as the API).

    Rate-limited on the dashboard (separate call site from the API), so the UI
    cannot exhaust the generation budget.
    """
    generation_rate_limit(request)

    from app.services.generation import generate_draft

    # Parse extra restrictions
    extra_restrictions_list = [r.strip() for r in extra_restrictions.split("\n") if r.strip()]

    # Generate idempotency key if not provided
    if not idempotency_key:
        idempotency_key = uuid.uuid4().hex

    try:
        req = GenerateRequest(
            profile_id=profile_id,
            platform=platform,
            content_type=content_type,
            campaign=campaign,
            asset_id=asset_id,
            asset_context=asset_context,
            seasonal_context=seasonal_context,
            tone_variation=tone_variation,
            cta_request=cta_request,
            extra_restrictions=extra_restrictions_list,
            idempotency_key=idempotency_key,
        )
    except ValueError as e:  # pydantic ValidationError: e.g. a platform / content-type mismatch
        return redirect_with_flash("/generate", "; ".join(str(x["msg"]) for x in e.errors()) if hasattr(e, "errors") else str(e), "error")

    try:
        post = generate_draft(db, req, client)
        return redirect_with_flash(f"/posts/{post.id}", "Content generated successfully")
    except HTTPException as e:
        # Re-render form with error (show a human message, not the raw detail dict).
        message = e.detail
        if isinstance(message, dict):
            message = message.get("message", message.get("code", "Error"))

        profiles = db.execute(select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.name)).scalars().all()
        assets = db.execute(select(MediaAsset).where(MediaAsset.is_active == True).order_by(desc(MediaAsset.imported_at)).limit(50)).scalars().all()
        campaigns = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()

        return _template_response("generate.html", {
            "request": request,
            "profiles": profiles,
            "assets": assets,
            "campaigns": campaigns,
            "platforms": UI_PLATFORMS,
            "content_types": UI_CONTENT_TYPES,
            "error": message,
            "form_data": req.model_dump(),
        }, status_code=e.status_code)
    except Exception as e:
        return redirect_with_flash("/generate", str(e), "error")


# =========================================================================
# Content Queue / Posts
# =========================================================================

@router.post("/generate/random")
def generate_random_form(
    request: Request,
    platform: str = Form(...),
    campaign: str = Form("auto"),
    count: int = Form(1),
    media_type: str = Form(""),
    include_used: bool = Form(False),
    seasonal_context: str = Form(""),
    batch_key: str = Form(""),
    both_platforms: bool = Form(False),
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """One click: random unused Dropbox/uploaded asset(s) -> AI looks at them -> drafts to review."""
    from app.services import AppError
    from app.services.random_draft import generate_random_drafts

    generation_rate_limit(request)
    # keep the pair flow's idempotency keys separate from the single-platform flow
    if both_platforms and batch_key:
        batch_key = f"pair-{batch_key}"
    try:
        req = RandomDraftRequest(
            platform=platform, campaign=campaign or "auto", count=count, media_type=media_type or None,
            include_used=include_used, seasonal_context=seasonal_context, batch_key=batch_key or None,
            both_platforms=both_platforms,
        )
        out = generate_random_drafts(db, req, client)
    except AppError as e:
        return redirect_with_flash("/generate", e.message, "error")
    except ValueError as e:  # pydantic validation
        return redirect_with_flash("/generate", f"Invalid options: {e}", "error")
    posts, errors = out["posts"], out["errors"]
    if errors:
        msg = f"Created {len(posts)} of {req.count} drafts; stopped: {errors[-1]['message']}"
        return redirect_with_flash("/posts", msg, "error")
    if len(posts) == 1:
        return redirect_with_flash(f"/posts/{posts[0].id}", "Random draft created - review it below")
    return redirect_with_flash("/posts", f"Created {len(posts)} drafts from random assets - review each one")


@router.get("/posts", response_class=HTMLResponse)
async def posts_list(
    request: Request,
    platform: str = "",
    status_filter: str = "",
    campaign: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    """Content queue page."""
    limit = 50
    offset = (page - 1) * limit

    # Build filters
    status_list = [status_filter] if status_filter else None
    if status_filter == "archived":
        status_list = ["archived"]

    result = list_posts(
        db,
        platform=platform or None,
        status=status_list,
        campaign=campaign or None,
        limit=limit,
        offset=offset,
    )

    # Filter options (PLAN §13: queue filters add publishing, sent_to_drafts, failed)
    platforms = UI_PLATFORMS
    statuses = ["draft", "approved", "scheduled", "publishing", "sent_to_drafts", "published", "failed", "archived"]
    campaigns = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()

    return _template_response("posts.html", {
        "request": request,
        "posts": result["items"],
        "total": result["total"],
        "page": page,
        "pages": (result["total"] + limit - 1) // limit,
        "filters": {"platform": platform, "status": status_filter, "campaign": campaign},
        "platforms": platforms,
        "statuses": statuses,
        "campaigns": campaigns,
        "flash": get_flash(request),
        "timezone": get_settings().APP_TIMEZONE,
    })


@router.get("/posts/{post_id}", response_class=HTMLResponse)
async def post_detail(
    request: Request,
    post_id: int,
    db: Session = Depends(get_db),
):
    """Post detail page."""
    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Load variants
    variants = db.execute(
        select(ContentVariant)
        .where(ContentVariant.post_id == post_id)
        .order_by(desc(ContentVariant.created_at))
    ).scalars().all()

    campaigns = db.execute(select(Tag).where(Tag.is_category == True).order_by(Tag.name)).scalars().all()
    all_tags = db.execute(select(Tag).order_by(Tag.name)).scalars().all()

    from app.services.engagement import latest_metrics
    from app.services.publishing import list_attempts, preflight_reasons

    return _template_response("post_detail.html", {
        "request": request,
        "post": post,
        "attempts": list_attempts(db, post_id),
        "metrics": latest_metrics(db, post_id),
        "preflight": preflight_reasons(db, post, utcnow()) if post.status in ("approved", "scheduled", "failed") else [],
        "variants": variants,
        "campaigns": campaigns,
        "all_tags": all_tags,
        "timezone": get_settings().APP_TIMEZONE,
        "full_caption": render_caption(post),
        "flash": get_flash(request),
    })


@router.post("/posts/{post_id}")
async def post_update(
    request: Request,
    post_id: int,
    hook: str = Form(""),
    caption: str = Form(""),
    hashtags: str = Form(""),
    call_to_action: str = Form(""),
    content_notes: str = Form(""),
    campaign: str = Form(""),
    tags: str = Form(""),
    asset_id: Optional[int] = Form(None),
    planned_publish_at: Optional[str] = Form(None),
    fanvue_audience: Optional[str] = Form(None),
    fanvue_price: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Update post content."""
    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    hashtag_list = [h.strip().lstrip("#") for h in hashtags.replace(",", " ").split() if h.strip()]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # The form posts every field; send only what the user changed, so a plain Save never
    # un-approves a post and an untouched time is never re-interpreted.
    changes: dict = {}
    if hook != (post.hook or ""):
        changes["hook"] = hook
    if caption != (post.caption or ""):
        changes["caption"] = caption
    if hashtag_list != list(post.hashtags or []):
        changes["hashtags"] = hashtag_list
    if call_to_action != (post.call_to_action or ""):
        changes["call_to_action"] = call_to_action
    if content_notes != (post.content_notes or ""):
        changes["content_notes"] = content_notes
    if campaign and campaign != (post.campaign or ""):
        changes["campaign"] = campaign
    if sorted(tag_list) != sorted(t.name for t in post.tags if not t.is_category):
        changes["tags"] = tag_list
    if asset_id != post.media_asset_id:
        changes["asset_id"] = asset_id
    if post.platform == "fanvue":
        if fanvue_audience and fanvue_audience != (post.fanvue_audience or "subscribers"):
            changes["fanvue_audience"] = fanvue_audience
        price_text = (fanvue_price or "").strip().lstrip("$")
        try:
            cents = round(float(price_text) * 100) if price_text else None
        except (ValueError, OverflowError):   # "abc", "inf", "nan"
            return redirect_with_flash(f"/posts/{post_id}", "Price must be a number like 9.99.", "error")
        if cents != post.fanvue_price_cents:
            changes["fanvue_price_cents"] = cents

    current_local = local_time(post.planned_publish_at)
    current_text = current_local.strftime("%Y-%m-%dT%H:%M") if current_local else ""
    submitted = (planned_publish_at or "").strip()
    if submitted != current_text:
        if not submitted:
            changes["planned_publish_at"] = None
        else:
            try:
                from zoneinfo import ZoneInfo
                naive = datetime.fromisoformat(submitted)
                changes["planned_publish_at"] = naive.replace(tzinfo=ZoneInfo(get_settings().APP_TIMEZONE)).astimezone(timezone.utc)
            except ValueError:
                return redirect_with_flash(f"/posts/{post_id}", "Planned publish time is not a valid date/time.", "error")

    try:
        if changes:
            update_post(db, post_id, PostUpdate(**changes))
        return redirect_with_flash(f"/posts/{post_id}", "Post updated successfully" if changes else "No changes to save")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/variants/{variant_id}/apply")
async def post_apply_variant(
    post_id: int,
    variant_id: int,
    db: Session = Depends(get_db),
):
    """Apply a variant to the post."""
    try:
        apply_variant(db, post_id, variant_id)
        return redirect_with_flash(f"/posts/{post_id}", "Variant applied successfully")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/approve")
async def post_approve(
    post_id: int,
    db: Session = Depends(get_db),
):
    """Approve a post."""
    try:
        approve_post(db, post_id)
        return redirect_with_flash(f"/posts/{post_id}", "Post approved")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/archive")
async def post_archive(
    post_id: int,
    db: Session = Depends(get_db),
):
    """Archive a post."""
    try:
        archive_post(db, post_id)
        return redirect_with_flash("/posts", "Post archived")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/posted")
async def post_marked_posted(
    request: Request,
    post_id: int,
    external_url: str = Form(""),
    db: Session = Depends(get_db),
):
    """Mark as posted: the human asserts they posted the content by hand.

    Works in every PUBLISHING_MODE (PLAN §8) - the human is the source of truth.
    """
    from app.services import AppError

    try:
        mark_posted(db, post_id, external_url=external_url or None)
        return redirect_with_flash(f"/posts/{post_id}", "Marked as posted - great work")
    except AppError as e:
        return redirect_with_flash(f"/posts/{post_id}", e.message, "error")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/restore")
async def post_restore(
    post_id: int,
    db: Session = Depends(get_db),
):
    """Restore an archived post."""
    try:
        restore_post(db, post_id)
        return redirect_with_flash(f"/posts/{post_id}", "Post restored")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


@router.post("/posts/{post_id}/regenerate")
def post_regenerate(
    request: Request,
    post_id: int,
    tone_variation: str = Form(""),
    extra_instructions: str = Form(""),
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """Generate a new variant for a post (same service as the API).

    Rate-limited on the dashboard (separate call site from the API), so repeated
    clicks cannot exhaust the generation budget.
    """
    generation_rate_limit(request)

    from app.services.generation import regenerate

    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status == PostStatus.archived.value:
        return redirect_with_flash(f"/posts/{post_id}", "Cannot regenerate archived post", "error")

    req = RegenerateRequest(
        tone_variation=tone_variation or None,
        extra_instructions=extra_instructions,
    )

    try:
        variant = regenerate(db, post_id, req, client)
        return redirect_with_flash(f"/posts/{post_id}", "New variant generated")
    except HTTPException as e:
        return redirect_with_flash(f"/posts/{post_id}", e.detail, "error")
    except Exception as e:
        return redirect_with_flash(f"/posts/{post_id}", str(e), "error")


# =========================================================================
# Planner + Calendar (PLAN §13)
# =========================================================================

@router.get("/planner", response_class=HTMLResponse)
async def planner_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Weekly planner page: per-platform rules + next-7-days slot grid."""
    from app.models import PostingRule
    from zoneinfo import ZoneInfo
    from datetime import timedelta

    rules = db.execute(select(PostingRule).order_by(PostingRule.platform)).scalars().all()

    now = utcnow()
    tz = ZoneInfo(get_settings().APP_TIMEZONE)
    weeks = {}
    for rule in rules:
        rule_tz = ZoneInfo(rule.timezone)
        start_day = (now.astimezone(rule_tz)).date()
        days = []
        for i in range(7):
            d = start_day + timedelta(days=i)
            day_start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=rule_tz)
            day_end = day_start + timedelta(days=1)
            posts = db.execute(
                select(ContentPost)
                .options(joinedload(ContentPost.profile), joinedload(ContentPost.asset))
                .where(
                    ContentPost.model_profile_id == rule.model_profile_id,
                    ContentPost.platform == rule.platform,
                    ContentPost.status.in_(
                        [PostStatus.scheduled.value, PostStatus.published.value,
                         PostStatus.sent_to_drafts.value, PostStatus.publishing.value]),
                    ContentPost.planned_publish_at >= day_start,
                    ContentPost.planned_publish_at < day_end,
                ).order_by(ContentPost.planned_publish_at)
            ).scalars().all()
            days.append({"date": d, "posts": posts})
        weeks[rule.platform] = {"rule": rule, "days": days, "tz": rule.timezone}

    return _template_response("planner.html", {
        "request": request,
        "rules": rules,
        "rule_weeks": weeks,
        "flash": get_flash(request),
    })


@router.post("/planner/rules/{rule_id}")
async def planner_rule_update(
    rule_id: int,
    posts_per_week: str = Form(""),
    min_per_day: str = Form(""),
    max_per_day: str = Form(""),
    window_start: str = Form(""),
    window_end: str = Form(""),
    min_gap_minutes: str = Form(""),
    timezone: str = Form(""),
    auto_topup: bool = Form(False),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    from app.models import PostingRule
    from app.services.planner import validate_rule_values

    rule = db.get(PostingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Posting rule not found")

    def _int(source, current):
        try:
            return int(source) if source != "" else current
        except ValueError:
            return current

    rule.posts_per_week = _int(posts_per_week, rule.posts_per_week)
    rule.min_per_day = _int(min_per_day, rule.min_per_day)
    rule.max_per_day = _int(max_per_day, rule.max_per_day)
    rule.min_gap_minutes = _int(min_gap_minutes, rule.min_gap_minutes)
    if window_start:
        rule.window_start = window_start
    if window_end:
        rule.window_end = window_end
    if timezone:
        rule.timezone = timezone
    rule.auto_topup = auto_topup
    rule.is_active = is_active

    try:
        validate_rule_values(rule.posts_per_week, rule.min_per_day, rule.max_per_day,
                             rule.window_start, rule.window_end, rule.min_gap_minutes, rule.timezone)
        db.commit()
        return redirect_with_flash("/planner", "Rule updated")
    except Exception as e:
        db.rollback()
        msg = getattr(e, "message", str(e))
        return redirect_with_flash("/planner", msg, "error")


@router.post("/planner/plan")
async def planner_run(
    platform: str = Form(...),
    profile_id: int = Form(1),
    db: Session = Depends(get_db),
):
    """'Plan next 7 days' button -> planner.plan_week (never approves)."""
    from app.models import PostingRule
    from app.services.planner import plan_week as run_plan_week
    from app.services import AppError

    rule = db.execute(
        select(PostingRule).where(PostingRule.model_profile_id == profile_id,
                                  PostingRule.platform == platform)
    ).scalar_one_or_none()
    if not rule:
        return redirect_with_flash("/planner", "No posting rule for that platform", "error")
    if not rule.is_active:
        return redirect_with_flash("/planner", "The rule is inactive - activate it first", "error")
    try:
        result = run_plan_week(db, rule, utcnow())
    except AppError as e:
        return redirect_with_flash("/planner", e.message, "error")
    msg = (f"Planned {platform}: {len(result.filled)} slots filled, "
           f"{len(result.unfilled)} empty")
    if result.topup_drafts:
        msg += f", {len(result.topup_drafts)} AI drafts added"
    return redirect_with_flash("/planner", msg)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(
    request: Request,
    week: str = "",
    db: Session = Depends(get_db),
):
    """7-column week grid of scheduled + published posts (server-rendered)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from app.services.window import in_window
    from app.services.publishing import get_rule as get_publish_rule

    tz = ZoneInfo(get_settings().APP_TIMEZONE)
    today = utcnow().astimezone(tz).date()
    if week:
        try:
            start = date.fromisoformat(week)
        except ValueError:
            start = today - timedelta(days=today.weekday())
    else:
        start = today - timedelta(days=today.weekday())

    if start.weekday() != 0:  # normalise to Monday
        start = start - timedelta(days=start.weekday())

    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        day_start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        posts = db.execute(
            select(ContentPost)
            .options(joinedload(ContentPost.profile), joinedload(ContentPost.asset))
            .where(
                ContentPost.status.in_(
                    [PostStatus.scheduled.value, PostStatus.published.value,
                     PostStatus.sent_to_drafts.value, PostStatus.publishing.value]),
                ContentPost.planned_publish_at >= day_start,
                ContentPost.planned_publish_at < day_end,
            ).order_by(ContentPost.planned_publish_at)
        ).scalars().all()
        for post in posts:
            rule = get_publish_rule(db, post.model_profile_id, post.platform)
            post.in_window = in_window(rule, post.planned_publish_at)
        days.append({"date": d, "posts": posts})

    return _template_response("calendar.html", {
        "request": request,
        "days": days,
        "timezone": get_settings().APP_TIMEZONE,
        "week_start": start.isoformat(),
        "prev_week": (start - timedelta(days=7)).isoformat(),
        "next_week": (start + timedelta(days=7)).isoformat(),
        "today": today.isoformat(),
        "flash": get_flash(request),
    })


# =========================================================================
# Profile Management
# =========================================================================

@router.get("/profiles", response_class=HTMLResponse)
async def profiles_list(
    request: Request,
    db: Session = Depends(get_db),
):
    """List profiles."""
    profiles = db.execute(select(ModelProfile).order_by(ModelProfile.name)).scalars().all()
    return _template_response("profile.html", {
        "request": request,
        "profiles": profiles,
        "flash": get_flash(request),
    })


@router.get("/profiles/{profile_id}", response_class=HTMLResponse)
async def profile_detail(
    request: Request,
    profile_id: int,
    db: Session = Depends(get_db),
):
    """Profile detail/edit page."""
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return _template_response("profile.html", {
        "request": request,
        "profile": profile,
        "profiles": None,  # Single profile view
        "flash": get_flash(request),
    })


@router.post("/profiles/{profile_id}")
async def profile_update(
    request: Request,
    profile_id: int,
    name: str = Form(""),
    persona_description: str = Form(""),
    tone_style: str = Form(""),
    communication_rules: str = Form(""),
    restricted_topics: str = Form(""),
    style_notes: str = Form(""),
    cta_guidelines: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Update profile."""
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    from pydantic import ValidationError

    from app.schemas import ProfileUpdate

    try:  # same rules as PATCH /api/v1/profiles (blank name, text caps, list sizes)
        data = ProfileUpdate(
            name=name.strip(), persona_description=persona_description, tone_style=tone_style,
            communication_rules=[r.strip() for r in communication_rules.splitlines() if r.strip()],
            restricted_topics=[r.strip() for r in restricted_topics.splitlines() if r.strip()],
            style_notes=style_notes, cta_guidelines=cta_guidelines, is_active=is_active)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        return redirect_with_flash(f"/profiles/{profile_id}", problems, "error")

    try:
        for field, value in data.model_dump().items():
            setattr(profile, field, value)

        db.commit()
        db.refresh(profile)
        return redirect_with_flash(f"/profiles/{profile_id}", "Profile updated successfully")
    except Exception as e:
        return redirect_with_flash(f"/profiles/{profile_id}", str(e), "error")

# =========================================================================
# Phase 2 B5: publish / retry / accounts
# =========================================================================

@router.post("/posts/{post_id}/publish")
def post_publish(post_id: int, override_window: str = Form(""), db: Session = Depends(get_db),
                 http=Depends(get_platform_http)):
    """Publish now (live) or record a dry run. Sync def: a publish can poll the platform for minutes."""
    from app.services import AppError
    from app.services.publishing import publish_post

    try:
        post = publish_post(db, post_id, trigger="manual", http=http, override_window=bool(override_window))
    except AppError as e:
        return redirect_with_flash(f"/posts/{post_id}", e.message, "error")
    msgs = {
        "published": "Published.",
        "sent_to_drafts": "Sent to your TikTok drafts - open TikTok, add the AI label and press Publish.",
        "scheduled": ("Dry run recorded - nothing was sent." if get_settings().PUBLISHING_MODE == "dry_run"
                      else "The platform had a temporary problem; it will be retried automatically."),
        "failed": "Publishing failed: " + (post.last_publish_error or "see attempts below"),
    }
    return redirect_with_flash(f"/posts/{post_id}", msgs.get(post.status, f"Status: {post.status}"),
                               "error" if post.status == "failed" else "ok")


@router.post("/posts/{post_id}/retry")
async def post_retry(post_id: int, db: Session = Depends(get_db)):
    from app.services import AppError
    from app.services.publishing import retry_post

    try:
        retry_post(db, post_id)
    except AppError as e:
        return redirect_with_flash(f"/posts/{post_id}", e.message, "error")
    return redirect_with_flash(f"/posts/{post_id}", "Back on the schedule - it will publish inside the posting window.")


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, db: Session = Depends(get_db)):
    from app.services import accounts as svc

    settings = get_settings()
    profiles = db.execute(select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.id)).scalars().all()
    by_key = {(a.model_profile_id, a.platform): a for a in svc.list_accounts(db)}
    cards = [
        {"profile": p, "platform": platform, "account": by_key.get((p.id, platform)),
         "missing": svc.missing_env(platform, settings)}
        for p in profiles for platform in svc.PLATFORMS
    ]
    return _template_response("accounts.html", {
        "request": request, "cards": cards, "notify_provider": settings.NOTIFY_PROVIDER,
        "notify_ready": settings.notify_configured, "flash": get_flash(request),
    })


@router.post("/accounts/{account_id}/disconnect")
async def accounts_disconnect(account_id: int, db: Session = Depends(get_db)):
    from app.services import AppError
    from app.services import accounts as svc

    try:
        svc.disconnect(db, account_id)
    except AppError as e:
        return redirect_with_flash("/accounts", e.message, "error")
    return redirect_with_flash("/accounts", "Account disconnected - its tokens were deleted.")


@router.post("/accounts/notify-test")
async def accounts_notify_test(db: Session = Depends(get_db)):
    from app.services.notifications import send_test

    if not get_settings().notify_configured:
        return redirect_with_flash("/accounts", "No push channel is configured (NOTIFY_PROVIDER).", "error")
    ok = send_test(db)
    return redirect_with_flash("/accounts", "Test notification sent." if ok else "The provider refused the test push.",
                               "ok" if ok else "error")


# =========================================================================
# Phase 2 B7: engagement (Instagram comments, human-sent replies)
# =========================================================================

_ENGAGEMENT_FILTERS = {"open": ["none", "drafted"], "sent": ["sent"], "ignored": ["ignored"], "all": None}


@router.get("/engagement", response_class=HTMLResponse)
async def engagement_page(request: Request, status: str = "open", post_id: Optional[int] = None,
                          db: Session = Depends(get_db)):
    from app.services import engagement as svc

    status = status if status in _ENGAGEMENT_FILTERS else "open"
    res = svc.list_comments(db, "instagram", post_id, _ENGAGEMENT_FILTERS[status], limit=50)
    return _template_response("engagement.html", {
        "request": request, "comments": res["items"], "total": res["total"], "status": status, "post_id": post_id,
        "totals": svc.totals_7d(db, utcnow()), "unanswered": svc.unanswered_count(db),
        "flash": get_flash(request),
    })


def _engagement_redirect(msg: str, kind: str = "ok", status: str = "open"):
    return redirect_with_flash(f"/engagement?status={status}", msg, kind)


@router.post("/engagement/sync")
def engagement_sync(db: Session = Depends(get_db), http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import engagement as svc

    if get_settings().PUBLISHING_MODE != "live":
        return _engagement_redirect("Comment sync runs in live mode only.", "error")
    try:
        out = svc.sync(db, utcnow(), http)
    except AppError as e:
        return _engagement_redirect(e.message, "error")
    return _engagement_redirect(f"Checked {out['posts']} post(s): {out['new_comments']} new comment(s)"
                                + (f", {out['errors']} problem(s)" if out["errors"] else "") + ".")


@router.post("/engagement/{comment_id}/draft")
def engagement_draft(request: Request, comment_id: int, instructions: str = Form(""), db: Session = Depends(get_db),
                     client=Depends(get_claude_client)):
    from app.services import AppError
    from app.services import engagement as svc
    from app.services.claude_client import GenerationError

    generation_rate_limit(request)
    try:
        _, suggest_ignore = svc.draft_reply(db, comment_id, client, instructions[:300])
    except GenerationError as e:
        return _engagement_redirect(e.message, "error")
    except AppError as e:
        return _engagement_redirect(e.message, "error")
    if suggest_ignore:
        return _engagement_redirect("No safe reply could be drafted for this comment - consider ignoring it.", "error")
    return _engagement_redirect("Draft ready - read it, edit it, then press Send reply.")


@router.post("/engagement/{comment_id}/send")
def engagement_send(request: Request, comment_id: int, text: str = Form(""), db: Session = Depends(get_db),
                    http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import engagement as svc

    generation_rate_limit(request)
    try:
        svc.send_reply(db, comment_id, text, http)
    except AppError as e:
        return _engagement_redirect(e.message, "error")
    return _engagement_redirect("Reply sent.")


@router.post("/engagement/{comment_id}/ignore")
async def engagement_ignore(comment_id: int, db: Session = Depends(get_db)):
    from app.services import AppError
    from app.services import engagement as svc

    try:
        svc.ignore_comment(db, comment_id)
    except AppError as e:
        return _engagement_redirect(e.message, "error")
    return _engagement_redirect("Comment ignored.")


# =========================================================================
# Phase 3 C4: Fanvue fan inbox (PLAN_PHASE3 §12)
# =========================================================================

_FAN_TABS = {"waiting": None, "replied": "replied", "ignored": "ignored"}


@router.get("/fans", response_class=HTMLResponse)
async def fans_page(request: Request, tab: str = "waiting", db: Session = Depends(get_db)):
    from app.services import fan_inbox as svc

    tab = tab if tab in _FAN_TABS else "waiting"
    if tab == "waiting":
        chats = [c for c in svc.list_chats(db) if c.status in ("escalated", "needs_reply", "sending")]
    else:
        chats = svc.list_chats(db, _FAN_TABS[tab])
    return _template_response("fans.html", {
        "request": request, "chats": chats, "tab": tab, "waiting": svc.waiting_count(db),
        "flash": get_flash(request),
    })


@router.get("/fans/{chat_id:int}", response_class=HTMLResponse)
async def fan_chat_page(request: Request, chat_id: int, db: Session = Depends(get_db)):
    from app.services import fan_inbox as svc

    chat = svc.get_chat(db, chat_id)
    ppv_assets = db.execute(select(MediaAsset).where(
        MediaAsset.is_active == True, MediaAsset.media_type.in_(("image", "video")),  # noqa: E712
        MediaAsset.storage_type.in_(("upload", "local_sync"))).order_by(desc(MediaAsset.created_at)).limit(200)).scalars().all()
    return _template_response("fan_chat.html", {
        "request": request, "chat": chat, "messages": chat.messages[-50:], "ppv_assets": ppv_assets,
        "flash": get_flash(request),
    })


def _fan_redirect(chat_id: Optional[int], msg: str, kind: str = "ok"):
    return redirect_with_flash(f"/fans/{chat_id}" if chat_id else "/fans", msg, kind)


@router.post("/fans/sync")
def fans_sync(db: Session = Depends(get_db), http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import fan_inbox as svc

    if get_settings().PUBLISHING_MODE != "live":
        return _fan_redirect(None, "The fan inbox syncs in live mode only.", "error")
    try:
        out = svc.sync(db, utcnow(), http)
    except AppError as e:
        return _fan_redirect(None, e.message, "error")
    return _fan_redirect(None, f"Checked {out['chats']} waiting chat(s): {out['needs_reply']} need a reply, "
                               f"{out['escalated']} need you." + (f" {out['errors']} problem(s)." if out["errors"] else ""))


@router.post("/fans/{chat_id:int}/draft")
def fan_draft(request: Request, chat_id: int, instructions: str = Form(""), db: Session = Depends(get_db),
              client=Depends(get_claude_client)):
    from app.services import AppError
    from app.services import fan_inbox as svc
    from app.services.claude_client import GenerationError

    generation_rate_limit(request)
    try:
        chat = svc.draft_reply(db, chat_id, client, instructions[:300])
    except (GenerationError, AppError) as e:
        return _fan_redirect(chat_id, e.message, "error")
    if chat.status == "escalated":
        return _fan_redirect(chat_id, "No safe draft: this one needs you. Reply yourself here or in the Fanvue app.", "error")
    return _fan_redirect(chat_id, "Draft ready - read it, edit it, then press Send.")


@router.post("/fans/{chat_id:int}/send")
def fan_send(request: Request, chat_id: int, text: str = Form(""), asset_id: Optional[int] = Form(None),
             price: str = Form(""), db: Session = Depends(get_db), http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import fan_inbox as svc

    generation_rate_limit(request)
    price_text = (price or "").strip().lstrip("$")
    try:
        cents = round(float(price_text) * 100) if price_text else None
    except (ValueError, OverflowError):
        return _fan_redirect(chat_id, "Price must be a number like 9.99.", "error")
    try:
        svc.send_reply(db, chat_id, text, http, asset_id, cents)
    except AppError as e:
        return _fan_redirect(chat_id, e.message, "error")
    return _fan_redirect(chat_id, "Sent." + (" Paid media attached." if asset_id else ""))


@router.post("/fans/{chat_id:int}/ignore")
def fan_ignore(chat_id: int, db: Session = Depends(get_db)):
    from app.services import AppError
    from app.services import fan_inbox as svc

    try:
        svc.ignore_chat(db, chat_id)
    except AppError as e:
        return _fan_redirect(chat_id, e.message, "error")
    return _fan_redirect(None, "Chat moved to Ignored.")


@router.post("/fans/{chat_id:int}/reopen")
def fan_reopen(chat_id: int, db: Session = Depends(get_db)):
    from app.services import AppError
    from app.services import fan_inbox as svc

    try:
        svc.reopen_chat(db, chat_id)
    except AppError as e:
        return _fan_redirect(chat_id, e.message, "error")
    return _fan_redirect(chat_id, "Chat reopened.")


# =========================================================================
# Phase 3 C5: Fanvue links + report (PLAN_PHASE3 §7, §10, §12)
# =========================================================================

@router.get("/fanvue", response_class=HTMLResponse)
def fanvue_page(request: Request, days: int = 7, db: Session = Depends(get_db), http=Depends(get_platform_http)):
    """Sync `def`: the report is fetched live from Fanvue (cached 10 minutes)."""
    from app.services import AppError
    from app.services import fanvue_report as rep

    days = days if days in (7, 30) else 7
    report, report_error = None, None
    try:
        report = rep.summary(db, http, days)
    except AppError as e:
        report_error = e.message
    posts = db.execute(select(ContentPost).where(ContentPost.platform == "fanvue", ContentPost.status == "published")
                       .order_by(desc(ContentPost.published_at)).limit(20)).scalars().all()
    links = rep.list_links(db)
    return _template_response("fanvue.html", {
        "request": request, "days": days, "report": report, "report_error": report_error, "money": rep.money,
        "links": links, "bio_names": dict(rep.BIO_LINKS), "link_platforms": ("instagram", "tiktok", "other"),
        "posts": posts, "flash": get_flash(request),
    })


@router.post("/fanvue/links/bio")
def fanvue_bio_links(db: Session = Depends(get_db), http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import fanvue_report as rep

    try:
        rep.ensure_bio_links(db, http)
    except AppError as e:
        return redirect_with_flash("/fanvue", e.message, "error")
    return redirect_with_flash("/fanvue", "Bio links ready - copy each one into Summer's Instagram / TikTok bio.")


@router.post("/fanvue/links")
def fanvue_create_link(name: str = Form(""), platform: str = Form("instagram"), db: Session = Depends(get_db),
                       http=Depends(get_platform_http)):
    from app.services import AppError
    from app.services import fanvue_report as rep

    try:
        link = rep.create_link(db, http, name, platform)
    except AppError as e:
        return redirect_with_flash("/fanvue", e.message, "error")
    return redirect_with_flash("/fanvue", f"Link '{link.name}' created.")

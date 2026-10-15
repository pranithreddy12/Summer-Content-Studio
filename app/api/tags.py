"""Tags API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Tag
from app.schemas import TagOut

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
async def list_tags(category_only: bool = False, db: Session = Depends(get_db)):
    """List all tags, optionally filtered to categories only."""
    query = select(Tag).order_by(Tag.name)
    if category_only:
        query = query.where(Tag.is_category == True)
    tags = db.execute(query).scalars().all()
    return tags
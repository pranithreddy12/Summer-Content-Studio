"""Profile API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ModelProfile
from app.schemas import ProfileOut, ProfileUpdate
from app.security import get_or_create_tags

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileOut])
async def list_profiles(active: bool | None = None, db: Session = Depends(get_db)):
    """List all profiles, optionally filtered by active status."""
    query = select(ModelProfile)
    if active is not None:
        query = query.where(ModelProfile.is_active == active)
    query = query.order_by(ModelProfile.name)
    profiles = db.execute(query).scalars().all()
    return profiles


@router.get("/{profile_id}", response_model=ProfileOut)
async def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get a profile by ID."""
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


@router.patch("/{profile_id}", response_model=ProfileOut)
async def update_profile(profile_id: int, data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update a profile."""
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    update_data = data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return profile
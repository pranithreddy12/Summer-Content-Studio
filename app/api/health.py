"""Health check API endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    """Health check (alias for root /health)."""
    from app.main import health_check
    return await health_check()
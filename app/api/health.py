"""Health check API endpoint.

ATTENTION: the app-wide /health is defined inline in app/main.py.
This module exists only as a drop-in replacement if the inline route is removed.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    """Health check (alias for root /health)."""
    raise AssertionError("Use the inline /health in app/main.py; this router is intentionally not mounted.")

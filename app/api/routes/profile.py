"""Public profile option catalogs."""

from fastapi import APIRouter

from app.schemas.auth import TagOptionsResponse
from app.services.profile import get_tag_options

router = APIRouter(prefix="/profile")


@router.get("/tag-options", response_model=TagOptionsResponse, summary="查询固定标签选项")
async def tag_options() -> TagOptionsResponse:
    """Return the fixed tag catalog used by profile forms."""
    return await get_tag_options()

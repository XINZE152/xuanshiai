"""Anonymous published legal and platform articles."""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.admin_config import PublicArticleDetail, PublicArticleItem
from app.services.public_articles import get_public_article, list_public_articles

router = APIRouter(prefix="/public/articles")


@router.get("", response_model=list[PublicArticleItem], summary="查询已发布的公开文章列表")
async def articles(db: AsyncSession = Depends(get_db)) -> list[PublicArticleItem]:
    return await list_public_articles(db)


@router.get("/{article_type}", response_model=PublicArticleDetail, summary="查询一篇已发布的公开文章")
async def article(
    article_type: str = Path(..., min_length=1, max_length=32, pattern=r"^[a-z_]+$"),
    db: AsyncSession = Depends(get_db),
) -> PublicArticleDetail:
    return await get_public_article(db, article_type)

"""Admin API for the live VIP package catalog."""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, declare_permission, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_config import AdminMembershipPackage, AdminMembershipPackageUpdate
from app.services.admin_membership_packages import list_admin_membership_packages, update_admin_membership_package

router = APIRouter(prefix="/admin/membership-packages")


@declare_permission("platform.config.read")
@router.get("", response_model=list[AdminMembershipPackage], summary="查询真实 VIP 套餐配置")
async def list_packages(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminMembershipPackage]:
    current.require("platform.config.read")
    return await list_admin_membership_packages(db)


@declare_permission("platform.config.write")
@router.patch("/{code}", response_model=AdminMembershipPackage, summary="更新现有 VIP 套餐")
async def update_package(
    body: AdminMembershipPackageUpdate,
    code: str = Path(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminMembershipPackage:
    current.require("platform.config.write")
    return await update_admin_membership_package(db, current.account.id, code, body)

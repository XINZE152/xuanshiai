"""Member creation, editing and certification details for the back office."""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.matchmaker_member_admin import (
    CertificationDetail,
    CertificationsAdminResponse,
    MatchmakerMemberAdminItem,
    MatchmakerMemberCreate,
    MatchmakerMemberUpdate,
    MemberAuditLogItem,
)
from app.services.matchmaker_member_admin import (
    certification_detail,
    create_member,
    member_audit_logs,
    update_member,
)

router = APIRouter(prefix="/admin/matchmaker/members")


def _member_scope(current: CurrentMatchmakerAdmin, params: dict[str, object]) -> str:
    """会员读写接口的统一数据范围谓词（D-4，实现见 dependencies.py）。"""
    return current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")


@router.post("", response_model=MatchmakerMemberAdminItem, status_code=201)
async def create(
    body: MatchmakerMemberCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerMemberAdminItem:
    return await create_member(
        db,
        body,
        current.account.id,
        organization_id=current.account.organization_id,
        matchmaker_user_id=current.account.matchmaker_user_id,
    )


@router.patch("/{member_id}", response_model=MatchmakerMemberAdminItem)
async def update(
    member_id: int = Path(..., ge=1),
    body: MatchmakerMemberUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerMemberAdminItem:
    return await update_member(
        db,
        member_id,
        body,
        current.account.id,
        scope=_member_scope(current, {}),
    )


@router.get("/{member_id}/certifications", response_model=CertificationsAdminResponse)
async def certifications(
    member_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CertificationsAdminResponse:
    scope = _member_scope(current, {})
    return CertificationsAdminResponse(
        education=await certification_detail(db, member_id, "education", scope=scope),
        house=await certification_detail(db, member_id, "house", scope=scope),
        marriage=await certification_detail(db, member_id, "marriage", scope=scope),
    )


@router.get("/{member_id}/certifications/{kind}", response_model=CertificationDetail)
async def certification(
    member_id: int = Path(..., ge=1),
    kind: str = Path(..., pattern="^(education|house|marriage)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CertificationDetail:
    return await certification_detail(db, member_id, kind, scope=_member_scope(current, {}))


@router.get("/{member_id}/audit-logs", response_model=list[MemberAuditLogItem])
async def audit_logs(
    member_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MemberAuditLogItem]:
    return await member_audit_logs(db, member_id, scope=_member_scope(current, {}))

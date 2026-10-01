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


def _member_guard(current: CurrentMatchmakerAdmin) -> tuple[str, dict[str, object]]:
    """会员读写接口的统一数据范围谓词及其绑定参数（D-4，判定实现见 dependencies.py）。

    谓词与参数**成对返回**：``scope`` 里的 ``:scope_*`` 占位符只能由生成它的那批参数
    绑定，分头传递时调用方很容易只取谓词、把参数留在一次性空字典里，三档普通账号
    会在参数缺失下整体不可用。
    """
    params: dict[str, object] = {}
    scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = u.id")
    return scope, params


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
    scope, scope_params = _member_guard(current)
    return await update_member(
        db,
        member_id,
        body,
        current.account.id,
        scope=scope,
        scope_params=scope_params,
    )


@router.get("/{member_id}/certifications", response_model=CertificationsAdminResponse)
async def certifications(
    member_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CertificationsAdminResponse:
    scope, scope_params = _member_guard(current)
    return CertificationsAdminResponse(
        education=await certification_detail(db, member_id, "education", scope=scope, scope_params=scope_params),
        house=await certification_detail(db, member_id, "house", scope=scope, scope_params=scope_params),
        marriage=await certification_detail(db, member_id, "marriage", scope=scope, scope_params=scope_params),
    )


@router.get("/{member_id}/certifications/{kind}", response_model=CertificationDetail)
async def certification(
    member_id: int = Path(..., ge=1),
    kind: str = Path(..., pattern="^(education|house|marriage)$"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CertificationDetail:
    scope, scope_params = _member_guard(current)
    return await certification_detail(db, member_id, kind, scope=scope, scope_params=scope_params)


@router.get("/{member_id}/audit-logs", response_model=list[MemberAuditLogItem])
async def audit_logs(
    member_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MemberAuditLogItem]:
    scope, scope_params = _member_guard(current)
    return await member_audit_logs(db, member_id, scope=scope, scope_params=scope_params)

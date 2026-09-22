"""Read-only dashboard endpoints for the independent matchmaker back office."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.matchmaker_dashboard_admin import MatchmakerDashboardStats

router = APIRouter(prefix="/admin/dashboard")


def _scoped_member_filter(
    current: CurrentMatchmakerAdmin, params: dict[str, object], member_sql: str
) -> str:
    if current.account.data_scope == "ALL" or "*" in current.permissions:
        return "1 = 1"
    scope = current.scope_condition(
        organization_column="scope_assignment.organization_id",
        params=params,
        user_column="scope_assignment.matchmaker_id",
    )
    return (
        "EXISTS (SELECT 1 FROM resource_assignment scope_assignment "
        f"WHERE scope_assignment.user_id = {member_sql} "
        "AND scope_assignment.status = 1 AND "
        + scope
        + ")"
    )


def _scoped_matchmaker_filter(
    current: CurrentMatchmakerAdmin, params: dict[str, object], subject_sql: str
) -> str:
    scope = current.scope_condition(
        organization_column="scope_org.id",
        params=params,
        user_column=subject_sql,
    )
    if current.account.data_scope in ("ALL",) or "*" in current.permissions:
        return scope
    if current.account.data_scope == "SELF":
        return scope
    return (
        "EXISTS (SELECT 1 FROM organization_member scope_member "
        "JOIN organization scope_org ON scope_org.id = scope_member.organization_id "
        "AND scope_org.org_type = 'store' "
        f"WHERE scope_member.user_id = {subject_sql} "
        "AND scope_member.role_code = 'store_matchmaker' "
        "AND scope_member.status = 1 AND "
        + scope
        + ")"
    )


@router.get("/stats", response_model=MatchmakerDashboardStats, summary="查询红娘后台首页统计")
async def dashboard_stats(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> MatchmakerDashboardStats:
    current.require("matchmaker.read")
    params: dict[str, object] = {}
    member_scope = _scoped_member_filter(current, params, "u.id")
    apply_scope = _scoped_matchmaker_filter(current, params, "a.user_id")
    service_scope = _scoped_matchmaker_filter(current, params, "s.matchmaker_id")
    row = (await db.execute(text("""SELECT
        (SELECT COUNT(*) FROM users u WHERE """ + member_scope + """ ) AS member_count,
        (SELECT COUNT(DISTINCT m.user_id) FROM user_membership m JOIN users u ON u.id = m.user_id WHERE m.status = 1 AND (m.end_at IS NULL OR m.end_at > UTC_TIMESTAMP()) AND """ + member_scope + """ ) AS vip_count,
        (SELECT COUNT(*) FROM user_matchmaker_apply a JOIN user_role r ON r.user_id = a.user_id AND r.role_code = 'service_matchmaker' AND r.status = 1 WHERE a.application_type = 'service_matchmaker' AND a.status = 1 AND """ + apply_scope + """ ) AS matchmaker_count,
        (SELECT COUNT(*) FROM matchmaker_service s WHERE s.status = 0 AND """ + service_scope + """ ) AS pending_service_count,
        (SELECT COUNT(*) FROM matchmaker_service s WHERE s.status = 1 AND """ + service_scope + """ ) AS active_service_count,
        (SELECT COUNT(*) FROM user_matchmaker_apply a WHERE a.application_type = 'service_matchmaker' AND a.status = 0 AND """ + apply_scope + """ ) AS pending_certification_count,
        (SELECT COUNT(*) FROM users u WHERE u.created_at >= CURDATE() AND """ + member_scope + """ ) AS today_new_member_count"""), params)).mappings().one()
    return MatchmakerDashboardStats(**{key: int(row[key] or 0) for key in MatchmakerDashboardStats.model_fields})


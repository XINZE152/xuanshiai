"""Customer lead management routes."""

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, declare_permission, get_current_matchmaker_admin, require_sensitive_operation
from app.core.sensitive_fields import redaction_level_from_permissions
from app.db.session import get_db
from app.schemas.customer_lead_admin import CustomerLead, CustomerLeadAbandonment, CustomerLeadAbandonRequest, CustomerLeadBatchDeleteFailure, CustomerLeadBatchDeleteRequest, CustomerLeadBatchDeleteResult, CustomerLeadBatchImportRequest, CustomerLeadAssignment, CustomerLeadConvertRequest, CustomerLeadConvertResult, CustomerLeadCreate, CustomerLeadBatchImportResult, CustomerLeadFollowUp, CustomerLeadFollowUpCreate, CustomerLeadFollowUpRecordPage, CustomerLeadFollowBucket, MatchmakerFollowStat, CustomerLeadPromoterStat, CustomerLeadImportSummary, CustomerLeadOptions, CustomerLeadPage, CustomerLeadRestoreRequest, CustomerLeadStatistics, CustomerLeadUpdate, LeadPreferenceResponse
from app.schemas.auth import PreferenceUpdateRequest
from app.schemas.customer_lead_dict_admin import LeadDictCreate, LeadDictItem, LeadDictOrder, LeadDictUpdate
from app.services.customer_lead_admin import abandon_lead, add_follow_up, assign_lead, batch_delete_leads, batch_import_leads, build_import_template, convert_lead, create_lead, delete_lead, get_lead, get_lead_preferences, import_leads_file, lead_options, lead_statistics, lead_follow_statistics, list_abandonments, list_follow_ups, list_follow_up_records, list_leads, matchmaker_follow_stats, promoter_acquisition_stats, restore_lead, update_lead, update_lead_preferences
from app.services import customer_lead_dict_admin as dict_service
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)

router = APIRouter(prefix="/admin/customer-leads")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _lead_scope(current: CurrentMatchmakerAdmin, *, alias: str = "") -> tuple[str, dict[str, object]]:
    """生成客源线索的四档作用域谓词（D-16）。

    唯一实现在 `CurrentMatchmakerAdmin.scope_clause`，此处仅按资源列名调用：
    ``matchmaker_id`` 为个人归属（SELF），``organization_id`` 为组织归属
    （STORE / ORGANIZATION）。``alias`` 供 JOIN 查询（弃海列表）限定列前缀，
    避免同名列歧义。
    """
    params: dict[str, object] = {}
    scope = current.scope_clause(
        params,
        user_column=f"{alias}matchmaker_id",
        organization_column=f"{alias}organization_id",
    )
    return scope, params


@declare_permission("customer_lead.manage")
@router.get("", response_model=CustomerLeadPage, summary="查询客源线索")
async def lead_list(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, pattern="^(NEW|CONTACTED|INTENDED|CONVERTED|LOST|CLOSED)$"),
    source: str | None = Query(None, max_length=64),
    matchmaker_id: int | None = Query(None, ge=1),
    search: str | None = Query(None, max_length=64),
    intention_level: int | None = Query(None, ge=1, le=7, description="客户意向 A-G 七档：1 A未接 2 B初步沟通 3 C深入沟通未缔结 4 D待确定到店 5 E已确定到店 6 F预约需二邀 7 G已到店未签约"),
    gender: int | None = Query(None, ge=0, le=2, description="性别 0未知 1男 2女（A13）"),
    start_date: str | None = Query(None, max_length=10, description="录入时间起（含，兼容前端空字符串）"),
    end_date: str | None = Query(None, max_length=10, description="录入时间止（含，兼容前端空字符串）"),
    assign_start: str | None = Query(None, max_length=10, description="分派时间起（含，兼容前端空字符串）"),
    assign_end: str | None = Query(None, max_length=10, description="分派时间止（含，兼容前端空字符串）"),
    promoter_id: int | None = Query(None, ge=1, description="推广红娘 users.id"),
    xiangqin_status: int | None = Query(None, ge=1, le=5, description="相亲状态 1公开相亲 2委托红娘 3完全私密 4关闭相亲 5已经脱单"),
    bind_mode: str | None = Query(None, pattern="^(bind|auto|custom)$", description="落户方式 bind绑定已有账号/auto自动生成账号/custom自定义创建"),
    wechat_added: int | None = Query(None, ge=0, le=2, description="加微信结果 0未添加 1已加上 2没加上"),
    age_min: int | None = Query(None, ge=16, le=100),
    age_max: int | None = Query(None, ge=16, le=100),
    height_min: int | None = Query(None, ge=80, le=250),
    height_max: int | None = Query(None, ge=80, le=250),
    marriage: str | None = Query(None, max_length=255, description="婚况多选，英文逗号分隔"),
    education: str | None = Query(None, max_length=255, description="学历多选，英文逗号分隔"),
    occupation: str | None = Query(None, max_length=255, description="职业多选，英文逗号分隔"),
    residence: str | None = Query(None, max_length=64, description="现居地模糊匹配"),
    hometown: str | None = Query(None, max_length=64, description="家乡模糊匹配"),
    hide_followed_today: bool | None = Query(None, description="隐藏今日已跟进的线索"),
    hide_called_today: bool | None = Query(None, description="隐藏今日已通话的线索"),
    has_phone: bool | None = Query(None, description="是否登记手机号"),
    has_wechat: bool | None = Query(None, description="是否登记微信号"),
    assigned: bool | None = Query(None, description="分派状态筛选：true 已分派 / false 待分派（B11）"),
    audit_status: str | None = Query(None, pattern="^(active|pending|rejected)$", description="审核状态：active 有效 / pending 待核 / rejected 无效（B11）"),
    stored: bool | None = Query(None, description="入库情况：true 已入库(已转会员) / false 未入库（B11）"),
    order: str | None = Query(None, pattern="^(latest|earliest)$", description="记录排序，默认 latest"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadPage:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await list_leads(
        db, page, page_size, status, source, matchmaker_id, search,
        redaction_level_from_permissions(current.permissions),
        scope=scope,
        scope_params=scope_params,
        intention_level=intention_level,
        gender=gender,
        start_date=start_date,
        end_date=end_date,
        assign_start=assign_start,
        assign_end=assign_end,
        promoter_id=promoter_id,
        xiangqin_status=xiangqin_status,
        bind_mode=bind_mode,
        wechat_added=wechat_added,
        age_min=age_min,
        age_max=age_max,
        height_min=height_min,
        height_max=height_max,
        marriage=marriage,
        education=education,
        occupation=occupation,
        residence=residence,
        hometown=hometown,
        hide_followed_today=hide_followed_today,
        hide_called_today=hide_called_today,
        has_phone=has_phone,
        has_wechat=has_wechat,
        assigned=assigned,
        audit_status=audit_status,
        stored=stored,
        order=order,
    )


@declare_permission("customer_lead.manage")
@router.post("", response_model=CustomerLead, status_code=201, summary="录入客源线索")
async def lead_create(body: CustomerLeadCreate, current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    params: dict[str, object] = {}
    assignment_scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    return await create_lead(db, current.account.id, body, redaction_level_from_permissions(current.permissions), assignment_scope=assignment_scope, assignment_scope_params=params)


@declare_permission("customer_lead.manage")
@router.get("/statistics", response_model=CustomerLeadStatistics, summary="查询客源线索统计")
async def lead_stats(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadStatistics:
    current.require("customer_lead.manage")
    # 统计只聚合 customer_lead 自身行，归属列在表上，用 _lead_scope 即可；
    # 不需要 resource_assignment 的 EXISTS 包装。
    scope, scope_params = _lead_scope(current)
    return await lead_statistics(db, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.get("/options", response_model=CustomerLeadOptions, summary="客源线索下拉字典")
async def lead_dict(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadOptions:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await lead_options(db, scope=scope, scope_params=scope_params)


# ─────────────────── A13 跟进全览 / 数据报表 ───────────────────
# 静态路径 /follow-records 、/follow-statistics 、/matchmaker-follow-stats 、
# /promoter-stats 均先于 /{lead_id} 注册。


@declare_permission("customer_lead.manage")
@router.get("/follow-records", response_model=CustomerLeadFollowUpRecordPage, summary="全局客源跟进记录列表")
async def follow_records(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    matchmaker_id: int | None = Query(None, ge=1, description="按服务红娘过滤"),
    promoter_id: int | None = Query(None, ge=1, description="按推广红娘过滤"),
    intention_level: int | None = Query(None, ge=1, le=7),
    start_date: str | None = Query(None, max_length=10, description="跟进时间起（含）"),
    end_date: str | None = Query(None, max_length=10, description="跟进时间止（含）"),
    search: str | None = Query(None, max_length=64, description="跟进内容/客源称呼模糊匹配"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadFollowUpRecordPage:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await list_follow_up_records(
        db, page, page_size,
        matchmaker_id=matchmaker_id, promoter_id=promoter_id, intention_level=intention_level,
        start_date=start_date, end_date=end_date, search=search,
        scope=scope, scope_params=scope_params,
    )


@declare_permission("customer_lead.manage")
@router.get("/follow-statistics", response_model=CustomerLeadFollowBucket, summary="客源跟进时间卡片计数")
async def follow_statistics(
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    matchmaker_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadFollowBucket:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await lead_follow_statistics(
        db, start_date=start_date, end_date=end_date, matchmaker_id=matchmaker_id,
        scope=scope, scope_params=scope_params,
    )


@declare_permission("customer_lead.manage")
@router.get("/matchmaker-follow-stats", response_model=list[MatchmakerFollowStat], summary="按红娘客源跟进统计")
async def matchmaker_follow_statistics(
    matchmaker_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MatchmakerFollowStat]:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await matchmaker_follow_stats(db, matchmaker_id=matchmaker_id, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.get("/promoter-stats", response_model=list[CustomerLeadPromoterStat], summary="推广红娘获客统计")
async def promoter_statistics(
    promoter_id: int | None = Query(None, ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CustomerLeadPromoterStat]:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await promoter_acquisition_stats(db, promoter_id=promoter_id, scope=scope, scope_params=scope_params)


# ─────────────────── A14 来源字典 CRUD ───────────────────
# 前端契约路径：/admin/customer-leads/sources（复数）。
# 与同域 /{lead_id} 动态路由共用 router，故必须排在前面（静态段先于 {lead_id} 注册）。
# 字典为全局配置，不需数据范围收敛；写入需 customer_lead.manage。


@declare_permission("customer_lead.manage")
@router.get("/sources", response_model=list[LeadDictItem], summary="来源字典列表")
async def source_dict_list(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[LeadDictItem]:
    current.require("customer_lead.manage")
    return await dict_service.list_dict(db, "sources")


@declare_permission("customer_lead.manage")
@router.post("/sources/reorder", status_code=204, summary="来源重排")
async def source_dict_reorder(body: LeadDictOrder, current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> None:
    current.require("customer_lead.manage")
    await dict_service.reorder_dict(db, "sources", body.ids)


@declare_permission("customer_lead.manage")
@router.post("/sources", response_model=LeadDictItem, status_code=201, summary="新增来源")
async def source_dict_create(body: LeadDictCreate, current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> LeadDictItem:
    current.require("customer_lead.manage")
    return await dict_service.create_dict(db, "sources", body)


@declare_permission("customer_lead.manage")
@router.patch("/sources/{item_id}", response_model=LeadDictItem, summary="修改来源")
async def source_dict_update(item_id: int = Path(..., ge=1), body: LeadDictUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> LeadDictItem:
    current.require("customer_lead.manage")
    return await dict_service.update_dict(db, "sources", item_id, body)


@declare_permission("customer_lead.manage")
@router.delete("/sources/{item_id}", status_code=204, summary="删除来源")
async def source_dict_delete(item_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> Response:
    current.require("customer_lead.manage")
    await dict_service.delete_dict(db, "sources", item_id)
    return Response(status_code=204)


@declare_permission("customer_lead.manage")
@router.get("/import-template", summary="下载客源批量导入模板")
async def lead_import_template(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin)) -> Response:
    current.require("customer_lead.manage")
    return Response(
        content=build_import_template(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="customer-lead-import-template.xlsx"'},
    )


@declare_permission("customer_lead.manage")
@router.get("/abandoned", response_model=list[CustomerLeadAbandonment], summary="查询弃海客源")
async def abandoned_list(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadAbandonment]:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current, alias="cl.")
    return await list_abandonments(db, True, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.get("/abandonments", response_model=list[CustomerLeadAbandonment], summary="查询弃海记录")
async def abandonment_list(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadAbandonment]:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current, alias="cl.")
    return await list_abandonments(db, False, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.post("/import", response_model=CustomerLeadImportSummary, summary="上传 Excel 批量导入客源线索")
async def lead_import(
    file: UploadFile = File(..., description="按模板填写的 .xlsx 文件"),
    audit_status: str = Form("active", pattern="^(active|pending|rejected)$", description="本次导入的审核状态：active 有效 / pending 待核 / rejected 无效"),
    default_source: str = Form("批量导入", max_length=64, description="留空或不匹配时的默认来源"),
    default_matchmaker_id: int | None = Form(None, description="默认分派红娘用户ID"),
    default_promoter_id: int | None = Form(None, description="默认推广红娘用户ID"),
    dup_mode: str = Form("skip", pattern="^(skip|append)$", description="昵称/联系方式重复处理：skip 不导入 / append 仍然导入"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadImportSummary:
    current.require("customer_lead.manage")
    # 导入可指定默认红娘/推广红娘，属写操作，必须先按四档数据范围校验目标用户
    # （与 lead_create 同口径，D-16）。
    params: dict[str, object] = {}
    assignment_scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    return await import_leads_file(
        db,
        current.account.id,
        await file.read(),
        file.filename or "",
        audit_status=audit_status,
        default_source=default_source or "批量导入",
        default_matchmaker_id=default_matchmaker_id,
        default_promoter_id=default_promoter_id,
        dup_mode=dup_mode,
        assignment_scope=assignment_scope,
        assignment_scope_params=params,
    )


@declare_permission("customer_lead.manage")
@router.post("/{lead_id}/convert", response_model=CustomerLeadConvertResult, summary="客源线索一键入库")
async def lead_convert(
    lead_id: int = Path(..., ge=1),
    body: CustomerLeadConvertRequest = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadConvertResult:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    lead, user_id, replayed = await convert_lead(
        db,
        current.account.id,
        current.account.organization_id,
        current.account.matchmaker_user_id,
        lead_id,
        body,
        scope=scope,
        scope_params=scope_params,
    )
    return CustomerLeadConvertResult(lead=lead, user_id=user_id, replayed=replayed)


@declare_permission("customer_lead.manage")
@router.get("/{lead_id}", response_model=CustomerLead, summary="查询客源线索详情")
async def lead_detail(lead_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await get_lead(db, lead_id, redaction_level_from_permissions(current.permissions), scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.patch("/{lead_id}", response_model=CustomerLead, summary="修改客源线索")
async def lead_update(lead_id: int = Path(..., ge=1), body: CustomerLeadUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await update_lead(db, current.account.id, lead_id, body, redaction_level_from_permissions(current.permissions), scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.get("/{lead_id}/follow-ups", response_model=list[CustomerLeadFollowUp], summary="查询线索跟进记录")
async def follow_up_list(lead_id: int = Path(..., ge=1), page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadFollowUp]:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await list_follow_ups(db, lead_id, page, page_size, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.get("/{lead_id}/preferences", response_model=LeadPreferenceResponse, summary="查询客源线索择偶要求")
async def lead_preference_get(lead_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> LeadPreferenceResponse:
    """读取线索的择偶要求；已入库读账号那份，未入库读 customer_lead_preference 暂存表（均不报错）。"""
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await get_lead_preferences(db, lead_id, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.put("/{lead_id}/preferences", response_model=LeadPreferenceResponse, summary="修改客源线索择偶要求")
async def lead_preference_put(lead_id: int = Path(..., ge=1), body: PreferenceUpdateRequest = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> LeadPreferenceResponse:
    """写入线索的择偶要求（已入库复用 C 端 user_partner_preference，未入库写 customer_lead_preference 暂存表）。"""
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await update_lead_preferences(db, current.account.id, lead_id, body, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.post("/batch-import", response_model=CustomerLeadBatchImportResult, summary="批量导入客源线索")
async def batch_import(
    request: CustomerLeadBatchImportRequest,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadBatchImportResult:
    current.require("customer_lead.manage")
    params: dict[str, object] = {}
    assignment_scope = current.scope_exists_clause(params, correlation="scope_assignment.user_id = users.id")
    return await batch_import_leads(db, current.account.id, request.rows, request.dup_mode, assignment_scope=assignment_scope, assignment_scope_params=params)


@declare_permission("customer_lead.manage")
@router.post("/{lead_id}/follow-ups", response_model=CustomerLeadFollowUp, status_code=201, summary="新增线索跟进记录")
async def follow_up_create(lead_id: int = Path(..., ge=1), body: CustomerLeadFollowUpCreate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadFollowUp:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await add_follow_up(db, current.account.id, lead_id, body, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.patch("/{lead_id}/assignment", response_model=CustomerLead, summary="分配客源线索")
async def lead_assignment(lead_id: int = Path(..., ge=1), body: CustomerLeadAssignment = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await assign_lead(db, current.account.id, lead_id, body, redaction_level_from_permissions(current.permissions), scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.post("/{lead_id}/abandon", response_model=CustomerLeadAbandonment, status_code=201, summary="放入弃海池")
async def lead_abandon(lead_id: int = Path(..., ge=1), body: CustomerLeadAbandonRequest = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadAbandonment:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await abandon_lead(db, current.account.id, lead_id, body.reason, scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.post("/{lead_id}/restore", response_model=CustomerLead, summary="从弃海池恢复")
async def lead_restore(lead_id: int = Path(..., ge=1), body: CustomerLeadRestoreRequest = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    return await restore_lead(db, current.account.id, lead_id, body.reason, redaction_level_from_permissions(current.permissions), scope=scope, scope_params=scope_params)


@declare_permission("customer_lead.manage")
@router.post("/batch-delete", response_model=CustomerLeadBatchDeleteResult, summary="批量删除客源（幂等 + 二次确认）")
async def lead_batch_delete(
    body: CustomerLeadBatchDeleteRequest,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadBatchDeleteResult:
    """A16：软删除多条客源，需 confirm=true + Idempotency-Key；单事务 + 一份汇总审计。

    越权/不存在/已删除的线索归入 failed，不整体报错；同键重放不重复删除。
    静态路径 /batch-delete 必须先于 /{lead_id} 系列注册。
    """
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="customer_lead.batch_delete",
        idempotency_key=idempotency_key,
        payload={"body": body.model_dump(mode="json")},
    )
    if replayed is not None:
        return CustomerLeadBatchDeleteResult(**replayed)
    try:
        deleted, failed = await batch_delete_leads(db, current.account.id, body.lead_ids, scope=scope, scope_params=scope_params)
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    result = CustomerLeadBatchDeleteResult(deleted=deleted, failed=[CustomerLeadBatchDeleteFailure(**item) for item in failed])
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@declare_permission("customer_lead.manage")
@router.delete("/{lead_id}", status_code=204, summary="删除客源线索（软删除）")
async def lead_delete(
    lead_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """A16：软删除单条客源，需 confirm=true + Idempotency-Key。越权/不存在/已删除返回 404。"""
    current.require("customer_lead.manage")
    scope, scope_params = _lead_scope(current)
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="customer_lead.delete",
        idempotency_key=idempotency_key,
        payload={"lead_id": lead_id},
    )
    if replayed is not None:
        return Response(status_code=204)
    try:
        await delete_lead(db, current.account.id, lead_id, scope=scope, scope_params=scope_params)
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, {"deleted": True, "id": lead_id})
    return Response(status_code=204)

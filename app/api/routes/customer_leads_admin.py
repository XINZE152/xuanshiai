"""Customer lead management routes."""

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.customer_lead_admin import CustomerLead, CustomerLeadAbandonment, CustomerLeadAbandonRequest, CustomerLeadBatchImportRequest, CustomerLeadAssignment, CustomerLeadCreate, CustomerLeadBatchImportResult, CustomerLeadFollowUp, CustomerLeadFollowUpCreate, CustomerLeadImportSummary, CustomerLeadOptions, CustomerLeadPage, CustomerLeadRestoreRequest, CustomerLeadStatistics, CustomerLeadUpdate
from app.services.customer_lead_admin import abandon_lead, add_follow_up, assign_lead, batch_import_leads, build_import_template, create_lead, get_lead, import_leads_file, lead_options, lead_statistics, list_abandonments, list_follow_ups, list_leads, restore_lead, update_lead

router = APIRouter(prefix="/admin/customer-leads")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("", response_model=CustomerLeadPage, summary="查询客源线索")
async def lead_list(page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100), status: str | None = Query(None, pattern="^(NEW|CONTACTED|INTENDED|CONVERTED|LOST|CLOSED)$"), source: str | None = Query(None, max_length=64), matchmaker_id: int | None = Query(None, ge=1), search: str | None = Query(None, max_length=64), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadPage:
    current.require("customer_lead.manage")
    return await list_leads(db, page, page_size, status, source, matchmaker_id, search)


@router.post("", response_model=CustomerLead, status_code=201, summary="录入客源线索")
async def lead_create(body: CustomerLeadCreate, current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    return await create_lead(db, current.account.id, body)


@router.get("/statistics", response_model=CustomerLeadStatistics, summary="查询客源线索统计")
async def lead_stats(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadStatistics:
    current.require("customer_lead.manage")
    return await lead_statistics(db)


@router.get("/options", response_model=CustomerLeadOptions, summary="客源线索下拉字典")
async def lead_dict(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadOptions:
    current.require("customer_lead.manage")
    return await lead_options(db)


@router.get("/import-template", summary="下载客源批量导入模板")
async def lead_import_template(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin)) -> Response:
    current.require("customer_lead.manage")
    return Response(
        content=build_import_template(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="customer-lead-import-template.xlsx"'},
    )


@router.get("/abandoned", response_model=list[CustomerLeadAbandonment], summary="查询弃海客源")
async def abandoned_list(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadAbandonment]:
    current.require("customer_lead.manage")
    return await list_abandonments(db, True)


@router.get("/abandonments", response_model=list[CustomerLeadAbandonment], summary="查询弃海记录")
async def abandonment_list(current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadAbandonment]:
    current.require("customer_lead.manage")
    return await list_abandonments(db, False)


@router.post("/import", response_model=CustomerLeadImportSummary, summary="上传 Excel 批量导入客源线索")
async def lead_import(
    file: UploadFile = File(..., description="按模板填写的 .xlsx 文件"),
    audit_status: str = Form("active", pattern="^(active|pending)$", description="本次导入的审核状态：active 有效 / pending 待核"),
    default_source: str = Form("批量导入", max_length=64, description="留空或不匹配时的默认来源"),
    default_matchmaker_id: int | None = Form(None, description="默认分派红娘用户ID"),
    default_promoter_id: int | None = Form(None, description="默认推广红娘用户ID"),
    default_tags: str = Form("", max_length=255, description="默认标签，英文逗号分隔"),
    dup_mode: str = Form("skip", pattern="^(skip|append)$", description="昵称/联系方式重复处理：skip 不导入 / append 仍然导入"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadImportSummary:
    current.require("customer_lead.manage")
    tags = [item.strip() for item in default_tags.split(",") if item.strip()]
    return await import_leads_file(
        db,
        current.account.id,
        await file.read(),
        file.filename or "",
        audit_status=audit_status,
        default_source=default_source or "批量导入",
        default_matchmaker_id=default_matchmaker_id,
        default_promoter_id=default_promoter_id,
        default_tags=tags,
        dup_mode=dup_mode,
    )


@router.get("/{lead_id}", response_model=CustomerLead, summary="查询客源线索详情")
async def lead_detail(lead_id: int = Path(..., ge=1), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    return await get_lead(db, lead_id)


@router.patch("/{lead_id}", response_model=CustomerLead, summary="修改客源线索")
async def lead_update(lead_id: int = Path(..., ge=1), body: CustomerLeadUpdate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    return await update_lead(db, current.account.id, lead_id, body)


@router.get("/{lead_id}/follow-ups", response_model=list[CustomerLeadFollowUp], summary="查询线索跟进记录")
async def follow_up_list(lead_id: int = Path(..., ge=1), page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100), current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> list[CustomerLeadFollowUp]:
    current.require("customer_lead.manage")
    return await list_follow_ups(db, lead_id, page, page_size)


@router.post("/batch-import", response_model=CustomerLeadBatchImportResult, summary="批量导入客源线索")
async def batch_import(
    request: CustomerLeadBatchImportRequest,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerLeadBatchImportResult:
    current.require("customer_lead.manage")
    return await batch_import_leads(db, current.account.id, request.rows, request.dup_mode)


@router.post("/{lead_id}/follow-ups", response_model=CustomerLeadFollowUp, status_code=201, summary="新增线索跟进记录")
async def follow_up_create(lead_id: int = Path(..., ge=1), body: CustomerLeadFollowUpCreate = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadFollowUp:
    current.require("customer_lead.manage")
    return await add_follow_up(db, current.account.id, lead_id, body)


@router.patch("/{lead_id}/assignment", response_model=CustomerLead, summary="分配客源线索")
async def lead_assignment(lead_id: int = Path(..., ge=1), body: CustomerLeadAssignment = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    return await assign_lead(db, current.account.id, lead_id, body)


@router.post("/{lead_id}/abandon", response_model=CustomerLeadAbandonment, status_code=201, summary="放入弃海池")
async def lead_abandon(lead_id: int = Path(..., ge=1), body: CustomerLeadAbandonRequest = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLeadAbandonment:
    current.require("customer_lead.manage")
    return await abandon_lead(db, current.account.id, lead_id, body.reason)


@router.post("/{lead_id}/restore", response_model=CustomerLead, summary="从弃海池恢复")
async def lead_restore(lead_id: int = Path(..., ge=1), body: CustomerLeadRestoreRequest = ..., current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin), db: AsyncSession = Depends(get_db)) -> CustomerLead:
    current.require("customer_lead.manage")
    return await restore_lead(db, current.account.id, lead_id, body.reason)

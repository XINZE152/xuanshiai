"""短信管理（A1）路由：签名 / 模板 / 群发 / 记录 / 余量。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path as PathParam, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    declare_permission,
    get_current_matchmaker_admin,
    require_sensitive_operation,
)
from app.core.config import settings
from app.db.session import get_db
from app.schemas.admin_sms_admin import (
    SmsQuota,
    SmsRecordPage,
    SmsSendRequest,
    SmsSendResult,
    SmsVendorConfigUpdate,
    SmsSignatureCreate,
    SmsSignatureItem,
    SmsSignaturePage,
    SmsSignatureUpdate,
    SmsTemplateCreate,
    SmsTemplateItem,
    SmsTemplatePage,
    SmsTemplateUpdate,
)
from app.services import admin_sms_admin as service
from app.services import sms_vendor_config as vendor_service
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)

router = APIRouter(prefix="/admin/sms")


@declare_permission("platform.sms.read")
@router.get("/vendor-config", summary="短信厂商配置（密钥掩码回显）")
async def sms_vendor_config_read(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("platform.sms.read")
    return await vendor_service.get_vendor_config(db)


@declare_permission("platform.sms.write")
@router.patch("/vendor-config", summary="更新短信厂商配置")
async def sms_vendor_config_update(
    body: SmsVendorConfigUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("platform.sms.write")
    return await vendor_service.update_vendor_config(db, current.account.id, body.model_dump(exclude_unset=True))


@declare_permission("platform.sms.read")
@router.get("/signatures", response_model=SmsSignaturePage, summary="短信签名列表")
async def signatures_list(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    status: str | None = Query(None, description="pending / approved / rejected"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsSignaturePage:
    current.require("platform.sms.read")
    return await service.list_signatures(db, page, page_size, keyword, status)


@declare_permission("platform.sms.write")
@router.post("/signatures", response_model=SmsSignatureItem, status_code=201, summary="新增短信签名报备")
async def signatures_create(
    body: SmsSignatureCreate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsSignatureItem:
    current.require("platform.sms.write")
    return await service.create_signature(db, current.account.id, body)


@declare_permission("platform.sms.write")
@router.patch("/signatures/{signature_id}", response_model=SmsSignatureItem, summary="更新短信签名")
async def signatures_update(
    body: SmsSignatureUpdate = ...,
    signature_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsSignatureItem:
    current.require("platform.sms.write")
    return await service.update_signature(db, current.account.id, signature_id, body)


@declare_permission("platform.sms.write")
@router.delete("/signatures/{signature_id}", status_code=204, summary="删除短信签名")
async def signatures_delete(
    signature_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.sms.write")
    await service.delete_signature(db, current.account.id, signature_id)


@declare_permission("platform.sms.read")
@router.get("/templates", response_model=SmsTemplatePage, summary="短信模板列表")
async def templates_list(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    scene: str | None = Query(None, max_length=32),
    status: int | None = Query(None, ge=0, le=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsTemplatePage:
    current.require("platform.sms.read")
    return await service.list_templates(db, page, page_size, keyword, scene, status)


@declare_permission("platform.sms.write")
@router.post("/templates", response_model=SmsTemplateItem, status_code=201, summary="新增短信模板")
async def templates_create(
    body: SmsTemplateCreate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsTemplateItem:
    current.require("platform.sms.write")
    return await service.create_template(db, current.account.id, body)


@declare_permission("platform.sms.write")
@router.patch("/templates/{template_id}", response_model=SmsTemplateItem, summary="更新短信模板")
async def templates_update(
    body: SmsTemplateUpdate = ...,
    template_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsTemplateItem:
    current.require("platform.sms.write")
    return await service.update_template(db, current.account.id, template_id, body)


@declare_permission("platform.sms.write")
@router.delete("/templates/{template_id}", status_code=204, summary="删除短信模板")
async def templates_delete(
    template_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.sms.write")
    await service.delete_template(db, current.account.id, template_id)


@declare_permission("platform.sms.write")
@router.post("/send", response_model=SmsSendResult, summary="短信群发（幂等 + 二次确认）")
async def sms_send(
    body: SmsSendRequest = ...,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsSendResult:
    current.require("platform.sms.write")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="admin.sms.send",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replayed is not None:
        return SmsSendResult(**replayed)
    try:
        result = await service.send_sms(db, current.account.id, body)
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@declare_permission("platform.sms.read")
@router.get("/records", response_model=SmsRecordPage, summary="短信发送记录（手机号按脱敏回显）")
async def sms_records(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=32, description="手机号片段匹配"),
    template_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, description="queued / sent / failed"),
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsRecordPage:
    current.require("platform.sms.read")
    return await service.list_records(
        db, page, page_size, keyword, template_id, status, start_date, end_date
    )


@declare_permission("platform.sms.read")
@router.get("/quota", response_model=SmsQuota, summary="短信余量与发送汇总")
async def sms_quota(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> SmsQuota:
    current.require("platform.sms.read")
    return await service.get_quota(db, settings.sms_template_provider)

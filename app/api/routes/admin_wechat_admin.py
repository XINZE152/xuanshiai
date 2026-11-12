"""微信公众号运营（A2）路由：配置 / 粉丝 / 分组 / 菜单 / 自动回复 / 模板 / 发送。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path as PathParam, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    declare_permission,
    get_current_matchmaker_admin,
    require_sensitive_operation,
)
from app.db.session import get_db
from app.schemas.admin_wechat_admin import (
    WechatConfig,
    WechatConfigUpdate,
    WechatFanGroupCreate,
    WechatFanGroupItem,
    WechatFanGroupUpdate,
    WechatFanPage,
    WechatFanSyncResult,
    WechatMenu,
    WechatMenuPublish,
    WechatMessagePage,
    WechatQrcodeResult,
    WechatReplyRuleCreate,
    WechatReplyRuleItem,
    WechatReplyRuleUpdate,
    WechatSendRequest,
    WechatSendResult,
    WechatTemplateItem,
    WechatTemplateSyncResult,
)
from app.services import admin_wechat_admin as service
from app.services.sensitive_operations import (
    abort_sensitive_operation,
    begin_sensitive_operation,
    finish_sensitive_operation,
)

router = APIRouter(prefix="/admin/wechat")


@declare_permission("platform.wechat.read")
@router.get("/config", response_model=WechatConfig, summary="公众号配置（密钥只回显是否已配置）")
async def wechat_config(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatConfig:
    current.require("platform.wechat.read")
    return await service.get_config(db)


@declare_permission("platform.wechat.write")
@router.put("/config", response_model=WechatConfig, summary="更新公众号配置（非敏感项）")
async def wechat_config_update(
    body: WechatConfigUpdate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatConfig:
    current.require("platform.wechat.write")
    return await service.update_config(db, current.account.id, body)


@declare_permission("platform.wechat.read")
@router.get("/fans", response_model=WechatFanPage, summary="粉丝列表（分页 + 分组 / 关注状态）")
async def wechat_fans(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    group_id: int | None = Query(None, ge=1),
    subscribe_status: int | None = Query(None, ge=0, le=1),
    subscribe_date: str | None = Query(None, max_length=10, description="按关注日期筛选（YYYY-MM-DD）"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatFanPage:
    current.require("platform.wechat.read")
    return await service.list_fans(db, page, page_size, keyword, group_id, subscribe_status, subscribe_date)


@declare_permission("platform.wechat.write")
@router.post("/fans/sync", response_model=WechatFanSyncResult, summary="同步微信粉丝")
async def wechat_fans_sync(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatFanSyncResult:
    current.require("platform.wechat.write")
    return await service.sync_fans(db, current.account.id)


@declare_permission("platform.wechat.read")
@router.get("/fans/groups", response_model=list[WechatFanGroupItem], summary="粉丝分组列表")
async def wechat_groups(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[WechatFanGroupItem]:
    current.require("platform.wechat.read")
    return await service.list_groups(db)


@declare_permission("platform.wechat.write")
@router.post(
    "/fans/groups", response_model=WechatFanGroupItem, status_code=201, summary="新增粉丝分组"
)
async def wechat_group_create(
    body: WechatFanGroupCreate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatFanGroupItem:
    current.require("platform.wechat.write")
    return await service.create_group(db, current.account.id, body)


@declare_permission("platform.wechat.write")
@router.patch("/fans/groups/{group_id}", response_model=WechatFanGroupItem, summary="更新粉丝分组")
async def wechat_group_update(
    body: WechatFanGroupUpdate = ...,
    group_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatFanGroupItem:
    current.require("platform.wechat.write")
    return await service.update_group(db, current.account.id, group_id, body)


@declare_permission("platform.wechat.write")
@router.delete("/fans/groups/{group_id}", status_code=204, summary="删除粉丝分组")
async def wechat_group_delete(
    group_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.wechat.write")
    await service.delete_group(db, current.account.id, group_id)


@declare_permission("platform.wechat.write")
@router.post("/qrcode", response_model=WechatQrcodeResult, summary="生成公众号带参二维码")
async def wechat_qrcode_create(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatQrcodeResult:
    current.require("platform.wechat.write")
    return await service.create_qrcode(db, current.account.id)


@declare_permission("platform.wechat.read")
@router.get("/menu", response_model=WechatMenu, summary="读取公众号菜单配置")
async def wechat_menu(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatMenu:
    current.require("platform.wechat.read")
    return await service.get_menu(db)


@declare_permission("platform.wechat.write")
@router.put("/menu", response_model=WechatMenu, summary="发布公众号菜单（先发微信再落本地）")
async def wechat_menu_publish(
    body: WechatMenuPublish = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatMenu:
    current.require("platform.wechat.write")
    return await service.publish_menu(db, current.account.id, body)


@declare_permission("platform.wechat.read")
@router.get("/autoreply/rules", response_model=list[WechatReplyRuleItem], summary="自动回复规则")
async def wechat_reply_rules(
    match_type: str | None = Query(None, description="keyword / subscribe / default"),
    status: int | None = Query(None, ge=0, le=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[WechatReplyRuleItem]:
    current.require("platform.wechat.read")
    return await service.list_rules(db, match_type, status)


@declare_permission("platform.wechat.write")
@router.post(
    "/autoreply/rules",
    response_model=WechatReplyRuleItem,
    status_code=201,
    summary="新增自动回复规则",
)
async def wechat_reply_rule_create(
    body: WechatReplyRuleCreate = ...,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatReplyRuleItem:
    current.require("platform.wechat.write")
    return await service.create_rule(db, current.account.id, body)


@declare_permission("platform.wechat.write")
@router.patch(
    "/autoreply/rules/{rule_id}", response_model=WechatReplyRuleItem, summary="更新自动回复规则"
)
async def wechat_reply_rule_update(
    body: WechatReplyRuleUpdate = ...,
    rule_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatReplyRuleItem:
    current.require("platform.wechat.write")
    return await service.update_rule(db, current.account.id, rule_id, body)


@declare_permission("platform.wechat.write")
@router.delete("/autoreply/rules/{rule_id}", status_code=204, summary="删除自动回复规则")
async def wechat_reply_rule_delete(
    rule_id: int = PathParam(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.wechat.write")
    await service.delete_rule(db, current.account.id, rule_id)


@declare_permission("platform.wechat.read")
@router.get("/templates", response_model=list[WechatTemplateItem], summary="模板消息列表")
async def wechat_templates(
    keyword: str | None = Query(None, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[WechatTemplateItem]:
    current.require("platform.wechat.read")
    return await service.list_templates(db, keyword)


@declare_permission("platform.wechat.write")
@router.post(
    "/templates/sync", response_model=WechatTemplateSyncResult, summary="从微信拉取模板消息"
)
async def wechat_templates_sync(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatTemplateSyncResult:
    current.require("platform.wechat.write")
    return await service.sync_templates(db, current.account.id)


@declare_permission("platform.wechat.write")
@router.post(
    "/messages/send", response_model=WechatSendResult, summary="群发 / 模板消息（幂等 + 二次确认）"
)
async def wechat_messages_send(
    body: WechatSendRequest = ...,
    idempotency_key: str = Depends(require_sensitive_operation),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatSendResult:
    current.require("platform.wechat.write")
    reservation, replayed = await begin_sensitive_operation(
        db,
        actor_id=current.account.id,
        operation="admin.wechat.send",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replayed is not None:
        return WechatSendResult(**replayed)
    try:
        result = await service.send_message(db, current.account.id, body)
    except Exception:
        await abort_sensitive_operation(db, reservation)
        raise
    await finish_sensitive_operation(db, reservation, result.model_dump(mode="json"))
    return result


@declare_permission("platform.wechat.read")
@router.get("/messages/records", response_model=WechatMessagePage, summary="消息发送记录")
async def wechat_message_records(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    send_type: str | None = Query(None, description="broadcast / template"),
    status: str | None = Query(None, description="queued / sent / failed"),
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> WechatMessagePage:
    current.require("platform.wechat.read")
    return await service.list_messages(db, page, page_size, send_type, status, start_date, end_date)

"""Administration APIs for the generic operational content items."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, declare_permission, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.admin_content import ContentItem, ContentItemCreate, ContentItemPage, ContentItemUpdate
from app.services.admin_content import create_item, delete_item, get_item, list_items, update_item

router = APIRouter(prefix="/admin/content")


@declare_permission("platform.config.read")
@router.get("/{domain}", response_model=ContentItemPage, summary="查询内容项列表")
async def content_list(
    domain: str = Path(..., min_length=2, max_length=64),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64),
    status: int | None = Query(None, ge=1, le=2),
    type: str | None = Query(None, max_length=32, description="内容类型，取自 extra.type：page 单页 / landing 引导页 / form 自由表单"),
    qrcode_type: str | None = Query(None, max_length=32, description="二维码类型，取自 extra.qrcode_type：h5 普通H5二维码 / follow 引导关注公众号"),
    xiangqin_status: str | None = Query(None, max_length=16, description="相亲状态，取自 extra.xiangqin_status：1公开相亲 2委托红娘 3完全私密 4停止相亲 5已经脱单"),
    good_news_status: str | None = Query(None, max_length=32, description="喜讯状态，取自 extra.status"),
    matchmaker_id: str | None = Query(None, max_length=20, description="服务红娘 ID，取自 extra.matchmaker_id"),
    group_id: str | None = Query(None, max_length=20, description="社群报名所属社群 ID，取自 extra.group_id"),
    pay: str | None = Query(None, max_length=16, description="社群报名支付状态，取自 extra.pay"),
    nick: str | None = Query(None, max_length=64, description="社群报名昵称，按 extra.nick 精确/模糊筛选"),
    search_type: str | None = Query(None, max_length=32, description="keyword 的匹配目标（按域生效）：free_pay_order/gift_exchange 支持 nickname 会员昵称 / phone 手机号 / order_no 订单号；未定义时按标题等全字段模糊匹配"),
    gender: str | None = Query(None, max_length=16, description="【仅 free_form】性别，取自 extra.gender：1 男 / 2 女"),
    landing_id: str | None = Query(None, max_length=32, description="【仅 free_form】落地页 ID，取自 extra.landing_id"),
    source: str | None = Query(None, max_length=64, description="【仅 free_form】客户来源，取自 extra.source，精确匹配"),
    broadcast_id: str | None = Query(None, max_length=20, description="【仅 sms_send_record】发送明细所属群发任务 ID，取自 extra.broadcast_id"),
    qrcode_id: str | None = Query(None, max_length=20, description="【仅 fan_qrcode_stat】统计所属二维码 ID，取自 extra.qrcode_id"),
    arrival: str | None = Query(None, max_length=16, description="【仅 sales_library】到店状态，取自 extra.arrival：已到店 / 未到店"),
    matchmaker: str | None = Query(None, max_length=64, description="【仅 sales_library】销售红娘姓名，按 extra.matchmaker 模糊筛选"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItemPage:
    current.require("platform.config.read")
    extra_filters = {
        "type": type,
        "qrcode_type": qrcode_type,
        "xiangqin_status": xiangqin_status,
        "good_news_status": good_news_status,
        "matchmaker_id": matchmaker_id,
        "group_id": group_id,
        "pay": pay,
        "nick": nick,
        "gender": gender,
        "landing_id": landing_id,
        "source": source,
        "broadcast_id": broadcast_id,
        "qrcode_id": qrcode_id,
        "arrival": arrival,
        "matchmaker": matchmaker,
    }
    return await list_items(db, domain, page, page_size, keyword, status, extra_filters, search_type)


@declare_permission("platform.config.read")
@router.get("/{domain}/{item_id}", response_model=ContentItem, summary="查询内容项详情")
async def content_detail(
    domain: str = Path(..., min_length=2, max_length=64),
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.read")
    return await get_item(db, domain, item_id)


@declare_permission("platform.config.write")
@router.post("/{domain}", response_model=ContentItem, status_code=201, summary="新增内容项")
async def content_create(
    body: ContentItemCreate = ...,
    domain: str = Path(..., min_length=2, max_length=64),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.write")
    return await create_item(db, current.account.id, domain, body)


@declare_permission("platform.config.write")
@router.patch("/{domain}/{item_id}", response_model=ContentItem, summary="更新内容项")
async def content_update(
    body: ContentItemUpdate = ...,
    domain: str = Path(..., min_length=2, max_length=64),
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> ContentItem:
    current.require("platform.config.write")
    return await update_item(db, domain, item_id, body, current.account.id)


@declare_permission("platform.config.write")
@router.delete("/{domain}/{item_id}", status_code=204, summary="删除内容项")
async def content_delete(
    domain: str = Path(..., min_length=2, max_length=64),
    item_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    current.require("platform.config.write")
    await delete_item(db, domain, item_id)

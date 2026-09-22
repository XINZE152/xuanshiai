"""线上行为（M3-3）管理后台路由。

前缀 ``/admin/members``；读接口需 ``matchmaker.member.read``，写接口需
``matchmaker.member.manage``（依赖注入已按路径自动映射，这里再显式声明）。

> 说明：``member_follow_up_admin.py`` 里已有一个早期版本的 ``GET /behavior/all``
> （仅覆盖浏览/收藏/爆灯/举报且字段不全，无前端调用）。本模块用
> ``/behavior-events`` 提供完整的五类流水（含赠送礼物、爆灯支付字段、举报IP与证据图），
> 保持旧接口不动以避免破坏既有调用方。
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.member_behavior_admin import MemberBehaviorPage
from app.services import member_behavior_admin as service

router = APIRouter(prefix="/admin/members")


# 方向化接口按类别**精确暴露**参数，只声明本类别真正生效的：
#   browse             → search / min_times
#   favorite           → search
#   superlike / gift   → search / pay_status
# 不在签名里的参数由 FastAPI 直接丢弃，不会造成静默失效的假筛选。
# 聚合接口 ``/behavior-events`` 的 category 在运行时才确定，因此仍暴露全部可选参数。


async def _list_kind(
    kind: str,
    member_id: int | None,
    page: int,
    page_size: int,
    current: CurrentMatchmakerAdmin,
    db: AsyncSession,
    direction: str = "sent",
    search: str | None = None,
    min_times: int | None = None,
    pay_status: int | None = None,
) -> MemberBehaviorPage:
    current.require("matchmaker.member.read")
    if member_id is not None and not await db.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": member_id}):
        raise HTTPException(404, detail="会员不存在")
    return await service.list_behavior(
        db,
        page,
        page_size,
        kind,
        search=search,
        min_times=min_times,
        pay_status=pay_status,
        member_id=member_id,
        direction=direction,
    )


@router.get("/browse-history", response_model=MemberBehaviorPage, summary="查询浏览过谁")
async def browse_history(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按浏览发起人过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    min_times: int | None = Query(None, ge=1, le=1000, description="浏览次数下限"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("browse", member_id, page, page_size, current, db, search=search, min_times=min_times)


@router.get("/visitors", response_model=MemberBehaviorPage, summary="查询被谁浏览")
async def visitors(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按被浏览会员过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    min_times: int | None = Query(None, ge=1, le=1000, description="浏览次数下限"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("browse", member_id, page, page_size, current, db, "received", search=search, min_times=min_times)


@router.get("/favorites", response_model=MemberBehaviorPage, summary="查询收藏了谁")
async def favorites(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按收藏发起人过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("favorite", member_id, page, page_size, current, db, search=search)


@router.get("/favorites/received", response_model=MemberBehaviorPage, summary="查询谁收藏了用户")
async def received_favorites(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按被收藏会员过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("favorite", member_id, page, page_size, current, db, "received", search=search)


@router.get("/superlikes", response_model=MemberBehaviorPage, summary="查询给谁爆灯")
async def superlikes(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按爆灯发起人过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("superlike", member_id, page, page_size, current, db, search=search, pay_status=pay_status)


@router.get("/superlikes/received", response_model=MemberBehaviorPage, summary="查询谁给用户爆灯")
async def received_superlikes(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按被爆灯会员过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("superlike", member_id, page, page_size, current, db, "received", search=search, pay_status=pay_status)


@router.get("/gifts", response_model=MemberBehaviorPage, summary="查询赠送礼物")
async def gifts(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按赠送人过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("gift", member_id, page, page_size, current, db, search=search, pay_status=pay_status)


@router.get("/gifts/received", response_model=MemberBehaviorPage, summary="查询收到礼物")
async def received_gifts(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    member_id: int | None = Query(None, ge=1, description="按收礼人过滤；不传则查询全部会员"),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号/编号关键字"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    return await _list_kind("gift", member_id, page, page_size, current, db, "received", search=search, pay_status=pay_status)


@router.get("/behavior-events", response_model=MemberBehaviorPage, summary="线上行为流水（五类）")
async def list_behavior_events(
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category: str = Query(
        "browse",
        pattern="^(browse|favorite|superlike|gift|report)$",
        description="browse浏览/favorite收藏/superlike爆灯/gift礼物/report举报",
    ),
    search: str | None = Query(None, max_length=64, description="会员昵称/手机号关键字"),
    min_times: int | None = Query(None, ge=1, le=1000, description="浏览次数下限（仅 browse）"),
    status: int | None = Query(None, ge=0, le=2, description="举报状态 0待处理 1已处理 2驳回（仅 report）"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付（爆灯/礼物）"),
    member_id: int | None = Query(None, ge=1, description="按发起行为的会员 ID 过滤；不传则查询全部会员"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    current.require("matchmaker.member.read")
    return await service.list_behavior(
        db,
        page,
        page_size,
        category,
        search,
        min_times,
        report_status=status,
        pay_status=pay_status,
        member_id=member_id,
    )


@router.get("/{member_id}/behavior-events", response_model=MemberBehaviorPage, summary="查询单个会员线上行为流水（五类）")
async def list_member_behavior_events(
    member_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category: str = Query(
        "browse",
        pattern="^(browse|favorite|superlike|gift|report)$",
        description="browse浏览/favorite收藏/superlike爆灯/gift礼物/report举报",
    ),
    min_times: int | None = Query(None, ge=1, le=1000, description="浏览次数下限（仅 browse）"),
    status: int | None = Query(None, ge=0, le=2, description="举报状态 0待处理 1已处理 2驳回（仅 report）"),
    pay_status: int | None = Query(None, ge=0, le=1, description="支付状态 0未支付 1已支付（爆灯/礼物）"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberBehaviorPage:
    current.require("matchmaker.member.read")
    if not await db.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": member_id}):
        raise HTTPException(404, detail="会员不存在")
    return await service.list_behavior(
        db, page, page_size, category, min_times=min_times,
        report_status=status, pay_status=pay_status, member_id=member_id,
    )


@router.delete("/behavior-events/{category}/{event_id}", summary="删除线上行为记录（爆灯/礼物/举报）")
async def delete_behavior_event(
    category: str = Path(..., pattern="^(superlike|gift|report)$"),
    event_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current.require("matchmaker.member.manage")
    return await service.delete_behavior_event(db, category, event_id, current.account.id)

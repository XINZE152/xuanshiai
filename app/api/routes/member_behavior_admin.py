"""线上行为（M3-3）管理后台路由。

前缀 ``/admin/members``；读接口需 ``matchmaker.member.read``，写接口需
``matchmaker.member.manage``（依赖注入已按路径自动映射，这里再显式声明）。

> 说明：``member_follow_up_admin.py`` 里已有一个早期版本的 ``GET /behavior/all``
> （仅覆盖浏览/收藏/爆灯/举报且字段不全，无前端调用）。本模块用
> ``/behavior-events`` 提供完整的五类流水（含赠送礼物、爆灯支付字段、举报IP与证据图），
> 保持旧接口不动以避免破坏既有调用方。
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.member_behavior_admin import MemberBehaviorPage
from app.services import member_behavior_admin as service

router = APIRouter(prefix="/admin/members")


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

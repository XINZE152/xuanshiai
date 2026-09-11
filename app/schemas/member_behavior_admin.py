"""线上行为（M3-3）管理后台契约：浏览 / 收藏 / 爆灯 / 赠送礼物 / 网友举报五类流水。

对应前端 ``/love-user-behavior`` 页面。五类共用同一行结构，未使用字段为 ``null``。
会员编号统一为 ``G`` + 6 位左补零；金额类字段一律序列化为字符串。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class MemberBehaviorItem(BaseModel):
    """线上行为流水行（五类通用）。"""

    event_id: int  # 事件主键（各来源表的 id）
    user_id: int  # 动作发起会员 user_id
    member_code: str  # 发起方会员编号 G+6 位
    nickname: str | None = None  # 发起方昵称
    user_avatar: str | None = None  # 发起方头像
    target_user_id: int | None = None  # 对象会员 user_id
    target_member_code: str | None = None  # 对象会员编号 G+6 位
    target_nickname: str | None = None  # 对象昵称
    target_avatar: str | None = None  # 对象头像
    occurred_at: datetime | None = None  # 发生时间

    # ── 浏览 ──
    browse_times: int | None = Field(default=None, description="同一对会员的第几次浏览（聚合计数）")

    # ── 爆灯 ──
    amount: str | None = Field(default=None, description="爆灯支付金额")
    order_no: str | None = None
    pay_status: int | None = Field(default=None, description="0未支付 1已支付")
    pay_status_label: str | None = Field(default=None, description="未支付/已支付")
    pay_method: str | None = None
    event_status: int | None = Field(default=None, description="爆灯状态 1生效中 2已过期 3已撤销")
    event_status_label: str | None = Field(default=None, description="正常/已过期/已取消")

    # ── 赠送礼物 ──
    gift_name: str | None = None
    gift_qty: int | None = None
    qty_unit: str | None = Field(default=None, description="数量单位 颗/发/个/架")
    point_cost: int | None = Field(default=None, description="消耗积分")
    paid_amount: str | None = Field(default=None, description="实付金额")
    reward_points: int | None = Field(default=None, description="奖励积分")

    # ── 网友举报 ──
    submit_ip: str | None = Field(default=None, description="提交人IP")
    report_type: str | None = Field(default=None, description="举报原因/类型")
    detail: str | None = Field(default=None, description="举报详细内容")
    images: list[str] | None = Field(default=None, description="证据图片URL列表")
    report_status: int | None = Field(default=None, description="0待处理 1已处理 2驳回")
    report_status_label: str | None = Field(default=None, description="待处理/已处理/已驳回")


class MemberBehaviorPage(BaseModel):
    items: list[MemberBehaviorItem]
    page: int
    page_size: int
    total: int
    has_more: bool

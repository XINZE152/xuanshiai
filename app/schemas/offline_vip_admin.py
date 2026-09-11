"""线下VIP会员服务契约（M3-6）。

对应前端页面：会员CRM → 线下VIP（/love-user-vip-underline）。
数据源：offline_vip（服务记录）、offline_vip_meet_log（成功约见人工修改记录）、
       users、user_auth、matchmaker_profile、organization。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# 服务进度枚举（与前端 9 个子 Tab 一一对应，all 不落库）
OfflineVipProgress = Literal[
    "matching",
    "dating",
    "deep",
    "in_love",
    "met_parents",
    "paused",
    "breakup",
    "married",
]

# 电子合同状态
OfflineVipContractStatus = Literal["none", "pending", "signed", "void"]

PROGRESS_LABELS: dict[str, str] = {
    "matching": "匹配推荐中",
    "dating": "约会进行中",
    "deep": "深度接触",
    "in_love": "已经恋爱",
    "met_parents": "已见父母",
    "paused": "暂停服务",
    "breakup": "恋爱分手",
    "married": "已经领证",
}

CONTRACT_STATUS_LABELS: dict[str, str] = {
    "none": "未发起",
    "pending": "签署中",
    "signed": "已签署",
    "void": "已作废",
}


class OfflineVipItem(BaseModel):
    """列表行：与前端表格 10 列（ID/会员/签约日期/服务套餐/服务进度/最近跟进/合同金额/红娘/电子合同/备注信息）对齐。"""

    id: int
    user_id: int
    member_code: str = Field(description="会员编号：G + user_id 左补零到 6 位")
    nickname: str | None = None
    avatar: str | None = None
    phone: str | None = None
    sign_date: date | None = Field(default=None, description="签约日期")
    package_name: str | None = Field(default=None, description="服务套餐")
    progress: str = Field(description="服务进度枚举")
    progress_label: str = Field(description="服务进度中文")
    service_start: date | None = None
    service_end: date | None = None
    last_follow_at: datetime | None = Field(default=None, description="最近跟进时间")
    contract_amount: Decimal = Field(default=Decimal("0.00"), description="合同金额，序列化为字符串")
    sales_matchmaker_id: int | None = None
    sales_matchmaker_name: str | None = None
    service_matchmaker_id: int | None = None
    service_matchmaker_name: str | None = None
    promoter_id: int | None = None
    promoter_name: str | None = None
    contract_status: str = Field(default="none", description="电子合同状态")
    contract_status_label: str = Field(default="未发起", description="电子合同状态中文")
    contract_no: str | None = None
    promise_meet_count: int = Field(default=0, description="承诺约见人数")
    success_meet_count: int = Field(default=0, description="已成功约见人数")
    remark: str | None = None
    attach_urls: list[str] = Field(default_factory=list, description="图片附件")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OfflineVipPage(BaseModel):
    items: list[OfflineVipItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class OfflineVipStatistics(BaseModel):
    """线下VIP 顶部 10 张统计卡，与前端卡片顺序一一对应。"""

    store_count: int = Field(default=0, description="门店数")
    vip_count: int = Field(default=0, description="线下VIP 总数")
    serving_count: int = Field(default=0, description="服务中")
    expiring_count: int = Field(default=0, description="即将到期（30 天内）")
    expired_count: int = Field(default=0, description="服务到期")
    paid_count: int = Field(default=0, description="有偿费（合同金额>0）")
    promise_meet_total: int = Field(default=0, description="总计安排见面（承诺约见人数合计）")
    promise_meet_month: int = Field(default=0, description="本月已安排（本月发起记录的承诺约见人数合计）")
    refund_risk_count: int = Field(default=0, description="有退费风险（暂停服务/恋爱分手）")
    refunded_count: int = Field(default=0, description="已退费（当前无数据源，恒为 0）")


class OfflineVipCreate(BaseModel):
    """新增线下VIP：优先按 user_id 绑定，其次按 lookup+lookup_by 搜索；两者都不传时按手机号精确查。"""

    user_id: int | None = Field(default=None, ge=1)
    lookup: str | None = Field(default=None, min_length=1, max_length=100)
    lookup_by: Literal["nickname", "phone"] = "nickname"
    sales_matchmaker_id: int | None = Field(default=None, ge=1)
    service_matchmaker_id: int | None = Field(default=None, ge=1)
    promoter_id: int | None = Field(default=None, ge=1)
    sign_date: date | None = None
    service_start: date | None = None
    service_end: date | None = None
    package_name: str | None = Field(default=None, max_length=128)
    contract_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), le=Decimal("100000000"))
    promise_meet_count: int = Field(default=0, ge=0, le=100000)
    success_meet_count: int = Field(default=0, ge=0, le=100000)
    remark: str | None = Field(default=None, max_length=500)
    attach_urls: list[str] = Field(default_factory=list)


class OfflineVipUpdate(BaseModel):
    """编辑线下VIP：全部可选；success_meet_count 变化时写入人工修改记录。"""

    sales_matchmaker_id: int | None = Field(default=None, ge=1)
    service_matchmaker_id: int | None = Field(default=None, ge=1)
    promoter_id: int | None = Field(default=None, ge=1)
    sign_date: date | None = None
    service_start: date | None = None
    service_end: date | None = None
    package_name: str | None = Field(default=None, max_length=128)
    contract_amount: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100000000"))
    promise_meet_count: int | None = Field(default=None, ge=0, le=100000)
    success_meet_count: int | None = Field(default=None, ge=0, le=100000)
    progress: OfflineVipProgress | None = None
    remark: str | None = Field(default=None, max_length=500)
    attach_urls: list[str] | None = None
    meet_change_remark: str | None = Field(
        default=None, max_length=255, description="人工修改成功约见次数时的说明，写入修改记录"
    )


class OfflineVipMeetLogItem(BaseModel):
    """成功约见次数人工修改记录。"""

    id: int
    vip_id: int
    before_count: int
    after_count: int
    remark: str | None = None
    changed_by: int | None = None
    changed_by_name: str | None = None
    created_at: datetime | None = None


class OfflineVipMeetLogPage(BaseModel):
    items: list[OfflineVipMeetLogItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class OfflineVipOption(BaseModel):
    id: int
    name: str
    extra: str | None = Field(default=None, description="附加说明，如红娘角色标签")


class OfflineVipOptions(BaseModel):
    """下拉选项集合：筛选区 3 个下拉 + 抽屉内 3 个下拉 + 套餐类型。"""

    sales_matchmakers: list[OfflineVipOption] = Field(default_factory=list)
    service_matchmakers: list[OfflineVipOption] = Field(default_factory=list)
    promoters: list[OfflineVipOption] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)

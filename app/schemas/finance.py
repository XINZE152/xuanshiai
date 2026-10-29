"""订单、分成、账本和提现接口契约。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CommissionRuleCreate(BaseModel):
    beneficiary_type: Literal["service_matchmaker", "store", "promoter", "partner"]
    name: str = Field(min_length=1, max_length=128)
    mode: Literal["fixed", "rate"]
    fixed_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    priority: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_mode(self) -> "CommissionRuleCreate":
        if self.mode == "fixed" and self.fixed_amount is None:
            raise ValueError("固定金额规则必须填写 fixed_amount")
        if self.mode == "rate" and self.rate_percent is None:
            raise ValueError("比例规则必须填写 rate_percent")
        return self


class CommissionRuleResponse(BaseModel):
    id: int
    beneficiary_type: str
    name: str
    mode: str
    fixed_amount: Decimal | None
    rate_percent: Decimal | None
    priority: int
    version: int
    status: Literal[1, 2]
    created_at: datetime


class ProductCommissionConfigCreate(BaseModel):
    beneficiary_type: Literal["service_matchmaker", "store", "promoter", "partner"]
    mode: Literal["fixed", "rate"]
    fixed_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)

    @model_validator(mode="after")
    def validate_mode(self) -> "ProductCommissionConfigCreate":
        if self.mode == "fixed" and self.fixed_amount is None:
            raise ValueError("固定金额配置必须填写 fixed_amount")
        if self.mode == "rate" and self.rate_percent is None:
            raise ValueError("比例配置必须填写 rate_percent")
        return self


class ProductCommissionConfigResponse(BaseModel):
    id: int
    product_id: int
    beneficiary_type: str
    mode: str
    fixed_amount: Decimal | None
    rate_percent: Decimal | None
    version: int
    status: Literal[1, 2]
    created_at: datetime


class FinanceOrderCreate(BaseModel):
    """后台代用户创建会员订单。

    价格/商品名不再接受客户端输入，只接受套餐编码，由服务端从
    ``config_membership_package`` 读取。
    """

    user_id: int = Field(ge=1, description="下单用户 ID")
    package_code: str = Field(
        min_length=1,
        max_length=64,
        description="config_membership_package.code，服务端据此取价",
    )


class PaymentOrderResponse(BaseModel):
    id: int
    order_no: str
    user_id: int
    product_type: int
    product_name: str
    amount: Decimal
    status: Literal[0, 1, 2, 3]
    pay_time: datetime | None
    created_at: datetime


class CommissionEntryResponse(BaseModel):
    id: int
    order_id: int
    beneficiary_type: str
    beneficiary_id: int
    base_amount: Decimal
    amount: Decimal
    status: str
    created_at: datetime


class MatchmakerCommissionEntryCreate(BaseModel):
    """后台手工录入一笔服务红娘分成。

    口径（与合伙人人手工录入同源，2026-10-28 决策）：只写 commission_entry + 审计日志，
    **不动余额账本**（不写 account_ledger，不凭空增加可提现余额）；默认 PENDING，
    供红娘线上分成明细页展示与事后核对。
    """

    matchmaker_user_id: int = Field(ge=1, description="服务红娘 user id")
    consumer_user_id: int | None = Field(default=None, ge=1, description="关联消费会员 user id（可选，仅留档）")
    rule_id: int | None = Field(default=None, ge=1, description="分成事件（commission_rule.id，beneficiary_type=service_matchmaker）")
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2, description="分成金额(元)")
    base_amount: Decimal | None = Field(default=None, ge=0, le=100000000, decimal_places=2, description="计费基数，缺省同 amount")
    remark: str | None = Field(default=None, max_length=255)


class MatchmakerCommissionEntryItem(BaseModel):
    id: int
    created_at: datetime | None = None
    matchmaker_id: int
    matchmaker_name: str | None = None
    rule_id: int | None = None
    rule_name: str | None = None
    base_amount: str = "0.00"
    amount: str = "0.00"
    status: str = "PENDING"
    source: str = "manual"
    remark: str | None = None


class MatchmakerCommissionEntryCreateResult(BaseModel):
    entry: MatchmakerCommissionEntryItem


class FinanceReportRow(BaseModel):
    beneficiary_type: str
    beneficiary_id: int
    order_count: int
    total_amount: Decimal
    pending_amount: Decimal
    available_amount: Decimal


class PaymentOrderAdminPage(BaseModel):
    items: list["PaymentOrderResponse"]
    page: int
    page_size: int
    total: int
    has_more: bool


class WithdrawalAdminPage(BaseModel):
    items: list["WithdrawalResponse"]
    page: int
    page_size: int
    total: int
    has_more: bool


class LedgerEntryResponse(BaseModel):
    id: int
    account_type: str
    account_id: int
    direction: str
    amount: Decimal
    state: str
    source_type: str
    source_id: int
    idempotency_key: str
    created_at: datetime


class LedgerEntryPage(BaseModel):
    items: list[LedgerEntryResponse]
    page: int
    page_size: int
    total: int
    has_more: bool


class AccountBalanceResponse(BaseModel):
    account_type: str
    account_id: int
    pending_amount: Decimal
    available_amount: Decimal


class WithdrawalCreate(BaseModel):
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2)
    payee_masked: str = Field(min_length=1, max_length=128)


class WithdrawalResponse(BaseModel):
    id: int
    account_type: str
    account_id: int
    amount: Decimal
    status: str
    payee_masked: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class WithdrawalReview(BaseModel):
    status: Literal["APPROVED", "REJECTED", "PROCESSING", "SUCCEEDED", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=255)


class FinanceRefundRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


# ============================================
# 后台「红娘线上分成明细」页契约
# ============================================


class FinanceDailyRow(BaseModel):
    """按日聚合的订单收入/退款报表行（依据 payment_order 的支付时间）。"""

    date: str
    pay_count: int
    income_amount: Decimal
    refund_count: int
    refund_amount: Decimal


class CommissionEntryDetailItem(BaseModel):
    """详情页单条明细：含关联订单/会员/红娘/事件，金额字段以 str 序列化避免精度丢失。"""

    id: int
    created_at: datetime
    store_name: str = Field(default="总店", description="总店红娘后台统一显示『总店』")
    matchmaker_id: int
    matchmaker_name: str
    matchmaker_avatar: str | None = None
    consumer_id: int
    consumer_name: str
    consumer_phone: str | None = None
    consumer_avatar: str | None = None
    event_name: str
    beneficiary_type: str
    order_id: int
    order_no: str | None = None
    consumer_amount: Decimal
    commission_amount: Decimal
    status: str


class CommissionEntryDetailPage(BaseModel):
    items: list[CommissionEntryDetailItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchmakerOption(BaseModel):
    """详情页筛选用的红娘下拉项。"""

    id: int
    name: str
    avatar: str | None = None


class EventOption(BaseModel):
    """详情页筛选用的事件下拉项。"""

    id: int = Field(description="commission_rule.id")
    name: str
    beneficiary_type: str


class CommissionEntryDetailOptions(BaseModel):
    """详情页一次性返回筛选下拉选项。"""

    matchmakers: list[MatchmakerOption] = Field(default_factory=list)
    events: list[EventOption] = Field(default_factory=list)


# ------------------------- M5 分店分成明细 -------------------------
class StoreCommissionEntryItem(BaseModel):
    """分店线上分成明细单条：beneficiary_type='store'，金额以 str 序列化。"""

    id: int
    created_at: datetime
    store_id: int
    store_name: str
    matchmaker_id: int | None = None
    matchmaker_name: str | None = None
    consumer_id: int | None = None
    consumer_name: str | None = None
    event_name: str
    order_id: int
    order_no: str | None = None
    consumer_amount: Decimal
    commission_amount: Decimal
    status: str


class StoreCommissionEntryPage(BaseModel):
    items: list[StoreCommissionEntryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class StoreOption(BaseModel):
    """分店切换下拉项。"""

    id: int
    name: str
    status: int


class StoreCommissionOptions(BaseModel):
    """分店分成明细页筛选下拉：门店 / 红娘 / 事件。"""

    stores: list[StoreOption] = Field(default_factory=list)
    matchmakers: list[MatchmakerOption] = Field(default_factory=list)
    events: list[EventOption] = Field(default_factory=list)


class StoreCommissionSummary(BaseModel):
    """分店分成 4 张统计卡。"""

    total_amount: Decimal = Decimal("0")
    current_month_amount: Decimal = Decimal("0")
    previous_month_amount: Decimal = Decimal("0")
    pending_amount: Decimal = Decimal("0")


# ------------------------- M9 财务-发放积分 -------------------------
class CreditGrantTargetType:
    """发放积分目标类型；保留 class 以便 Python 类型提示与 Literal 校验并存。"""

    ALL = "all"
    MEMBER = "member"
    VERIFIED = "verified"
    MATCHMAKER_TEAM = "matchmaker_team"


class CreditGrantRequest(BaseModel):
    """后台「积分明细-发放积分」弹窗入参。"""

    target_type: Literal["all", "member", "verified", "matchmaker_team"] = Field(
        description="all=全部注册用户；member=指定会员；verified=已实名认证会员；matchmaker_team=红娘团队"
    )
    user_ids: list[int] | None = Field(default=None, description="target_type=member 时必填")
    amount: int = Field(gt=0, le=1_000_000, description="每人发放积分数，正整数")
    reason: str = Field(min_length=1, max_length=20, description="发放理由，不超过 20 字")

    @model_validator(mode="after")
    def validate_member_targets(self) -> "CreditGrantRequest":
        if self.target_type == "member":
            if not self.user_ids:
                raise ValueError("指定会员发放必须传入 user_ids")
            if len(self.user_ids) > 5000:
                raise ValueError("单次发放最多 5000 名会员")
        return self


class CreditGrantResult(BaseModel):
    """发放积分结果汇总。"""

    granted_count: int = Field(description="实际发放的账户数（去重后）")
    total_amount: int = Field(description="总发放积分数 = granted_count * amount")
    sample_ledger_ids: list[int] = Field(default_factory=list, description="前 10 条 user_points.id 样本（发放行）")
    target_user_ids: list[int] = Field(default_factory=list, description="实际命中的 user_id 列表")


class CreditDeductRequest(BaseModel):
    """后台「积分明细-扣减积分」弹窗入参（与发放积分对称，写 user_points 负数行）。

    口径（A12-积分修复）：与「发放积分」`credit-grants` 同一账本（`user_points` type=5），
    保证积分明细页发放/扣减同页可见，且列表「积分」列随发放/扣减即时变动。
    """

    target_type: Literal["all", "member", "verified", "matchmaker_team"] = Field(
        description="all=全部注册用户；member=指定会员；verified=已实名认证会员；matchmaker_team=红娘团队"
    )
    user_ids: list[int] | None = Field(default=None, description="target_type=member 时必填")
    amount: int = Field(gt=0, le=1_000_000, description="每人扣减积分数，正整数")
    reason: str = Field(min_length=1, max_length=20, description="扣减理由，不超过 20 字")

    @model_validator(mode="after")
    def validate_member_targets(self) -> "CreditDeductRequest":
        if self.target_type == "member":
            if not self.user_ids:
                raise ValueError("指定会员扣减必须传入 user_ids")
            if len(self.user_ids) > 5000:
                raise ValueError("单次扣减最多 5000 名会员")
        return self


class CreditDeductResult(BaseModel):
    """扣减积分结果汇总。"""

    deducted_count: int = Field(description="实际扣减的账户数（去重后）")
    total_amount: int = Field(description="总扣减积分数 = deducted_count * amount")
    sample_ledger_ids: list[int] = Field(default_factory=list, description="前 10 条 user_points.id 样本（扣减行）")
    target_user_ids: list[int] = Field(default_factory=list, description="实际命中的 user_id 列表")


# ============================================
# A12-钱包：后台余额调整（发放/扣减）+ 按用户余额/流水视图
# 不新建表，复用 account_ledger（account_type='user'）；不接真实支付。
# ============================================


class BalanceAdjustmentRequest(BaseModel):
    """后台「用户余额-修改」入参：按 user_ids 批量发放(CREDIT)或扣减(DEBIT)。"""

    user_ids: list[int] = Field(min_length=1, max_length=5000, description="目标用户 ID 列表，单次最多 5000")
    direction: Literal["CREDIT", "DEBIT"] = Field(description="CREDIT 发放/充值，DEBIT 扣减/调整")
    amount: Decimal = Field(gt=0, le=1_000_000, decimal_places=2, description="每人调整金额（元），正数，两位小数")
    reason: str = Field(min_length=1, max_length=20, description="调整理由，不超过 20 字")

    @model_validator(mode="after")
    def validate_user_ids(self) -> "BalanceAdjustmentRequest":
        cleaned = [int(uid) for uid in self.user_ids if int(uid) > 0]
        if not cleaned:
            raise ValueError("user_ids 必须为正整数")
        self.user_ids = list(dict.fromkeys(cleaned))
        return self


class BalanceAdjustmentResult(BaseModel):
    """余额调整结果汇总。"""

    direction: str
    adjusted_count: int = Field(description="实际写入账本的账户数")
    total_amount: Decimal = Field(description="本次调整总金额 = adjusted_count * amount")
    sample_ledger_ids: list[int] = Field(default_factory=list, description="前 10 条 ledger.id 样本")
    target_user_ids: list[int] = Field(default_factory=list, description="实际命中的 user_id 列表")


class UserWalletResponse(BaseModel):
    """按用户聚合的钱包视图：可用/待用余额 + 最新流水。"""

    account_type: str
    account_id: int
    pending_amount: Decimal
    available_amount: Decimal
    ledger: LedgerEntryPage


# ------------------------- 积分明细列表 -------------------------
# 积分明细页（/system-credit-history）此前只有「发放积分」写接口，没有查询接口。
# 数据取自 C 端积分账本 user_points（amount>0 为入账、<0 为消耗），后台发放/扣减写入
# source_type='admin_grant'（type=5），与「发放/扣减积分」写入同源。

# 会员范围 -> SQL 附加条件（u 为 users 别名）。
CREDIT_MEMBER_SCOPE_SQL: dict[str, str] = {
    "verified": "u.is_real_name = 1",
    "xiangqin": "COALESCE(pr.match_status, 1) IN (1, 2)",
    "xiangqin_female": "COALESCE(pr.match_status, 1) IN (1, 2) AND u.gender = 2",
    "xiangqin_male": "COALESCE(pr.match_status, 1) IN (1, 2) AND u.gender = 1",
    "online_vip": (
        "EXISTS (SELECT 1 FROM user_membership um WHERE um.user_id = u.id AND um.status = 1"
        " AND (um.end_at IS NULL OR um.end_at > UTC_TIMESTAMP()))"
    ),
}

# 积分类别 -> user_points.source_type。
CREDIT_CATEGORY_SOURCES: dict[str, tuple[str, ...]] = {
    "recharge": ("admin_grant",),
    "gift_exchange": ("gift_exchange",),
    "gift_received": ("gift_received",),
    "gift_sent": ("gift_sent",),
}


class CreditRecordItem(BaseModel):
    """积分明细单条流水。"""

    id: int = Field(description="user_points.id")
    user_id: int = Field(description="会员 users.id")
    nickname: str | None = Field(default=None, description="会员昵称")
    avatar: str | None = Field(default=None, description="会员头像")
    phone: str | None = Field(default=None, description="会员手机号（按敏感等级脱敏后下发）")
    direction: str = Field(description="CREDIT 入账 / DEBIT 消耗")
    amount: Decimal = Field(description="变动积分数，正数")
    state: str = Field(description="流水状态；user_points 无冲正概念，恒为 AVAILABLE")
    source_type: str = Field(description="流水来源类型，如 admin_grant（后台发放/扣减）；C 端既有行为 system")
    action_source: str = Field(description="操作来源文案，管理员操作为『管理员操作』")
    category: str | None = Field(default=None, description="积分类别编码；未归类时为 null")
    operator_id: int | None = Field(default=None, description="操作人 users.id；非人工操作时为 null")
    created_at: datetime = Field(description="流水发生时间")


class CreditRecordPage(BaseModel):
    items: list[CreditRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class CreditRecordOptions(BaseModel):
    """积分明细页筛选下拉。"""

    member_scopes: list[dict[str, str]] = Field(default_factory=list, description="会员范围可选项")
    categories: list[dict[str, str]] = Field(default_factory=list, description="积分类别可选项")
    action_sources: list[dict[str, str]] = Field(default_factory=list, description="操作来源可选项")

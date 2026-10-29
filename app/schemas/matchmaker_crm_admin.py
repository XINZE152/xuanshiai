"""Member CRM contracts for the independent matchmaker back office."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class MemberListItem(BaseModel):
    id: int
    nickname: str | None
    phone: str | None
    gender: int | None
    status: int
    is_vip: bool
    vip_end_at: datetime | None
    matchmaker_id: int | None
    created_at: datetime
    avatar: str | None = None
    birthday: date | None = None
    is_married: int | None = None
    height: int | None = None
    weight: int | None = None
    constellation: str | None = None
    zodiac: str | None = None
    household: str | None = None
    ethnicity: str | None = None
    house: str | None = None
    car: str | None = None
    smoking: str | None = None
    drinking: str | None = None
    religion: str | None = None
    marriage_plan: str | None = None
    hometown_province_code: str | None = None
    hometown_city_code: str | None = None
    hometown_district_code: str | None = None
    residence_province_code: str | None = None
    residence_city_code: str | None = None
    residence_district_code: str | None = None
    household_province_code: str | None = None
    household_city_code: str | None = None
    household_district_code: str | None = None
    income: float | None = None
    hometown: str | None = None
    residence: str | None = None
    education: str | None = None
    school: str | None = None
    job: str | None = None
    company: str | None = None
    match_status: int | None = None
    auth_status: int | None = None
    intention_level: int | None = None
    last_follow_at: datetime | None = None
    next_follow_at: datetime | None = None
    # 2026-10-11 新增：会员资料页筛选与展示补列，迁移未执行时 SQL 侧兜底为 NULL/0。
    source: str | None = None
    region: str | None = None
    cert_status: int | None = None
    wechat_added: int | None = None
    promoted: int | None = None
    newcomer: int | None = None
    activity_signup: int | None = None
    level: str | None = None
    fetched_at: datetime | None = None
    assigned_at: datetime | None = None
    abandoned: bool = False  # 弃海（member_abandonment 存在未捞回记录）
    # 2026-10-24 补（B8/A12）：会员列表 4 个展示列 + 最后登录/IP 属地。
    # 所需 JOIN（users u / user_profile p / user_auth ua）本已存在，纯 SELECT 补列；
    # 迁移未执行时 SQL 侧按缺列降级返回 NULL，字段全部可选，不会让整页 500。
    real_name: str | None = None
    tags: dict[str, Any] | list[Any] | str | None = None
    last_login_at: datetime | None = None
    ip_location: str | None = None
    # A12：积分余额取 user_points 流水最新一条 balance，无流水为 0（无需补列）。
    # 不施加 ge=0：与同源的 balance 一致，避免历史负值/未来扣减在列表构造时抛 ValidationError 拖垮整页。
    points: int = Field(default=0)
    # A12-补列：累计登录次数取 users.login_num，缺列（迁移未执行）时降级为 None。
    login_num: int | None = None
    # A12-补列：IP 属地 / 手机号归属地取 users.ip_region / phone_region，
    # 本轮仅展示不自动解析（缺列降级 None）；ip_location 仍为原始注册 IP。
    ip_region: str | None = None
    phone_region: str | None = None
    # A12-钱包（余额）：取 account_ledger 中 account_type='user' 的可用余额，
    # 与 finance.get_balance 口径一致（无流水为 0）；纯只读派生，不新建表。
    balance: Decimal = Field(default=Decimal(0))


class MemberPage(BaseModel):
    items: list[MemberListItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberDetail(MemberListItem):
    avatar: str | None
    birthday: date | None
    is_married: int | None
    residence_city_code: str | None
    education: str | None = None
    job: str | None = None
    # B10：放宽 tags 出参类型。库里历史脏数据可能出现非列表值（如嵌套对象
    # {"custom_categories": {}}），窄类型会让整个详情接口 500。这里接受任意合法
    # JSON 结构，保证单个字段的形态差异不会拖垮整个响应。
    tags: dict[str, Any] | list[Any] | str | None = None
    self_intro: str | None = None
    ideal_partner: str | None = None
    wechat: str | None = None
    last_login_at: datetime | None = None
    ip_location: str | None = None
    # A12-微信绑定（第389行）：抽屉展示微信绑定状态。openid 本身为登录身份标识，不外传，
    # 只回是否已绑定与绑定时间；缺列时降级为未绑定。
    wechat_bound: bool = False
    wechat_bound_at: datetime | None = None


class MemberWechatBindRequest(BaseModel):
    """后台手动为会员绑定微信（写入 openid/unionid）。敏感写：需 confirm=true + Idempotency-Key。"""

    openid: str = Field(min_length=1, max_length=64, description="待绑定的微信 openid（全局唯一）")
    unionid: str | None = Field(default=None, max_length=64, description="微信 unionid，可选")
    reason: str = Field(min_length=1, max_length=255, description="绑定理由（记入审计）")


class MemberStatusUpdate(BaseModel):
    status: Literal[1, 2, 3]
    reason: str = Field(min_length=1, max_length=255)


class MemberStatusResponse(BaseModel):
    id: int
    status: Literal[1, 2, 3]
    reason: str


class MemberProfileUpdate(BaseModel):
    """后台统一修改会员基础、认证和隐私信息。未提交字段保持不变。"""

    nickname: str | None = Field(default=None, max_length=64)
    avatar: str | None = Field(default=None, max_length=255)
    status: Literal[1, 2, 3] | None = None
    is_married: Literal[0, 1, 2, 3] | None = None
    is_single_pledge: bool | None = None
    real_name: str | None = Field(default=None, max_length=64)
    auth_status: Literal[0, 1, 2, 3] | None = None
    house_verified: Literal[0, 1, 2, 3] | None = None
    education_verified: Literal[0, 1, 2, 3] | None = None
    match_status: Literal[1, 2, 3, 4, 5] | None = None
    only_vip_can_see_detail: bool | None = None
    show_profile: bool | None = None
    reason: str = Field(min_length=1, max_length=255)


class MemberProfileUpdateResponse(BaseModel):
    user_id: int
    updated_fields: list[str]
    reason: str


class SourceDistributionItem(BaseModel):
    """来源分布单项：value 为原始 source 值（空值归为 "" 表示未填写），label 用于展示。"""

    value: str
    label: str
    count: int


class MemberStatistics(BaseModel):
    total: int
    male: int
    female: int
    vip: int
    active: int
    unassigned: int = 0
    never_followed: int = 0
    follow_due_today: int = 0
    abandoned: int = 0
    # B5/A11：会员登记来源占比。含 source 为空的一档（value="" / label="未填写来源"），
    # 供概览页环形图直接取用，避免前端并发多次列表倒推。
    source_distribution: list[SourceDistributionItem] = []


class MemberOption(BaseModel):
    """下拉字典项：value 为接口筛选值，label 为界面展示文案。"""

    value: str
    label: str


class MemberOptions(BaseModel):
    """会员资料页筛选下拉的可选项。

    门店/推广红娘在原界面里是写死的空下拉（选项未填充），这里按当前账号的数据
    范围动态给值，避免「看得见选不了」。
    """

    organizations: list[MemberOption] = []
    promoters: list[MemberOption] = []


class MemberAssignmentUpdate(BaseModel):
    matchmaker_id: int | None = Field(default=None, ge=1, description="服务红娘用户 ID；传 null 表示取消分派")

class MemberAssignmentResponse(BaseModel):
    user_id: int
    matchmaker_id: int | None


class MatchRecordCreate(BaseModel):
    from_love_user_id: int = Field(ge=1)
    to_love_user_id: int = Field(ge=1)
    create_time: datetime
    complete_time: datetime
    line_status: Literal[1, 2] = Field(description="1 成功，2 失败")


class MatchRecordResponse(BaseModel):
    id: int
    from_user_id: int
    to_user_id: int
    status: Literal[0, 1, 2, 3]
    created_at: datetime
    responded_at: datetime | None


class MatchRecordItem(MatchRecordResponse):
    from_nickname: str | None = None
    to_nickname: str | None = None
    matchmaker_id: int | None = None
    # 2026-10-28 补（对标站牵线记录列）：均为真值派生，不新建表。
    # gender 取 users.gender（1男2女，0/null 未知）；age 用 TIMESTAMPDIFF 由 birthday 算，birthday 空则 null；
    # is_verified = user_auth.realname_status==2；has_promise = users.is_single_pledge==1（单身承诺书）；
    # matchmaker_name = 归属服务红娘昵称。无实名/无承诺书关联时 SQL COALESCE 为 0（false），
    # 且下方校验器兜底把任何漏进的 None 归一为 False，杜绝 bool 字段收到 NULL 触发整页 500。
    from_gender: int | None = None
    to_gender: int | None = None
    from_age: int | None = None
    to_age: int | None = None
    from_is_verified: bool = False
    to_is_verified: bool = False
    from_has_promise: bool = False
    to_has_promise: bool = False
    matchmaker_name: str | None = None
    # 2026-10-28 补（头像/会员编号）：均为真值派生，不新建列。
    # avatar 取 COALESCE(users.avatar, user_profile.photos[0])；member_code = CONCAT('G', LPAD(id,6,'0'))（与会员列表同口径）。
    from_avatar: str | None = None
    to_avatar: str | None = None
    from_member_code: str | None = None
    to_member_code: str | None = None

    @field_validator(
        "from_is_verified", "to_is_verified", "from_has_promise", "to_has_promise", mode="before",
    )
    @classmethod
    def _bool_from_none(cls, v: Any) -> Any:
        # LEFT JOIN 无关联时布尔派生列可能为 NULL；None 视为 false，避免 bool 字段校验失败 500。
        return False if v is None else v


class MatchRecordPage(BaseModel):
    items: list[MatchRecordItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MatchRecordStatistics(BaseModel):
    """顶部统计卡口径：待牵线=未分派红娘的待处理，牵线中=已分派红娘的待处理。"""

    total: int
    pending: int
    matching: int
    success: int
    fail: int
    closed: int


class MatchRecordUpdate(BaseModel):
    status: Literal[0, 1, 2, 3]
    reason: str | None = Field(default=None, max_length=255)

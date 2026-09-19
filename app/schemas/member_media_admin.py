"""会员资料媒体验证（M3-2）管理后台契约：个人介绍编辑 + 头像/照片/视频媒体审核。

所有出参字段名与前端 ``content-verify`` 页面严格对应；会员编号统一为
``G`` + 6 位左补零（由 SQL ``CONCAT('G', LPAD(u.id, 6, '0'))`` 生成）。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── 个人介绍（自白内容） ───────────────────────────────────────


class MemberIntroItem(BaseModel):
    """个人介绍行。``id`` 等于 ``user_id``，便于前端「查看资料」复用。"""

    id: int  # 列表主键（= user_id）
    user_id: int  # 会员 user_id，「查看资料」使用
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 头像 URL
    self_intro: str | None = None  # 自白内容（个人介绍）
    updated_at: datetime | None = None  # 自白最近修改时间


class MemberIntroPage(BaseModel):
    """个人介绍分页响应。"""

    items: list[MemberIntroItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberIntroUpdate(BaseModel):
    """个人介绍（自白内容）更新请求体。允许为空串（清空自白）。"""

    self_intro: str = Field(..., max_length=500)  # 自白内容，≤500 字；空串表示清空（不限制最小长度）


# ─── 媒体（头像/照片/视频） ─────────────────────────────────────


class MemberMediaItem(BaseModel):
    """媒体审核行（头像/照片/视频通用）。"""

    id: int  # 媒体记录主键（user_media.id）
    user_id: int  # 关联会员 user_id
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 会员头像 URL
    media_type: str  # avatar / photo / video
    file_url: str | None = None  # 媒体文件地址
    thumbnail_url: str | None = None  # 缩略图地址（视频/部分图片）
    mime_type: str | None = None  # 文件 MIME 类型
    duration_seconds: int | None = None  # 视频时长（秒）
    review_status: int  # 0审核中 1已通过 2未通过 3已隐藏
    review_status_label: str  # 审核状态中文标签（审核中/已通过/未通过/已隐藏）
    review_reason: str | None = None  # 审核不通过/隐藏原因
    age: int | None = None  # 由 birthday 推算的年龄（无生日为 None）
    meta_text: str | None = None  # 形如 "1990年 175cm 大专"，缺项跳过；全空为 None
    created_at: datetime | None = None  # 上传时间


class MemberMediaPage(BaseModel):
    """媒体分页响应。"""

    items: list[MemberMediaItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MemberMediaReview(BaseModel):
    """媒体审核请求体：通过(1)/未通过(2)/隐藏(3)。"""

    review_status: int = Field(..., ge=1, le=3)  # 1通过 2未通过 3隐藏（0 审核中由上传自动置位，不可手动置回）
    review_reason: str | None = Field(None, max_length=255)  # 不通过/隐藏原因


class MemberMediaReplace(BaseModel):
    """媒体重新上传请求体：覆盖文件并将审核状态归零（待审核）。"""

    file_url: str = Field(..., min_length=1, max_length=512)  # 新文件地址（必填，不可为空）
    thumbnail_url: str | None = Field(None, max_length=512)  # 新缩略图地址（可选）


# ─── 资料扩展字段（性格/爱好/MBTI/自我介绍/红娘说） ─────────────


class MemberProfileExtItem(BaseModel):
    """会员资料扩展字段行。``id`` 等于 ``user_id``，便于前端「查看资料」复用。"""

    id: int  # 列表主键（= user_id）
    user_id: int  # 会员 user_id
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 头像 URL
    personality_tags: list[str] = []  # 性格标签（user_profile.personality_tags json）
    hobbies: str | None = None  # 爱好（user_profile.hobbies）
    mbti: str | None = None  # MBTI（user_profile.mbti）
    self_intro: str | None = None  # 自我介绍（user_profile.self_intro）
    matchmaker_note: str | None = None  # 红娘说（user_profile.matchmaker_note）
    updated_at: datetime | None = None  # 最近修改时间


class MemberProfileExtUpdate(BaseModel):
    """资料扩展字段更新请求体。全部可选，至少提供一个字段；性格传 [] 表示清空。"""

    personality_tags: list[str] | None = Field(
        default=None, min_length=0, max_length=10, description="性格标签，0～10 个；空数组清空"
    )
    hobbies: str | None = Field(default=None, max_length=500)
    mbti: str | None = Field(default=None, max_length=16)
    self_intro: str | None = Field(default=None, max_length=500)
    matchmaker_note: str | None = Field(default=None, max_length=1000)

    @field_validator("personality_tags")
    @classmethod
    def validate_personality_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if len(value) != len({tag.strip() for tag in value}):
            raise ValueError("性格标签不能重复")
        if any(not tag.strip() for tag in value):
            raise ValueError("性格标签不能为空")
        if any(len(tag) > 20 for tag in value):
            raise ValueError("单个性格标签不能超过 20 字")
        return value

    @model_validator(mode="after")
    def require_update(self) -> "MemberProfileExtUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


# ─── 会员推荐（后台按条件筛候选人） ────────────────────────────


class MemberRecommendItem(BaseModel):
    """推荐候选人卡片。字段取自 users + user_profile，供红娘后台筛人列表用。"""

    user_id: int  # 候选人 user_id
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 头像 URL
    gender: int | None = None  # 1男 2女
    age: int | None = None  # 按生日计算
    height: int | None = None  # 身高 cm
    income: float | None = None  # 月收入
    education_level: int | None = None  # 学历 1-8
    occupation: str | None = None  # 职业
    constellation: str | None = None  # 星座
    mbti: str | None = None  # 人格类型
    hometown: str | None = None  # 家乡
    residence: str | None = None  # 现居
    smoking: str | None = None  # 抽烟
    drinking: str | None = None  # 喝酒
    house: str | None = None  # 住房
    ethnicity: str | None = None  # 民族
    tags: list[str] = []  # 标签（tags/interest_tags 合并去重）
    matchmaker_id: int | None = None  # 服务红娘后台账号 ID
    matchmaker_name: str | None = None  # 服务红娘姓名


class MemberRecommendPage(BaseModel):
    """推荐候选人分页。"""

    items: list[MemberRecommendItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    has_more: bool


# ─── 择偶要求（后台管理端） ─────────────────────────────────────
# 与 C 端 PreferenceUpdateRequest 相互独立：图中单选项以中文字符串落库
# （风格同 dating_goal/children_intention），不占用 C 端 tinyint 枚举口径。

IncomeRangeOption = Literal[
    "不限", "3千元以下", "3千-5千元", "5-8千元", "8千-1万元",
    "1-2万元", "2万以上", "5万以上", "年入百万",
]
EducationRequirementOption = Literal[
    "不限", "初中", "技校", "高中", "中专", "大专", "本科", "硕士", "博士",
]
OccupationRequirementOption = Literal[
    "不限", "私企员工", "央企/国企", "外企", "事业单位", "公务员", "教师",
    "医生", "护士", "互联网行业", "自由职业", "军人", "工人", "服务业",
    "金融", "律师", "求职中", "在校学生", "个体老板", "公司高管",
    "美容师", "健身教练",
]
MarriageRequirementOption = Literal[
    "不限", "不接受离异", "可接受离异未育", "可接受离异有孩子", "视情况而定",
]
HousingExpectationOption = Literal[
    "不限", "愿意和父母同住", "要有独立婚房", "住房无所谓",
]
SmokingExpectationOption = Literal[
    "不限", "不接受吸烟", "可以偶尔吸烟", "吸烟无所谓",
]
DrinkingExpectationOption = Literal[
    "不限", "不接受喝酒", "可以偶尔小酌", "喝酒无所谓",
]
MarriageTimelineOption = Literal[
    "不限", "一年内结婚", "两年内结婚", "三年内结婚", "时机成熟时结婚",
]


class MemberPreferenceAdminItem(BaseModel):
    """会员择偶要求行。``id`` 等于 ``user_id``。"""

    id: int  # 主键（= user_id）
    user_id: int  # 会员 user_id
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 昵称
    avatar: str | None = None  # 头像 URL
    age_min: int | None = None  # 期望年龄下限
    age_max: int | None = None  # 期望年龄上限
    height_min: int | None = None  # 期望身高下限 cm
    height_max: int | None = None  # 期望身高上限 cm
    income_range: str | None = None  # 收入档位（单选）
    education_requirement: str | None = None  # 学历（单选）
    preferred_occupation: str | None = None  # 职业（单选）
    marriage_requirement: str | None = None  # 婚况（单选）
    housing_expectation: str | None = None  # 住房（单选）
    smoking_expectation: str | None = None  # 吸烟（单选）
    drinking_expectation: str | None = None  # 喝酒（单选）
    marriage_timeline: str | None = None  # 结婚计划（单选）
    extra_requirement: str | None = None  # 补充说明（≤200 字）
    updated_at: datetime | None = None  # 最近修改时间


class MemberPreferenceAdminUpdate(BaseModel):
    """择偶要求更新请求体。全部可选，至少提供一个字段。"""

    age_min: int | None = Field(default=None, ge=18, le=100)
    age_max: int | None = Field(default=None, ge=18, le=100)
    height_min: int | None = Field(default=None, ge=100, le=250)
    height_max: int | None = Field(default=None, ge=100, le=250)
    income_range: IncomeRangeOption | None = None
    education_requirement: EducationRequirementOption | None = None
    preferred_occupation: OccupationRequirementOption | None = None
    marriage_requirement: MarriageRequirementOption | None = None
    housing_expectation: HousingExpectationOption | None = None
    smoking_expectation: SmokingExpectationOption | None = None
    drinking_expectation: DrinkingExpectationOption | None = None
    marriage_timeline: MarriageTimelineOption | None = None
    extra_requirement: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_ranges(self) -> "MemberPreferenceAdminUpdate":
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("期望年龄下限不能大于上限")
        if self.height_min is not None and self.height_max is not None and self.height_min > self.height_max:
            raise ValueError("期望身高下限不能大于上限")
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self

"""会员认证（M3-1）管理后台契约：实名/承诺/婚姻/房产/学历/其他认证审核与认证类型管理。

所有出参字段名与前端 ``love-user-auth`` 页面严格对应；金额类字段一律序列化为字符串，
会员编号统一为 ``G`` + 6 位左补零（由 SQL ``CONCAT('G', LPAD(u.id, 6, '0'))`` 生成）。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ─── 通用认证审核行（实名/承诺/房产/学历/其他复用） ─────────────────────


class MemberAuthReviewItem(BaseModel):
    """通用认证审核行。各认证类型在此公共字段基础上扩展。"""

    id: int  # 审核记录主键（实名/房产/学历为 user_auth.id，承诺/婚姻/其他为各自表 id）
    user_id: int  # 关联会员 user_id，用于「查看资料」
    member_code: str  # 会员编号 G+6 位
    nickname: str | None = None  # 会员昵称
    avatar: str | None = None  # 会员头像 URL
    real_name: str | None = None  # 实名姓名（来自 user_auth.real_name）
    id_card_masked: str | None = None  # 身份证号（仅掩码展示，如 330105199306******）
    file_url: str | None = None  # 文件凭证地址（房产/学历/其他为对应凭证；承诺为签名文件）
    result: str  # 审核结果枚举：pending/pass/fail；实名另用 success/fail
    result_label: str  # 结果中文标签：待审/通过/未通过（实名：认证成功/认证失败/待审）
    created_at: datetime | None = None  # 提交时间


class MemberAuthReviewPage(BaseModel):
    """通用分页响应（items 为 MemberAuthReviewItem）。"""

    items: list[MemberAuthReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 实名认证 ─────────────────────────────────────────────────────


class RealnameReviewItem(MemberAuthReviewItem):
    """实名认证行：在公共字段基础上扩展人脸核验与证件信息。"""

    gender: str | None = None  # 性别：男/女
    birthday: str | None = None  # 出生日期 YYYY-MM-DD
    id_card_issued: str | None = None  # 身份证发证机关
    id_card_front: str | None = None  # 身份证正面 URL（用于「证件照片」列判断有/未上传）
    id_card_back: str | None = None  # 身份证反面 URL
    face_method: str | None = None  # 验证方式：动作活检/照片比对
    face_vendor: str | None = None  # 人脸服务商：如腾讯云人脸核身
    face_score: str | None = None  # 人脸比对得分（Decimal 序列化为字符串）
    face_photo: str | None = None  # 人脸核验文件 URL（用于「核验文件」缩略图）
    # result 覆盖为：success（verified=1）/ fail（verified=2）/ pending（verified=0）


class RealnameReviewPage(BaseModel):
    items: list[RealnameReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class RealnameStats(BaseModel):
    """实名认证统计卡数据。"""

    quota_remaining: int = 0  # 人脸核验余量（当前无独立额度数据源，默认 0，见服务注释）
    success_count: int = 0  # 核验成功次数（face_verified=1）
    fail_count: int = 0  # 核验失败次数（face_verified=2）
    total_consumed: int = 0  # 总计消耗次数（success + fail）


# ─── 会员承诺 ─────────────────────────────────────────────────────


class CommitmentReviewItem(MemberAuthReviewItem):
    """会员承诺书签署记录行。"""

    sign_times: int = 1  # 第几次签署
    title: str | None = None  # 承诺书标题


class CommitmentReviewPage(BaseModel):
    items: list[CommitmentReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 婚姻状况 ─────────────────────────────────────────────────────


class MarriageReviewItem(MemberAuthReviewItem):
    """婚姻状况核验记录行。result 枚举：married/no_record/divorced。"""

    check_method: str | None = None  # 核验方式：民政数据接口/线下核查
    declared_status: str | None = None  # 客户资料中填写的婚姻状态
    cost: str | None = None  # 查询费用（Decimal 序列化为字符串）
    checked_at: datetime | None = None  # 核验时间


class MarriageReviewPage(BaseModel):
    items: list[MarriageReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 房产认证 ─────────────────────────────────────────────────────


class HouseReviewItem(MemberAuthReviewItem):
    """房产认证行（file_url = user_auth.house_cert）。"""


class HouseReviewPage(BaseModel):
    items: list[HouseReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 学历认证 ─────────────────────────────────────────────────────


class EducationReviewItem(MemberAuthReviewItem):
    """学历认证行。"""

    degree: str | None = None  # 学历：博士/硕士/本科…
    school: str | None = None  # 毕业学校


class EducationReviewPage(BaseModel):
    items: list[EducationReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 其他认证 ─────────────────────────────────────────────────────


class OtherReviewItem(MemberAuthReviewItem):
    """其他认证提交记录行。"""

    auth_type_id: int | None = None  # 关联认证类型 config_auth_type.id
    auth_type_name: str | None = None  # 认证类型名称（快照）


class OtherReviewPage(BaseModel):
    items: list[OtherReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─── 婚姻状况统计 ─────────────────────────────────────────────────


class MarriageStats(BaseModel):
    """婚姻状况统计卡数据。"""

    quota_remaining: int = 0  # 婚况核验查询余量（当前无独立额度数据源，默认 0）
    total_consumed: int = 0  # 总计消耗次数（user_marriage_check 记录数）


# ─── 认证类型配置（其他认证） ─────────────────────────────────────


class AuthTypeItem(BaseModel):
    """认证类型配置项。"""

    id: int
    name: str  # 认证类型名称（唯一）
    icon_url: str | None = None  # 认证图标 URL
    description: str | None = None  # 说明文案
    require_realname: bool = True  # 提交前是否要求先完成实名认证
    sort: int = 0  # 显示排序，数字越大越靠前
    status: int = 1  # 1启用 0关闭
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AuthTypeCreate(BaseModel):
    """创建认证类型请求体。"""

    name: str = Field(..., min_length=1, max_length=64)
    icon_url: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=1000)
    require_realname: bool = True
    sort: int = Field(default=0, ge=0)
    status: int = Field(default=1, ge=0, le=1)


class AuthTypeUpdate(BaseModel):
    """更新认证类型请求体（全字段可选，复用 AuthTypeCreate 字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    icon_url: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=1000)
    require_realname: bool | None = None
    sort: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=1)


# ─── 审核动作 / 删除 ───────────────────────────────────────────────


class ReviewActionRequest(BaseModel):
    """认证审核动作：通过(1)/未通过(2)。"""

    status: int = Field(..., ge=1, le=2)  # 1 通过 2 未通过
    remark: str | None = Field(default=None, max_length=255)


class MemberAuthConfigUpdate(BaseModel):
    """通用配置请求体（用于本模块三处配置抽屉，payload 为具体配置键值）。"""

    payload: dict[str, Any] = Field(default_factory=dict)

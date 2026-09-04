"""User supplied certification materials and review status."""

from datetime import datetime

from typing import Literal

from pydantic import BaseModel


class CertificationItem(BaseModel):
    kind: str
    status: int
    material_submitted: bool
    material: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    fail_reason: str | None
    next_action: str


class CertificationsResponse(BaseModel):
    education: CertificationItem
    house: CertificationItem
    marriage: CertificationItem


class CertificationReviewItem(BaseModel):
    user_id: int
    nickname: str | None
    kind: Literal["education", "house", "marriage"]
    status: int
    material_submitted: bool
    material: str | None = None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    fail_reason: str | None


class CertificationReviewPage(BaseModel):
    items: list[CertificationReviewItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class EducationCertificationRequest(BaseModel):
    education: Literal["小学", "初中", "高中", "中专", "大专", "本科", "硕士", "博士"]


class MarriageCertificationRequest(BaseModel):
    is_unmarried: bool

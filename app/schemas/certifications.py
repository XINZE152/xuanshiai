"""User supplied certification materials and review status."""

from datetime import datetime

from typing import Literal

from pydantic import BaseModel


class CertificationItem(BaseModel):
    kind: str
    status: int
    material_submitted: bool
    submitted_at: datetime | None
    reviewed_at: datetime | None
    fail_reason: str | None
    next_action: str


class CertificationsResponse(BaseModel):
    education: CertificationItem
    house: CertificationItem
    marriage: CertificationItem


class EducationCertificationRequest(BaseModel):
    education: Literal["小学", "初中", "高中", "中专", "大专", "本科", "硕士", "博士"]


class MarriageCertificationRequest(BaseModel):
    is_unmarried: bool

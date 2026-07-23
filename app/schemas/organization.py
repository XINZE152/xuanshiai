"""一期组织、门店和归属接口契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class StoreCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    region_code: str | None = Field(default=None, max_length=64)
    auto_redirect: bool = False


class StoreResponse(BaseModel):
    id: int
    code: str
    name: str
    display_name: str | None
    region_code: str | None
    status: Literal[1, 2, 3]
    auto_redirect: bool
    created_at: datetime


class StoreMemberCreate(BaseModel):
    user_id: int = Field(ge=1)
    role_code: Literal["store_manager", "store_matchmaker"]


class StoreMemberResponse(BaseModel):
    id: int
    organization_id: int
    user_id: int
    role_code: str
    status: Literal[1, 2, 3]
    started_at: datetime
    ended_at: datetime | None


class ResourceAssignmentCreate(BaseModel):
    user_id: int = Field(ge=1)
    organization_id: int | None = Field(default=None, ge=1)
    matchmaker_id: int | None = Field(default=None, ge=1)
    source: Literal["manual", "promotion", "default", "self_created"] = "manual"


class ResourceAssignmentResponse(BaseModel):
    id: int
    user_id: int
    organization_id: int | None
    matchmaker_id: int | None
    source: str
    status: Literal[1, 2]
    effective_at: datetime
    ended_at: datetime | None


class PromotionTouchCreate(BaseModel):
    code: str = Field(min_length=6, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    promoter_id: int = Field(ge=1)
    partner_team_id: int | None = Field(default=None, ge=1)


class PromotionTouchResponse(BaseModel):
    id: int
    code: str
    promoter_id: int
    partner_team_id: int | None
    registered_user_id: int | None
    created_at: datetime


class PromotionAttributionCreate(BaseModel):
    code: str = Field(min_length=6, max_length=128)


class PromotionAttributionResponse(BaseModel):
    id: int
    user_id: int
    promoter_id: int
    touch_id: int
    status: Literal[1, 2, 3]
    effective_at: datetime
    ended_at: datetime | None


class PartnerTeamCreate(BaseModel):
    owner_user_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    open_mode: Literal["manual", "paid"] = "manual"


class PartnerTeamResponse(BaseModel):
    id: int
    owner_user_id: int
    name: str
    status: Literal[1, 2, 3]
    open_mode: str
    created_at: datetime


class PartnerJoinCreate(BaseModel):
    team_id: int = Field(ge=1)


class PartnerMembershipResponse(BaseModel):
    id: int
    team_id: int
    promoter_id: int
    status: Literal[1, 2, 3]
    joined_at: datetime
    left_at: datetime | None

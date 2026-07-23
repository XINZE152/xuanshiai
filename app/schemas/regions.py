"""Administrative region lookup response models."""

from pydantic import BaseModel


class RegionItem(BaseModel):
    code: str
    name: str


class RegionListResponse(BaseModel):
    items: list[RegionItem]
    total: int

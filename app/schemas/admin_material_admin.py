"""Cloud material library (云端素材库, A4) contracts for the back office."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────── 素材分类 ───────────────────────────


class MaterialCategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, ge=1)
    sort: int = Field(default=0, ge=0, le=100000)
    enabled: bool = True


class MaterialCategoryCreate(MaterialCategoryBase):
    pass


class MaterialCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, ge=1)
    sort: int | None = Field(default=None, ge=0, le=100000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "MaterialCategoryUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MaterialCategoryItem(MaterialCategoryBase):
    id: int
    material_count: int = 0
    created_at: datetime | None = None


class MaterialCategoryPage(BaseModel):
    items: list[MaterialCategoryItem]
    page: int
    page_size: int
    total: int
    has_more: bool


# ─────────────────────────── 素材 ───────────────────────────


class MaterialItem(BaseModel):
    id: int
    category_id: int
    category_name: str | None = None
    name: str
    url: str
    thumbnail_url: str
    mime_type: str
    size: int
    width: int | None = None
    height: int | None = None
    uploaded_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MaterialUpdate(BaseModel):
    """改名 / 移动分类（URL 不可改，文件已由上传时确定）。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    category_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_update(self) -> "MaterialUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class MaterialPage(BaseModel):
    items: list[MaterialItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class MaterialUploadResponse(BaseModel):
    """上传成功后返回，url/thumbnail_url 为现签的临时访问地址。"""

    id: int
    name: str
    url: str
    thumbnail_url: str
    mime_type: str
    size: int


class MaterialPublicCopyResponse(BaseModel):
    """Public common-image URL for long-lived platform configuration."""

    material_id: int
    url: str

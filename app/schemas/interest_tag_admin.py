"""兴趣标签目录（分类 + 标签）后台 CRUD 的接口契约。

目录落 `interest_tag_category` / `interest_tag` 两张表，供前台 `tag-options` 与
`personal_tags` 校验读取（经进程内缓存 `app/core/tag_catalog.py`）。

字段口径对齐客源来源字典：`enabled` 用 0/1 整数、`sort` 越小越靠前。分类的 `key`
是前端与存储引用的稳定标识，创建后不可改（改名只动 `label`）；标签可从属某个分类
`category_key`，更新不允许换分类（避免历史 `tag_selections` 归属漂移）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KEY_PATTERN = r"^[a-z0-9_]{1,64}$"


class TagCategoryItem(BaseModel):
    """分类行。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    label: str
    enabled: int = 1
    sort: int = 0


class TagCategoryCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=_KEY_PATTERN, description="分类稳定 key，仅小写字母/数字/下划线")
    label: str = Field(min_length=1, max_length=64)
    enabled: int = Field(default=1, ge=0, le=1)
    sort: int = Field(default=0, ge=-1000000, le=1000000)


class TagCategoryUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def require_update(self) -> "TagCategoryUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class TagItem(BaseModel):
    """标签行。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category_key: str
    name: str
    enabled: int = 1
    sort: int = 0


class TagCreate(BaseModel):
    category_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    enabled: int = Field(default=1, ge=0, le=1)
    sort: int = Field(default=0, ge=-1000000, le=1000000)


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def require_update(self) -> "TagUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class TagOrder(BaseModel):
    """重排：ids 为期望的从前到后顺序，服务端按下标写 sort（从 1 递增）。"""

    ids: list[int] = Field(min_length=1, max_length=1000)

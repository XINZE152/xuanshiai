"""客源来源字典（A14）的接口契约。

来源落 `customer_lead_source` 字典表。字典为全局配置（与
`lead_options` 口径一致），不随数据范围（D-16）收敛；删除时若仍
被有效客源引用则返回 409，与商家分类删除的守卫一致。
（客源标签字典已下线，本契约仅供 `sources` 使用。）
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LeadDictItem(BaseModel):
    """来源 / 标签字典行。type 区分两类，便于前端复用同一渲染组件。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    color: str | None = None
    enabled: int = 1
    sort: int = 0


class LeadDictCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=16)
    enabled: int = Field(default=1, ge=0, le=1)
    sort: int = Field(default=0, ge=-1000000, le=1000000)


class LeadDictUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=16)
    enabled: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def require_update(self) -> "LeadDictUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class LeadDictOrder(BaseModel):
    """重排：ids 为期望的从前到后顺序，服务端按数组下标写入 sort。"""

    ids: list[int] = Field(min_length=1, max_length=500)

"""匿名页面显示开关下发响应（PAGE-SWITCH-20261116）。"""

from pydantic import BaseModel, Field

from app.schemas.ai_common import AiCapabilitiesResponse


class PublicPageSwitchOperation(BaseModel):
    registration_enabled: bool = Field(description="新用户注册总开关（platform_operation 与 sys_access 同时生效）")
    browse_enabled: bool = Field(description="浏览/发现总开关（platform_operation 与 sys_access 同时生效）")
    maintenance_message: str = Field(description="平台维护文案")


class PublicPageSwitchesResponse(BaseModel):
    version: int = Field(description="platform_page_switches 快照版本号；未保存过为 0，供前端缓存比对")
    modules: dict[str, bool] = Field(description="10 个模块开关原始值")
    pages: dict[str, bool] = Field(description="71 个页面路径的生效值（模块 AND 页面；登录/注册/首页恒 true）")
    tabs: list[str] = Field(description="5 个 tab 页路径（自绘 XsaTabBar 条目过滤用）")
    placeholder_text: str = Field(description="功能关闭时的占位文案")
    operation: PublicPageSwitchOperation = Field(description="平台运营总开关")
    ai: AiCapabilitiesResponse = Field(
        description="AI 公开基准位（不含登录用户测试范围）；登录用户权威判定仍是 GET /ai/capabilities"
    )
"""微信公众号统一接口。

后台运营接口只依赖本协议：粉丝同步 / 菜单发布 / 模板消息同步与发送。
凭据取自环境变量（``WECHAT_MP_*``），库内 ``wechat_mp`` 配置域只存非敏感项，
因此协议实现不接受调用方传入密钥。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WechatFan:
    openid: str
    unionid: str | None = None
    nickname: str | None = None
    avatar_url: str | None = None
    gender: int = 0
    city: str | None = None
    province: str | None = None
    subscribe_status: int = 1
    subscribe_at: object | None = None
    provider_group_id: int | None = None


@dataclass(frozen=True)
class WechatTemplate:
    template_id: str
    title: str
    primary_industry: str | None = None
    deputy_industry: str | None = None
    content: str | None = None


class WechatMpProvider(Protocol):
    """公众号供应商必须实现的接口；未配置时一律抛 503。"""

    name: str

    async def fetch_fans(self) -> list[WechatFan]:
        """拉取全部粉丝（含关注状态）。"""

    async def publish_menu(self, menu: dict) -> None:
        """发布自定义菜单。"""

    async def fetch_templates(self) -> list[WechatTemplate]:
        """拉取账号下已添加的模板消息。"""

    async def send_template(
        self, openid: str, template_id: str, data: dict[str, str], url: str | None
    ) -> str:
        """发送模板消息，返回微信侧 msgid。"""

    async def broadcast(
        self, openids: list[str], content: str, url: str | None
    ) -> str | None:
        """图文群发（按 openid 列表），返回微信侧 msg_id。"""

    async def fetch_miniprogram_code(self, path: str, scene: str) -> bytes:
        """生成小程序码 PNG（A5 ``type=miniprogram`` 使用）；未配置时 503。"""

    async def create_qrcode(self, scene_str: str) -> str:
        """创建公众号带参二维码，返回可直接展示的 showqrcode 图片地址。"""

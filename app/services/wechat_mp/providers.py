"""微信公众号供应商实现与工厂。

真实实现通过公开 HTTP API 直连（与 ``services/discovery.create_poster`` 同风格），
access_token 按进程内缓存复用；凭据缺任何一项都 503，绝不返回假成功。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.wechat_mp.base import WechatFan, WechatMpProvider, WechatTemplate

_API_BASE = "https://api.weixin.qq.com"


class DisabledWechatMpProvider:
    """未配置公众号凭据时的实现：所有动作 fail-closed。"""

    name = "disabled"

    async def fetch_fans(self) -> list[WechatFan]:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def publish_menu(self, menu: dict) -> None:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def fetch_templates(self) -> list[WechatTemplate]:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def send_template(
        self, openid: str, template_id: str, data: dict[str, str], url: str | None
    ) -> str:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def broadcast(self, openids: list[str], content: str, url: str | None) -> str | None:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def fetch_miniprogram_code(self, path: str, scene: str) -> bytes:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")

    async def create_qrcode(self, scene_str: str) -> str:
        raise HTTPException(status_code=503, detail="公众号凭据未配置")


class MockWechatMpProvider:
    """仅 development/testing：不发真实请求，返回可对账的假结果。"""

    name = "mock"

    def _guard(self) -> None:
        if not settings.is_test_mode:
            raise HTTPException(status_code=503, detail="公众号 Mock 服务仅允许测试环境使用")

    async def fetch_fans(self) -> list[WechatFan]:
        self._guard()
        return []

    async def publish_menu(self, menu: dict) -> None:
        self._guard()

    async def fetch_templates(self) -> list[WechatTemplate]:
        self._guard()
        return []

    async def send_template(
        self, openid: str, template_id: str, data: dict[str, str], url: str | None
    ) -> str:
        self._guard()
        return f"mock-{openid[-6:]}-{template_id}"

    async def broadcast(self, openids: list[str], content: str, url: str | None) -> str | None:
        self._guard()
        return f"mock-broadcast-{len(openids)}"

    async def fetch_miniprogram_code(self, path: str, scene: str) -> bytes:
        self._guard()
        raise HTTPException(status_code=503, detail="Mock 公众号不生成小程序码")

    async def create_qrcode(self, scene_str: str) -> str:
        self._guard()
        return f"https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=mock-{scene_str}"


class HttpWechatMpProvider:
    """微信公众号 HTTP 实现（服务号 / 订阅号通用公开接口）。"""

    name = "wechat_mp"
    _token_cache: dict[str, tuple[str, float]] = {}

    def __init__(self, app_id: str, app_secret: str, timeout: float = 10.0) -> None:
        self._app_id = app_id
        self._secret = app_secret
        self._timeout = timeout

    async def _access_token(self) -> str:
        cached = self._token_cache.get(self._app_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{_API_BASE}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self._app_id,
                    "secret": self._secret,
                },
            )
        data: dict[str, Any] = response.json()
        token = data.get("access_token")
        if not token:
            # 不回显微信的错误详情：响应体可能带 appid/secret 相关线索。
            raise HTTPException(503, detail="公众号访问令牌获取失败")
        expires_in = int(data.get("expires_in", 7200))
        self._token_cache[self._app_id] = (str(token), time.time() + expires_in)
        return str(token)

    async def fetch_fans(self) -> list[WechatFan]:
        token = await self._access_token()
        fans: list[WechatFan] = []
        next_openid = ""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while True:
                response = await client.get(
                    f"{_API_BASE}/cgi-bin/user/get",
                    params={"access_token": token, "next_openid": next_openid},
                )
                payload = response.json()
                if payload.get("errcode"):
                    raise HTTPException(503, detail="公众号粉丝列表拉取失败")
                block = payload.get("data") or {}
                for openid in block.get("openid") or ():
                    fans.append(WechatFan(openid=str(openid)))
                next_openid = str(payload.get("next_openid") or "")
                if not next_openid or len(fans) >= int(payload.get("total") or 0):
                    break
            # 逐个补昵称 / 关注时间（微信未提供批量详情接口）。
            for index, fan in enumerate(fans):
                info = await client.get(
                    f"{_API_BASE}/cgi-bin/user/info",
                    params={"access_token": token, "openid": fan.openid, "lang": "zh_CN"},
                )
                detail = info.json()
                if detail.get("errcode"):
                    continue
                fans[index] = WechatFan(
                    openid=fan.openid,
                    unionid=detail.get("unionid"),
                    nickname=detail.get("nickname"),
                    avatar_url=detail.get("headimgurl"),
                    gender=int(detail.get("sex") or 0),
                    city=detail.get("city"),
                    province=detail.get("province"),
                    subscribe_status=int(detail.get("subscribe") or 0),
                )
        return fans

    async def publish_menu(self, menu: dict) -> None:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{_API_BASE}/cgi-bin/menu/create", params={"access_token": token}, json=menu
            )
        payload = response.json()
        if payload.get("errcode"):
            raise HTTPException(503, detail="公众号菜单发布失败")

    async def fetch_templates(self) -> list[WechatTemplate]:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{_API_BASE}/cgi-bin/template/get_all_private_template",
                params={"access_token": token},
            )
        payload = response.json()
        if payload.get("errcode"):
            raise HTTPException(503, detail="公众号模板消息拉取失败")
        return [
            WechatTemplate(
                template_id=str(item.get("template_id")),
                title=str(item.get("title") or ""),
                primary_industry=item.get("primary_industry"),
                deputy_industry=item.get("deputy_industry"),
                content=item.get("content"),
            )
            for item in payload.get("template_list") or ()
            if item.get("template_id")
        ]

    async def send_template(
        self, openid: str, template_id: str, data: dict[str, str], url: str | None
    ) -> str:
        token = await self._access_token()
        body: dict[str, Any] = {
            "touser": openid,
            "template_id": template_id,
            "data": {key: {"value": value} for key, value in data.items()},
        }
        if url:
            body["url"] = url
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{_API_BASE}/cgi-bin/message/template/send",
                params={"access_token": token},
                json=body,
            )
        payload = response.json()
        if payload.get("errcode"):
            raise HTTPException(503, detail="公众号模板消息发送失败")
        return str(payload.get("msgid") or "")

    async def broadcast(self, openids: list[str], content: str, url: str | None) -> str | None:
        # 图文群发按标签/全体投递；按 openid 精准投递走客服文本消息，
        # 保证「选定粉丝」语义与发送记录可对账。
        token = await self._access_token()
        last_msg_id: str | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for openid in openids:
                body: dict[str, Any] = {
                    "touser": openid,
                    "msgtype": "text",
                    "text": {"content": content},
                }
                response = await client.post(
                    f"{_API_BASE}/cgi-bin/message/custom/send",
                    params={"access_token": token},
                    json=body,
                )
                payload = response.json()
                if payload.get("errcode"):
                    raise HTTPException(503, detail="公众号消息发送失败")
                last_msg_id = str(payload.get("msgid") or last_msg_id or "")
        return last_msg_id


    async def fetch_miniprogram_code(self, path: str, scene: str) -> bytes:
        """调用 ``wxa/getwxacodeunlimit`` 生成小程序码；失败 503。"""
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{_API_BASE}/wxa/getwxacodeunlimit",
                params={"access_token": token},
                json={"scene": scene, "page": path, "check_path": False},
            )
        # 成功时直接返回图片二进制；失败时是 JSON（含 errcode）。
        if "image" not in response.headers.get("content-type", ""):
            raise HTTPException(503, detail="小程序码生成失败")
        return response.content

    async def create_qrcode(self, scene_str: str) -> str:
        """调用 ``cgi-bin/qrcode/create`` 创建带参二维码（永久字符场景值）。

        返回 ``showqrcode`` 换图地址，前端可直接 <img> 展示。
        """
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{_API_BASE}/cgi-bin/qrcode/create",
                params={"access_token": token},
                json={
                    "action_name": "QR_STR_SCENE",
                    "action_info": {"scene": {"scene_str": scene_str}},
                },
            )
        payload = response.json()
        ticket = payload.get("ticket")
        if payload.get("errcode") or not ticket:
            raise HTTPException(503, detail="公众号二维码生成失败")
        return f"https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket={ticket}"


def ensure_wechat_mp_configured() -> None:
    """写库前的显式可用性检查：凭据缺失时直接 503，不留「已同步」假记录。"""
    if get_wechat_mp_provider().name == "disabled":
        raise HTTPException(status_code=503, detail="公众号凭据未配置")


def get_wechat_mp_provider() -> WechatMpProvider:
    """按 ``wechat_mp_app_id`` / ``wechat_mp_app_secret`` 是否齐备返回供应商。"""
    if settings.wechat_mp_app_id and settings.wechat_mp_app_secret is not None:
        secret = settings.wechat_mp_app_secret.get_secret_value()
        if secret:
            return HttpWechatMpProvider(settings.wechat_mp_app_id, secret)
    if settings.is_test_mode:
        return MockWechatMpProvider()
    return DisabledWechatMpProvider()


def provider_status() -> dict[str, object]:
    """凭据就绪情况（只回显布尔，密钥绝不外泄）。"""
    secret_ready = (
        settings.wechat_mp_app_secret is not None
        and bool(settings.wechat_mp_app_secret.get_secret_value())
    )
    return {
        "app_id_configured": bool(settings.wechat_mp_app_id),
        "app_secret_configured": secret_ready,
        "token_configured": settings.wechat_mp_token is not None,
        "aes_key_configured": bool(settings.wechat_mp_aes_key),
        "configured": bool(settings.wechat_mp_app_id) and secret_ready,
    }

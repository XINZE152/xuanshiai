"""微信官方内容安全检测：msgSecCheck（文本）与 imgSecCheck（图片）。

设计要点（对齐 docs/内容安全-微信图片违规检测接入方案-2026-11-18.md）：

- 默认关闭（``WECHAT_SEC_CHECK_ENABLED=false``），关闭时一律 allow，绝不改变既有审核链路。
- 复用 ``auth.get_wechat_access_token`` 的带缓存全局 token，不新建凭据体系。
- 三态结果：allow / reject / manual_review，直接映射到既有 action 语义。
- 服务异常（超时/网络/协议错）按 ``WECHAT_SEC_CHECK_FAIL_MODE`` 兜底，默认转人工，
  不因第三方抖动阻断用户发布。
- 图片送检前必须归一为 JPEG/PNG（微信不支持 webp）、缩放并压到 1MB 内；
  该归一只用于送检，不影响落盘的 webp 原图。
- Mock 模式（``WECHAT_SEC_CHECK_MOCK`` / wechat_provider=mock）便于本地与单测覆盖，
  用固定关键字/字节标记触发三态，不产生真实微信调用。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

_WECHAT_MSG_SEC_CHECK = "https://api.weixin.qq.com/wxa/msg_sec_check"
_WECHAT_IMG_SEC_CHECK = "https://api.weixin.qq.com/wxa/img_sec_check"

# 微信内容安全返回的违规 errcode（内容含违规信息）
_ERRCODE_RISKY = 87014
# 文本检测 scene：1 资料 / 2 评论 / 3 论坛 / 4 社交日志
VALID_SCENES = frozenset({1, 2, 3, 4})

# Mock 模式：文本包含该前缀判违规；图片字节含该标记判违规；含另一标记判人工。
_MOCK_REJECT_TEXT = "mock-wechat-risky"
_MOCK_REVIEW_TEXT = "mock-wechat-review"
_MOCK_REJECT_IMAGE_MARK = b"MOCK_WECHAT_RISKY_IMAGE"
_MOCK_REVIEW_IMAGE_MARK = b"MOCK_WECHAT_REVIEW_IMAGE"


@dataclass(frozen=True)
class SecCheckDecision:
    """归一化的内容检测结果。action: allow / reject / manual_review。"""

    action: str
    risk_level: int = 0
    label: int | None = None
    provider: str = "wechat"
    raw_errcode: int | None = None
    error: str | None = None


def _enabled() -> bool:
    return bool(settings.wechat_sec_check_enabled)


def _mock_mode() -> bool:
    return bool(settings.wechat_sec_check_mock) or settings.wechat_provider.lower() == "mock"


def _failure_decision(provider: str, reason: str, *, errcode: int | None = None) -> SecCheckDecision:
    if settings.wechat_sec_check_fail_mode == "reject":
        return SecCheckDecision("reject", risk_level=3, provider=provider, raw_errcode=errcode, error=reason)
    return SecCheckDecision("manual_review", risk_level=3, provider=provider, raw_errcode=errcode, error=reason)


async def _get_access_token() -> str:
    """复用 auth 模块的带缓存 token；延迟导入避免模块级循环依赖。"""
    from app.services.auth import get_wechat_access_token

    return await get_wechat_access_token()


def _normalize_scene(scene: int) -> int:
    return scene if scene in VALID_SCENES else 1


# ---------------------------------------------------------------------------
# 文本：msgSecCheck
# ---------------------------------------------------------------------------


def _classify_text(payload: dict[str, Any]) -> SecCheckDecision:
    errcode = payload.get("errcode")
    try:
        errcode_int = int(errcode) if errcode is not None else 0
    except (TypeError, ValueError):
        return _failure_decision("wechat_msg", "内容安全响应 errcode 非法")

    if errcode_int == 0:
        result = payload.get("result") or {}
        suggestion = ""
        label = None
        if isinstance(result, dict):
            suggestion = str(result.get("suggest") or "").lower()
            raw_label = result.get("label")
            try:
                label = int(raw_label) if raw_label is not None else None
            except (TypeError, ValueError):
                label = None
        # 新版接口 errcode=0 即通过；suggest 仅作参考，只有明确的 risk/risky 才拦截，
        # review 交给人工，别名 pass/normal 一律放行，避免误伤。
        if suggestion in {"risk", "risky"}:
            return SecCheckDecision("reject", risk_level=2, label=label, provider="wechat_msg", raw_errcode=errcode_int)
        if suggestion in {"review"}:
            return SecCheckDecision("manual_review", risk_level=1, label=label, provider="wechat_msg", raw_errcode=errcode_int)
        return SecCheckDecision("allow", provider="wechat_msg", label=label, raw_errcode=errcode_int)

    if errcode_int == _ERRCODE_RISKY:
        return SecCheckDecision("reject", risk_level=2, provider="wechat_msg", raw_errcode=errcode_int)
    # 40001/40002 等凭证问题与 44992 额度耗尽等，属于服务异常 → 兜底。
    return _failure_decision("wechat_msg", "内容安全服务返回异常", errcode=errcode_int)


async def check_text(
    content: str | None,
    *,
    scene: int = 1,
    openid: str | None = None,
) -> SecCheckDecision:
    """检测文本是否违规。未启用或空内容直接 allow。"""
    value = (content or "").strip()
    if not _enabled() or not value:
        return SecCheckDecision("allow")

    if _mock_mode():
        if _MOCK_REJECT_TEXT in value:
            return SecCheckDecision("reject", risk_level=2, provider="wechat_msg_mock")
        if _MOCK_REVIEW_TEXT in value:
            return SecCheckDecision("manual_review", risk_level=1, provider="wechat_msg_mock")
        return SecCheckDecision("allow", provider="wechat_msg_mock")

    token = await _get_access_token()
    payload: dict[str, Any] = {
        "content": value,
        "scene": _normalize_scene(scene),
        "version": 2,
    }
    # openid 在资料/社交场景强烈建议提供，缺失时微信仍可检测但召回略降。
    if openid:
        payload["openid"] = openid

    last_error = "内容安全服务调用失败"
    for attempt in range(settings.wechat_sec_check_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.wechat_sec_check_timeout_seconds) as client:
                response = await client.post(
                    _WECHAT_MSG_SEC_CHECK,
                    params={"access_token": token},
                    json=payload,
                )
                data = response.json()
            return _classify_text(data)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            last_error = f"内容安全服务调用失败: {type(exc).__name__}"
            logger.warning("wechat sec check text failed attempt=%s type=%s", attempt + 1, type(exc).__name__)
    return _failure_decision("wechat_msg", last_error)


# ---------------------------------------------------------------------------
# 图片：imgSecCheck
# ---------------------------------------------------------------------------


def _normalize_image_for_check(data: bytes) -> bytes:
    """把图片转成微信可接受的 JPEG：缩放至上限内、体积压到 1MB 内。"""
    from PIL import Image, ImageOps, UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    try:
        with Image.open(BytesIO(data)) as source:
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (settings.wechat_sec_check_max_width, settings.wechat_sec_check_max_height),
                Image.Resampling.LANCZOS,
            )
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            quality = 85
            while quality >= 30:
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                result = output.getvalue()
                if len(result) <= settings.wechat_sec_check_max_bytes:
                    return result
                quality -= 10
            # 极端情况下继续降分辨率后再压一次，保证不超微信 1MB 限制。
            image.thumbnail((320, 320), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=60, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, DecompressionBombError) as exc:
        raise HTTPException(415, detail="图片内容无法识别") from exc


def _classify_image(payload: dict[str, Any]) -> SecCheckDecision:
    errcode = payload.get("errcode")
    try:
        errcode_int = int(errcode) if errcode is not None else 0
    except (TypeError, ValueError):
        return _failure_decision("wechat_img", "图片检测响应 errcode 非法")

    if errcode_int == 0:
        return SecCheckDecision("allow", provider="wechat_img", raw_errcode=errcode_int)
    if errcode_int == _ERRCODE_RISKY:
        label = payload.get("label") or payload.get("result", {}).get("label")
        try:
            label_int = int(label) if label is not None else None
        except (TypeError, ValueError):
            label_int = None
        return SecCheckDecision("reject", risk_level=2, label=label_int, provider="wechat_img", raw_errcode=errcode_int)
    return _failure_decision("wechat_img", "图片检测服务返回异常", errcode=errcode_int)


async def check_image(data: bytes, *, scene: int = 1) -> SecCheckDecision:
    """检测图片是否违规。未启用或空数据直接 allow。"""
    if not _enabled() or not data:
        return SecCheckDecision("allow")

    if _mock_mode():
        if _MOCK_REJECT_IMAGE_MARK in data:
            return SecCheckDecision("reject", risk_level=2, provider="wechat_img_mock")
        if _MOCK_REVIEW_IMAGE_MARK in data:
            return SecCheckDecision("manual_review", risk_level=1, provider="wechat_img_mock")
        return SecCheckDecision("allow", provider="wechat_img_mock")

    normalized = await _normalize_image_for_check_async(data)
    encoded = base64.b64encode(normalized).decode("ascii")
    token = await _get_access_token()
    payload: dict[str, Any] = {"image": encoded}
    if scene in VALID_SCENES:
        payload["scene"] = scene

    last_error = "图片检测服务调用失败"
    for attempt in range(settings.wechat_sec_check_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.wechat_sec_check_timeout_seconds) as client:
                response = await client.post(
                    _WECHAT_IMG_SEC_CHECK,
                    params={"access_token": token},
                    json=payload,
                )
                result = response.json()
            return _classify_image(result)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            last_error = f"图片检测服务调用失败: {type(exc).__name__}"
            logger.warning("wechat sec check image failed attempt=%s type=%s", attempt + 1, type(exc).__name__)
    return _failure_decision("wechat_img", last_error)


async def _normalize_image_for_check_async(data: bytes) -> bytes:
    import asyncio

    return await asyncio.to_thread(_normalize_image_for_check, data)

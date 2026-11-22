"""腾讯云短信真实供应商：TC3-HMAC-SHA256（由官方 SDK 完成签名）。

配置来自后台「短信签名配置」页，实时读取 sms_vendor 配置域；密钥服务端解密后仅内存使用。
模板 ID 不手工填写：根据模板内容自动在腾讯云查找/提交并缓存，内容取自通知配置与群发模板。
未配置完整时 fail-closed（503）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import session_factory
from app.services.sms.base import SmsProvider
from app.services.sms_vendor_config import get_vendor_config

_TEMPLATE_LOCK = asyncio.Lock()


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("0086"):
        digits = digits[4:]
    if len(digits) == 11 and digits.startswith("1"):
        return "+86" + digits
    if digits.startswith("+"):
        return "+" + digits[1:]
    return "+" + digits


async def _load_runtime_config() -> dict:
    if session_factory is None:
        raise HTTPException(status_code=503, detail="短信服务未配置")
    async with session_factory() as db:  # type: AsyncSession
        cfg = await get_vendor_config(db, reveal=True)
    # 真实短信常开：只要厂商参数齐全即发送，不再由后台开关控制。
    missing = [k for k in ("secret_id", "secret_key", "sdk_app_id", "sign_name") if not cfg.get(k)]
    if missing:
        raise HTTPException(status_code=503, detail="短信厂商参数未配置完整")
    return cfg


def _client(cfg: dict):
    from tencentcloud.common import credential
    from tencentcloud.sms.v20210111 import sms_client

    cred = credential.Credential(str(cfg["secret_id"]), str(cfg["secret_key"]))
    return sms_client.SmsClient(cred, str(cfg.get("region") or "ap-beijing"))


def _content_key(content: str) -> str:
    normalized = "".join(str(content).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


async def _read_config_json(namespace: str) -> dict:
    if session_factory is None:
        return {}
    async with session_factory() as db:  # type: AsyncSession
        row = (
            await db.execute(
                text("SELECT config_json FROM admin_config_snapshot WHERE namespace=:ns"),
                {"ns": namespace},
            )
        ).mappings().first()
    if not row:
        return {}
    try:
        value = json.loads(row["config_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


async def _write_template_map(template_map: dict) -> None:
    if session_factory is None:
        return
    async with session_factory() as db:  # type: AsyncSession
        row = (
            await db.execute(
                text("SELECT config_json FROM admin_config_snapshot WHERE namespace=:ns"),
                {"ns": "sms_vendor"},
            )
        ).mappings().first()
        if not row:
            return
        try:
            cfg = json.loads(row["config_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["template_map"] = template_map
        await db.execute(
            text("UPDATE admin_config_snapshot SET config_json=:cfg WHERE namespace=:ns"),
            {"cfg": json.dumps(cfg, ensure_ascii=False), "ns": "sms_vendor"},
        )
        await db.commit()


async def _resolve_template_id(cfg: dict, content: str) -> str:
    """按模板内容获取腾讯云 TemplateId：命中缓存 → 云端已审核模板匹配 → 自动提交新模板。"""
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.sms.v20210111 import models

    content = str(content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="短信模板内容为空")
    key = _content_key(content)
    template_map = dict(cfg.get("template_map") or {})
    if key in template_map:
        return str(template_map[key])

    async with _TEMPLATE_LOCK:
        # 双检：并发等待期间可能已被其它请求写入
        latest = await _read_config_json("sms_vendor")
        template_map = dict(latest.get("template_map") or {})
        if key in template_map:
            cfg["template_map"] = template_map
            return str(template_map[key])

        client = _client(cfg)
        normalized = "".join(content.split())

        # 1) 已在腾讯云登记的模板：按内容精确匹配，直接复用（含审核通过的同文模板）
        try:
            listed = await asyncio.to_thread(client.DescribeSmsTemplateList, models.DescribeSmsTemplateListRequest())
            for item in getattr(listed, "DescribeTemplateStatusSet", None) or []:
                remote_content = getattr(item, "TemplateContent", "") or ""
                if "".join(str(remote_content).split()) == normalized:
                    tid = str(getattr(item, "TemplateId", "") or "")
                    if tid:
                        template_map[key] = tid
                        cfg["template_map"] = template_map
                        await _write_template_map(template_map)
                        return tid
        except TencentCloudSDKException:
            pass  # 列表查询失败不阻断，继续尝试提交

        # 2) 内容未在腾讯云登记：自动提交模板，缓存返回的 TemplateId
        req = models.AddSmsTemplateRequest()
        req.TemplateName = ("短信-" + content)[:48]
        req.TemplateContent = content
        req.SmsType = 1  # 通知类
        req.International = 0
        req.Remark = "平台短信配置自动生成"
        try:
            resp = await asyncio.to_thread(client.AddSmsTemplate, req)
        except TencentCloudSDKException as exc:
            raise HTTPException(status_code=502, detail=f"短信模板自动提交失败：{getattr(exc, 'get_message', lambda: str(exc))()}")
        status = getattr(resp, "AddTemplateStatus", None)
        tid = str(getattr(status, "TemplateId", "") or "") if status else ""
        if not tid:
            raise HTTPException(status_code=502, detail="短信模板提交成功但未返回模板ID")
        template_map[key] = tid
        cfg["template_map"] = template_map
        await _write_template_map(template_map)
        return tid


async def _verification_content() -> str:
    """验证码模板内容取自通知配置中的「全平台验证码」场景。"""
    cfg = await _read_config_json("sys_sms")
    notices = cfg.get("notices") or []
    if isinstance(notices, list):
        for item in notices:
            if isinstance(item, dict) and int(item.get("id") or 0) == 1:
                content = str(item.get("content") or "").strip()
                if content:
                    return content
    return "验证码为：{1}，5分钟内有效"


async def _send(cfg: dict, phone: str, content: str, params: list[str]) -> str:
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.sms.v20210111 import models

    template_id = await _resolve_template_id(cfg, content)
    req = models.SendSmsRequest()
    req.SmsSdkAppId = str(cfg["sdk_app_id"])
    req.SignName = str(cfg["sign_name"])
    req.TemplateId = template_id
    req.PhoneNumberSet = [_normalize_phone(phone)]
    req.TemplateParamSet = [str(p) for p in params]
    try:
        client = _client(cfg)
        # 官方 SDK 为同步 HTTP 客户端，放到线程池避免阻塞事件循环。
        resp = await asyncio.to_thread(client.SendSms, req)
    except TencentCloudSDKException as exc:
        raise HTTPException(status_code=502, detail=f"短信厂商返回错误：{getattr(exc, 'get_message', lambda: str(exc))()}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="短信厂商请求失败") from exc
    statuses = getattr(resp, "SendStatusSet", None) or []
    if not statuses:
        raise HTTPException(status_code=502, detail="短信厂商未返回发送状态")
    first = statuses[0]
    code = getattr(first, "Code", "")
    if code != "Ok":
        raise HTTPException(status_code=502, detail=f"短信发送失败：{code} {getattr(first, 'Message', '')}".strip())
    return str(getattr(first, "SerialNo", "") or "")


class TencentCloudSmsProvider(SmsProvider):
    name = "tencent_cloud"

    async def send_code(self, phone: str, purpose: str, code: str) -> None:
        """登录验证码：模板内容取自通知配置的验证码场景，变量为验证码。"""
        cfg = await _load_runtime_config()
        content = await _verification_content()
        await _send(cfg, phone, content, [code])

    async def send_template(
        self, phone: str, template_code: str, params: dict[str, str]
    ) -> str | None:
        """运营群发：template_code 即模板内容；变量按 {1}、{2} 顺序填入。"""
        cfg = await _load_runtime_config()
        content = str(template_code or "").strip()
        if not content:
            raise HTTPException(status_code=422, detail="短信模板内容为空")
        values = [str(params[key]) for key in sorted(params.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x))] if params else []
        return await _send(cfg, phone, content, values)

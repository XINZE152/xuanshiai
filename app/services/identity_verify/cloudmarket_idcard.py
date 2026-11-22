"""腾讯云市场身份证二要素核验（VerifyIdcardv2）provider。

签名：HMAC-SHA1("x-date: <GMT>", secretKey) -> base64，放入 Authorization 头；
响应：error_code==0 且 result.isok==true 表示一致。
配置来自 admin 后台「实名配置」页，凭据实时从 DB 读取并解密。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urlencode

from fastapi import HTTPException

from app.db.session import session_factory
from app.services.identity_verify.base import IdentityProvider, IdentityVerifyRequest, IdentityVerifyResult
from app.services.identity_config import get_identity_config


async def _runtime_config() -> dict:
    if session_factory is None:
        raise HTTPException(status_code=503, detail="实名核验服务未配置")
    async with session_factory() as db:
        cfg = await get_identity_config(db, reveal=True)
    missing = [k for k in ("secret_id", "secret_key") if not cfg.get(k)]
    if missing:
        raise HTTPException(status_code=503, detail="实名核验厂商参数未配置完整")
    if not (cfg.get("base_url") or "").strip():
        raise HTTPException(status_code=503, detail="实名核验接口地址未配置")
    return cfg


class CloudMarketIdentityProvider(IdentityProvider):
    """云市场身份证二要素核验：姓名 + 身份证号。"""

    name = "cloud_market_idcard"

    async def verify(self, request: IdentityVerifyRequest) -> IdentityVerifyResult:
        cfg = await _runtime_config()
        x_date = formatdate(usegmt=True)
        sign_str = "x-date: %s" % x_date
        signature = base64.b64encode(
            hmac.new(
                str(cfg["secret_key"]).encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("ascii")
        auth = json.dumps(
            {"id": str(cfg["secret_id"]), "x-date": x_date, "signature": signature},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        params = {"cardNo": request.id_card_number, "realName": request.real_name}
        url = str(cfg["base_url"]).strip() + "?" + urlencode(params)
        headers = {
            "request-id": str(uuid.uuid1()),
            "Authorization": auth,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }
        timeout = float(cfg.get("timeout_seconds") or 10)

        def do_request() -> bytes:
            from urllib.request import Request, urlopen

            req = Request(url, headers=headers)
            req.get_method = lambda: "GET"
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()

        try:
            content = await asyncio.to_thread(do_request)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="实名核验服务暂时不可用") from exc

        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail="实名核验返回数据异常") from exc

        error_code = data.get("error_code")
        try:
            error_code = int(error_code)
        except (TypeError, ValueError):
            error_code = -1
        if error_code != 0:
            # 206501 = 库无；其余为网关联调/业务错误，统一按未通过处理并给安全原因码
            reason = "identity_not_found" if error_code == 206501 else "identity_provider_error"
            return IdentityVerifyResult(verified=False, provider=self.name, reason=reason)
        result = data.get("result") or {}
        verified = bool(result.get("isok")) if isinstance(result, dict) else False
        return IdentityVerifyResult(
            verified=verified,
            provider=self.name,
            reason=None if verified else "identity_mismatch",
            raw={"error_code": error_code},
        )

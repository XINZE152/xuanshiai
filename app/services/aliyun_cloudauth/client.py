"""Minimal Alibaba Cloud CloudAuth RPC client.

使用阿里云 RPC 风格签名（HMAC-SHA1），避免引入整套云 SDK；请求与签名参数
按官方规则构造。日志和异常只保留错误码/RequestId，不记录姓名、身份证号、
人脸 Base64 或原始响应体。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx


class AliyunCloudAuthError(Exception):
    """阿里云返回业务错误；``code`` 可安全用于日志。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.safe_message = (message or code)[:200]


class AliyunCloudAuthUnavailable(AliyunCloudAuthError):
    """网络、超时或无法解析响应；调用方应降级人工/503。"""


def _percent_encode(value: object) -> str:
    return quote(str(value), safe="~")


def _canonical_query(params: dict[str, object]) -> str:
    return "&".join(
        f"{_percent_encode(key)}={_percent_encode(params[key])}"
        for key in sorted(params)
    )


def _sign(secret: str, canonical_query: str) -> str:
    string_to_sign = f"POST&%2F&{_percent_encode(canonical_query)}"
    digest = hmac.new(
        f"{secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def result_object(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``ResultObject`` as a dict; some RPC responses serialize it as JSON text."""
    value = data.get("ResultObject")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


class AliyunCloudAuthClient:
    """CloudAuth 2019-03-07 RPC 调用器。"""

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str = "cloudauth.aliyuncs.com",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not access_key_id.strip() or not access_key_secret.strip():
            raise AliyunCloudAuthError("ALIYUN_CREDENTIALS_MISSING")
        self._access_key_id = access_key_id.strip()
        self._access_key_secret = access_key_secret.strip()
        self._endpoint = endpoint.strip() or "cloudauth.aliyuncs.com"
        self._timeout = timeout_seconds
        self._http_client = http_client

    async def call(self, action: str, params: dict[str, object]) -> dict[str, Any]:
        common: dict[str, object] = {
            "AccessKeyId": self._access_key_id,
            "Action": action,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2019-03-07",
        }
        payload = {**common, **params}
        canonical = _canonical_query(payload)
        signature = _sign(self._access_key_secret, canonical)
        body = f"{canonical}&Signature={_percent_encode(signature)}"

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                f"https://{self._endpoint}/",
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise AliyunCloudAuthUnavailable("ALIYUN_HTTP_ERROR") from exc
        except ValueError as exc:
            raise AliyunCloudAuthUnavailable("ALIYUN_BAD_RESPONSE") from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(data, dict):
            raise AliyunCloudAuthUnavailable("ALIYUN_BAD_RESPONSE")
        code = str(data.get("Code") or "")
        if code != "200":
            request_id = str(data.get("RequestId") or "")
            raise AliyunCloudAuthError(
                code or "ALIYUN_ERROR",
                f"request_id={request_id}" if request_id else "",
            )
        return data

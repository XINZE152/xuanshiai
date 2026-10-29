"""微信支付 APIv3 HTTP 客户端。

职责边界：
- 只负责「与微信通信 + 验签/解密 + 错误映射」；
- 不写业务库、不 commit 事务；
- 业务订单状态由 :class:`app.services.payment.orders.PaymentOrderService` 负责。

``WechatPayV3Client`` 实现第一轮 :class:`PaymentGateway` Protocol；旧版
``app/services/membership.py`` / ``app/services/payments.py`` 保持不变，通过
``WECHAT_PAY_MODE`` 灰度切换。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.payment.base import (
    JsapiPrepay,
    PaymentAPIError,
    PaymentConfigError,
    PaymentGateway,
    PaymentNotification,
    PaymentSignatureError,
    PaymentValidationError,
    RefundResult,
    WechatPayConfig,
)
from app.services.payment.crypto import WechatPayCrypto

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaymentRuntime:
    """一次请求内复用的网关 + 加密工具。

    ``gateway`` 持有 ``httpx.AsyncClient``，调用方必须在 ``finally`` 中
    ``await runtime.gateway.aclose()``，避免连接池泄漏。
    """

    gateway: "WechatPayV3Client"
    crypto: WechatPayCrypto


def build_wechat_pay_config() -> WechatPayConfig:
    """从 ``settings.wechat_pay_*`` 构造网关配置。

    只在 ``WECHAT_PAY_MODE=real`` 时允许构造；mock/disabled 下调用方应直接
    返回 503，避免误把测试环境当真实支付。
    """
    if settings.wechat_pay_mode != "real":
        raise PaymentConfigError(
            "微信支付网关未启用：WECHAT_PAY_MODE 必须为 real"
        )

    private_key_pem = settings.wechat_pay_private_key_pem()
    api_v3_key = (
        settings.wechat_pay_api_v3_key.get_secret_value().strip()
        if settings.wechat_pay_api_v3_key
        else ""
    )
    if not private_key_pem:
        raise PaymentConfigError("微信支付商户私钥未配置")
    if not settings.wechat_pay_mch_id or not settings.wechat_pay_app_id:
        raise PaymentConfigError("微信支付商户号或 AppID 未配置")
    if not settings.wechat_pay_serial_no:
        raise PaymentConfigError("微信支付商户证书序列号未配置")
    if not settings.wechat_pay_notify_url:
        raise PaymentConfigError("微信支付回调地址未配置")

    return WechatPayConfig(
        mch_id=settings.wechat_pay_mch_id,
        app_id=settings.wechat_pay_app_id,
        serial_no=settings.wechat_pay_serial_no,
        private_key_pem=private_key_pem,
        api_v3_key=api_v3_key,
        notify_url=settings.wechat_pay_notify_url,
        refund_notify_url=settings.wechat_pay_refund_notify_url,
        public_key_id=settings.wechat_pay_public_key_id,
        public_key_pem=settings.wechat_pay_public_key_pem(),
        base_url=settings.wechat_pay_base_url,
        timeout_seconds=settings.wechat_pay_timeout_seconds,
        max_clock_skew_seconds=settings.wechat_pay_max_clock_skew_seconds,
    )


def build_payment_crypto() -> WechatPayCrypto:
    """构造回调验签/解密所需的加密工具（不创建 HTTP 连接）。"""
    return WechatPayCrypto(build_wechat_pay_config())


def build_payment_runtime() -> PaymentRuntime:
    """构造预支付所需的网关运行时。"""
    crypto = build_payment_crypto()
    gateway = WechatPayV3Client(crypto.config, crypto)
    return PaymentRuntime(gateway=gateway, crypto=crypto)


def parse_wechat_notification(
    crypto: WechatPayCrypto,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> PaymentNotification:
    """验签、解密并解析微信支付/退款通知。

    该方法只做密码学和报文解析，不写数据库；调用方负责把结果写入
    ``payment_notify_log`` 并执行幂等履约。
    """
    crypto.verify_notification(headers, raw_body)

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentValidationError("微信支付通知不是合法 JSON") from exc
    if not isinstance(event, dict):
        raise PaymentValidationError("微信支付通知顶层必须是对象")

    notify_id = str(event.get("id") or "").strip()
    event_type = str(event.get("event_type") or "").strip()
    if not notify_id:
        raise PaymentValidationError("微信支付通知缺少 id")
    if not event_type:
        raise PaymentValidationError("微信支付通知缺少 event_type")

    resource = event.get("resource")
    if not isinstance(resource, dict):
        raise PaymentValidationError("微信支付通知缺少 resource")

    plain = crypto.decrypt_resource(resource)
    amount = plain.get("amount") if isinstance(plain.get("amount"), dict) else {}
    payer = plain.get("payer") if isinstance(plain.get("payer"), dict) else {}

    return PaymentNotification(
        notify_id=notify_id,
        event_type=event_type,
        out_trade_no=_optional_str(plain.get("out_trade_no")),
        transaction_id=_optional_str(plain.get("transaction_id")),
        refund_no=_optional_str(plain.get("out_refund_no")),
        refund_id=_optional_str(plain.get("refund_id")),
        trade_state=_optional_str(plain.get("trade_state")),
        refund_status=_optional_str(plain.get("refund_status")),
        amount_total=_optional_int(amount.get("total")),
        refund_amount=_optional_int(amount.get("refund")),
        currency=_optional_str(amount.get("currency")),
        payer_openid=_optional_str(payer.get("openid")),
        mchid=_optional_str(plain.get("mchid")),
        appid=_optional_str(plain.get("appid")),
        success_time=_optional_str(plain.get("success_time")),
        # 只保留解析后的业务明文，不把原始密文重复塞进内存。
        raw={"event": event, "resource": plain},
    )


class WechatPayV3Client(PaymentGateway):
    """微信支付 APIv3 客户端。

    参数 ``http_client`` 仅用于测试注入 ``httpx.MockTransport``；生产环境不传，
    由客户端自己创建并负责关闭连接池。
    """

    def __init__(
        self,
        config: WechatPayConfig,
        crypto: WechatPayCrypto,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.crypto = crypto
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=min(3.0, config.timeout_seconds),
                read=config.timeout_seconds,
                write=config.timeout_seconds,
                pool=min(3.0, config.timeout_seconds),
            )
        )

    async def aclose(self) -> None:
        """关闭自建连接池；注入的 client 由调用方管理。"""
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # PaymentGateway 实现
    # ------------------------------------------------------------------
    async def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        description: str,
        amount_cents: int,
        openid: str,
        notify_url: str,
    ) -> JsapiPrepay:
        """调用 ``/v3/pay/transactions/jsapi`` 获取 ``prepay_id``。"""
        if amount_cents <= 0:
            raise PaymentValidationError("支付金额必须大于 0")
        if not openid:
            raise PaymentValidationError("缺少付款人 openid")
        if not notify_url:
            raise PaymentValidationError("缺少支付回调地址")

        data = await self._request(
            "POST",
            "/v3/pay/transactions/jsapi",
            {
                "appid": self.config.app_id,
                "mchid": self.config.mch_id,
                "description": description[:127],
                "out_trade_no": out_trade_no,
                "notify_url": notify_url,
                "amount": {"total": amount_cents, "currency": "CNY"},
                "payer": {"openid": openid},
            },
        )
        prepay_id = str(data.get("prepay_id") or "").strip()
        if not prepay_id:
            raise PaymentAPIError(502, "微信支付未返回 prepay_id", payload=data)
        return JsapiPrepay(prepay_id=prepay_id)

    async def query_order(self, out_trade_no: str) -> dict[str, Any]:
        """按商户订单号查询微信订单。"""
        path = (
            f"/v3/pay/transactions/out-trade-no/{quote(out_trade_no, safe='')}"
            f"?mchid={quote(self.config.mch_id, safe='')}"
        )
        return await self._request("GET", path, retryable=True)

    async def close_order(self, out_trade_no: str) -> None:
        """关闭未支付订单。"""
        path = (
            f"/v3/pay/transactions/out-trade-no/{quote(out_trade_no, safe='')}/close"
        )
        await self._request("POST", path, {"mchid": self.config.mch_id})

    async def create_refund(
        self,
        *,
        out_refund_no: str,
        out_trade_no: str,
        refund_cents: int,
        total_cents: int,
        reason: str,
        notify_url: str,
    ) -> RefundResult:
        """调用 ``/v3/refund/domestic/refunds`` 发起退款。"""
        if refund_cents <= 0 or total_cents <= 0:
            raise PaymentValidationError("退款金额与订单金额必须大于 0")
        if refund_cents > total_cents:
            raise PaymentValidationError("退款金额不能大于订单金额")

        data = await self._request(
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "out_trade_no": out_trade_no,
                "out_refund_no": out_refund_no,
                "reason": reason[:80],
                "notify_url": notify_url,
                "amount": {
                    "refund": refund_cents,
                    "total": total_cents,
                    "currency": "CNY",
                },
            },
        )
        return RefundResult(
            refund_no=out_refund_no,
            wechat_refund_id=_optional_str(data.get("refund_id")),
            status=str(data.get("status") or "PROCESSING"),
            raw=data,
        )

    async def query_refund(self, out_refund_no: str) -> RefundResult:
        """按商户退款单号查询退款结果。"""
        path = f"/v3/refund/domestic/refunds/{quote(out_refund_no, safe='')}"
        data = await self._request("GET", path, retryable=True)
        return RefundResult(
            refund_no=out_refund_no,
            wechat_refund_id=_optional_str(data.get("refund_id")),
            status=str(data.get("status") or "PROCESSING"),
            raw=data,
        )

    def parse_notification(
        self,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> PaymentNotification:
        """实现 Protocol：验签、解密并解析通知。"""
        return parse_wechat_notification(self.crypto, headers, raw_body)

    async def get_bill_download_url(
        self,
        bill_date: str,
        bill_type: str = "ALL",
    ) -> str:
        """获取微信交易账单下载链接。

        :param bill_date: ``YYYY-MM-DD``，只能查询当天或更早的账单。
        :param bill_type: ``ALL`` / ``SUCCESS`` / ``REFUND``。
        """
        path = (
            "/v3/bill/tradebill"
            f"?bill_date={quote(bill_date, safe='')}"
            f"&bill_type={quote(bill_type, safe='')}"
        )
        data = await self._request("GET", path, retryable=True)
        url = str(data.get("download_url") or "").strip()
        if not url:
            raise PaymentAPIError(502, "微信账单未返回 download_url", payload=data)
        return url

    async def download_bill(self, download_url: str) -> bytes:
        """下载账单文件原始字节。

        账单下载链接是微信返回的临时签名 URL，不属于 APIv3 JSON 响应，
        因此不调用 ``crypto.verify_response``；调用方负责解压/解析。
        """
        if not download_url:
            raise PaymentValidationError("账单下载链接为空")
        try:
            response = await self._http.get(
                download_url,
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PaymentAPIError(504, "微信账单下载超时") from exc
        except httpx.RequestError as exc:
            raise PaymentAPIError(502, "微信账单下载网络异常") from exc
        if response.status_code >= 400:
            raise PaymentAPIError(
                response.status_code,
                f"微信账单下载失败 HTTP {response.status_code}",
            )
        return response.content

    async def refresh_platform_certs(self) -> None:
        """拉取平台证书并安装到 ``WechatPayCrypto`` 内存缓存。

        公钥模式下无需调用；调用方可在启动或遇到未知 ``Wechatpay-Serial``
        时触发一次。
        """
        data = await self._request("GET", "/v3/certificates", retryable=True)
        self.crypto.install_platform_certificates(data)

    # ------------------------------------------------------------------
    # HTTP 基础设施
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        """发送 APIv3 请求。

        - 签名和发送使用**同一份序列化字节**；
        - 响应体验签；
        - 网络超时/连接错误映射为 :class:`PaymentAPIError`；
        - GET 可重试，POST 不盲目重试（由业务用同一业务单号补偿）。
        """
        body_bytes = (
            b""
            if body is None
            else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        headers = {
            "Authorization": self.crypto.build_authorization(method, path, body_bytes),
            "Accept": "application/json",
            "User-Agent": "xuanshiai-payment/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"

        url = self.config.base_url.rstrip("/") + path
        attempts = 2 if retryable else 1
        last_error: PaymentAPIError | None = None

        for attempt in range(attempts):
            try:
                response = await self._http.request(
                    method,
                    url,
                    content=body_bytes if body is not None else None,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                last_error = PaymentAPIError(504, "微信支付请求超时")
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise last_error from exc
            except httpx.RequestError as exc:
                last_error = PaymentAPIError(502, "微信支付网络异常")
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise last_error from exc

            # 微信 APIv3 的响应签名与通知同规范；204/空响应跳过。
            if response.content:
                try:
                    self.crypto.verify_response(response.headers, response.content)
                except PaymentSignatureError:
                    # 5xx 可能是网关错误页，没有微信签名；保留原始状态码映射。
                    if response.status_code < 400:
                        raise
                    logger.warning(
                        "wechat pay response signature invalid status=%s path=%s",
                        response.status_code,
                        path,
                    )

            data = _json_or_empty(response)
            if response.status_code >= 400:
                message = str(
                    data.get("message")
                    or f"微信支付接口错误 HTTP {response.status_code}"
                )
                error = PaymentAPIError(
                    response.status_code,
                    message,
                    code=_optional_str(data.get("code")),
                    payload=data,
                )
                if retryable and response.status_code >= 500 and attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise error

            return data

        raise last_error or PaymentAPIError(500, "微信支付请求失败")


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    """解析 JSON；非 JSON 错误页只保留截断文本，避免日志/异常过大。"""
    if not response.content:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {"raw": response.text[:500]}
    return value if isinstance(value, dict) else {"raw": value}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

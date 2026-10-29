"""微信支付网关的公共类型、协议与异常定义。

本模块只依赖标准库，不依赖 FastAPI/SQLAlchemy，方便被 API 层、Worker 和
单元测试共同复用。具体渠道实现放在 ``app/services/payment/wechat_v3.py``。

设计约束：
- 金额在内部统一使用「分」；数据库仍按项目既有 ``decimal(12,2)`` 存元，
  由调用方在边界处转换。
- 网关只负责「与微信通信 + 验签/解密」，不写业务库、不 commit 事务。
- 回调解析返回 :class:`PaymentNotification`，业务层再决定如何履约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class WechatPayConfig:
    """微信支付 APIv3 运行配置。

    所有字段都应来自环境变量/KMS，不得从数据库后台配置读取。
    ``private_key_pem`` 为商户 API 私钥 PEM 文本；生产环境建议通过只读文件
    挂载，由配置层读取后传入，避免私钥出现在进程参数中。
    """

    mch_id: str
    app_id: str
    serial_no: str
    private_key_pem: str
    api_v3_key: str
    notify_url: str
    refund_notify_url: str | None = None
    # 公钥模式（与平台证书模式二选一）。未配置时由客户端下载平台证书。
    public_key_id: str | None = None
    public_key_pem: str | None = None
    base_url: str = "https://api.mch.weixin.qq.com"
    timeout_seconds: float = 10.0
    max_clock_skew_seconds: int = 300

    def __post_init__(self) -> None:
        # APIv3 密钥固定 32 字节，长度不对时后续 AES-GCM 一定失败，提前 fail fast。
        if len(self.api_v3_key.encode("utf-8")) != 32:
            raise ValueError("微信支付 APIv3 密钥必须是 32 字节")
        if not self.mch_id.strip() or not self.app_id.strip():
            raise ValueError("微信支付商户号与 AppID 不能为空")
        if not self.serial_no.strip():
            raise ValueError("微信支付商户证书序列号不能为空")
        if not self.private_key_pem.strip():
            raise ValueError("微信支付商户私钥不能为空")


@dataclass(frozen=True, slots=True)
class JsapiPrepay:
    """微信 JSAPI 统一下单返回的预支付信息。"""

    prepay_id: str


@dataclass(frozen=True, slots=True)
class JsapiPayParams:
    """小程序 ``wx.requestPayment`` 所需参数。

    字段名使用小程序侧驼峰命名，便于直接透传给前端；服务端内部仍保持
    snake_case，避免把前端命名带到业务层。
    """

    app_id: str
    time_stamp: str
    nonce_str: str
    package: str
    sign_type: str
    pay_sign: str

    def as_dict(self) -> dict[str, str]:
        """转换为小程序 ``wx.requestPayment`` 直接可用的字典。"""
        return {
            "appId": self.app_id,
            "timeStamp": self.time_stamp,
            "nonceStr": self.nonce_str,
            "package": self.package,
            "signType": self.sign_type,
            "paySign": self.pay_sign,
        }


@dataclass(frozen=True, slots=True)
class PaymentNotification:
    """微信支付/退款通知解密后的统一结构。

    ``raw`` 保留解密后的完整 JSON，便于审计与排障；业务层只依赖上面的
    显式字段，避免直接读 ``raw`` 造成字段口径漂移。
    """

    notify_id: str
    event_type: str
    out_trade_no: str | None = None
    transaction_id: str | None = None
    refund_no: str | None = None
    refund_id: str | None = None
    trade_state: str | None = None
    refund_status: str | None = None
    amount_total: int | None = None
    refund_amount: int | None = None
    currency: str | None = None
    payer_openid: str | None = None
    mchid: str | None = None
    appid: str | None = None
    success_time: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefundResult:
    """微信退款接口/退款查询的统一返回。"""

    refund_no: str
    wechat_refund_id: str | None
    status: str
    refund_type: str = "FULL"
    order_no: str | None = None
    amount: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentGateway(Protocol):
    """支付网关协议。

    ``WechatPayV3Client`` 实现该协议；未来接入支付宝/余额渠道时只需新增实现，
    业务层不感知具体渠道。
    """

    async def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        description: str,
        amount_cents: int,
        openid: str,
        notify_url: str,
    ) -> JsapiPrepay:
        """调用统一下单，返回 ``prepay_id``。"""
        ...

    async def query_order(self, out_trade_no: str) -> dict[str, Any]:
        """按商户订单号查询微信订单。"""
        ...

    async def close_order(self, out_trade_no: str) -> None:
        """关闭未支付订单。"""
        ...

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
        """发起退款。"""
        ...

    async def query_refund(self, out_refund_no: str) -> RefundResult:
        """按商户退款单号查询退款结果。"""
        ...

    def parse_notification(
        self,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> PaymentNotification:
        """验签、解密并解析支付/退款通知。

        该方法必须是纯函数式校验：失败抛异常，不写数据库。
        """
        ...


class PaymentError(Exception):
    """支付域基础异常，便于 API 层统一映射为 4xx/5xx。"""


class PaymentConfigError(PaymentError):
    """配置缺失或格式错误。"""


class PaymentSignatureError(PaymentError):
    """微信支付签名/验签失败。"""


class PaymentUnknownSerialError(PaymentSignatureError):
    """通知/响应携带了本地未缓存的平台证书序列号。"""


class PaymentDecryptError(PaymentError):
    """回调 resource 解密失败。"""


class PaymentValidationError(PaymentError):
    """通知字段校验失败（商户号、金额、状态等）。"""


class PaymentAPIError(PaymentError):
    """微信支付接口返回非 2xx。"""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}

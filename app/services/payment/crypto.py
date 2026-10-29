"""微信支付 APIv3 签名、验签、解密与小程序 paySign。

本模块只处理密码学与报文规范，不负责 HTTP 请求和业务落库。

实现依据微信支付 APIv3 通用规范：
- 请求签名：``METHOD\\nURL\\nTIMESTAMP\\nNONCE\\nBODY\\n``，商户私钥 RSA-SHA256；
- 通知/响应验签：``TIMESTAMP\\nNONCE\\nBODY\\n``，微信支付平台证书/公钥 RSA-SHA256；
- 回调 resource：AES-256-GCM，APIv3 密钥 32 字节；
- 小程序 paySign：``appId\\ntimeStamp\\nnonceStr\\npackage\\n``，商户私钥 RSA-SHA256。

假设：项目通过 ``python-jose[cryptography]`` 间接安装了 ``cryptography``。
如果生产镜像裁剪依赖，请在 ``pyproject.toml`` 显式声明
``cryptography>=42,<44``，不要依赖传递依赖。
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from typing import Any, Mapping

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.payment.base import (
    PaymentConfigError,
    PaymentDecryptError,
    PaymentSignatureError,
    PaymentUnknownSerialError,
    WechatPayConfig,
)

logger = logging.getLogger(__name__)

# 微信支付 APIv3 回调 resource 的固定算法名。
_WECHAT_PAY_RESOURCE_ALGORITHM = "AEAD_AES_256_GCM"


class WechatPayCrypto:
    """微信支付 APIv3 密码学工具。

    线程/协程安全说明：
    - 商户私钥只读，可被多个请求并发使用；
    - 平台证书缓存只在刷新时整体替换，读路径无锁；
    - 生产环境建议在每次刷新前由调用方加分布式锁，避免多实例同时打证书接口。
    """

    def __init__(self, config: WechatPayConfig) -> None:
        self.config = config
        self._api_v3_key = config.api_v3_key.encode("utf-8")
        if len(self._api_v3_key) != 32:
            raise PaymentConfigError("微信支付 APIv3 密钥必须是 32 字节")

        try:
            self._private_key = serialization.load_pem_private_key(
                config.private_key_pem.encode("utf-8"),
                password=None,
            )
        except Exception as exc:  # noqa: BLE001 - 统一转换为支付配置异常
            raise PaymentConfigError("微信支付商户私钥格式无效") from exc

        # serial_no -> 平台证书；公钥模式则由 public_key_id -> 公钥。
        self._platform_certs: dict[str, x509.Certificate] = {}
        self._public_keys: dict[str, Any] = {}
        if config.public_key_id and config.public_key_pem:
            self.install_public_key(config.public_key_id, config.public_key_pem)

    # ------------------------------------------------------------------
    # 1. 请求签名
    # ------------------------------------------------------------------
    def build_authorization(self, method: str, url_path: str, body: bytes = b"") -> str:
        """生成 APIv3 ``Authorization`` 请求头。

        :param method: HTTP 方法，大小写不敏感。
        :param url_path: 带 query 的路径，例如 ``/v3/pay/transactions/jsapi``。
        :param body: **实际发送的原始字节**。调用方必须先序列化一次，签名和
            发送必须使用同一份字节，不能签一份、发另一份。
        """
        if not url_path.startswith("/"):
            raise ValueError("微信支付签名路径必须以 / 开头")

        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        body_text = body.decode("utf-8") if body else ""
        message = f"{method.upper()}\n{url_path}\n{timestamp}\n{nonce}\n{body_text}\n".encode(
            "utf-8"
        )
        signature = self._rsa_sign(message)
        return (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{self.config.mch_id}",'
            f'nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{timestamp}",serial_no="{self.config.serial_no}"'
        )

    # ------------------------------------------------------------------
    # 2. 通知/响应验签
    # ------------------------------------------------------------------
    def verify_notification(self, headers: Mapping[str, str], body: bytes) -> None:
        """校验微信支付通知或 API 响应签名。

        校验失败统一抛 :class:`PaymentSignatureError`；未知证书序列号抛
        :class:`PaymentUnknownSerialError`，调用方应刷新平台证书后重试一次。

        注意：这里只做密码学校验，不校验业务字段（金额、商户号、交易状态）。
        业务字段校验在 ``PaymentOrderService.mark_paid`` 中完成。
        """
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        timestamp = normalized.get("wechatpay-timestamp")
        nonce = normalized.get("wechatpay-nonce")
        signature = normalized.get("wechatpay-signature")
        serial = normalized.get("wechatpay-serial")

        if not all([timestamp, nonce, signature, serial]):
            raise PaymentSignatureError("微信支付签名头不完整")

        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise PaymentSignatureError("微信支付时间戳格式错误") from exc

        max_skew = int(self.config.max_clock_skew_seconds or 0)
        if max_skew > 0 and abs(int(time.time()) - timestamp_int) > max_skew:
            raise PaymentSignatureError("微信支付通知时间戳超出允许范围")

        message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n".encode("utf-8")
        verification_key = self._verification_key(serial)

        try:
            verification_key.verify(
                base64.b64decode(signature),
                message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise PaymentSignatureError("微信支付签名校验失败") from exc
        except Exception as exc:  # noqa: BLE001 - base64/密钥类型错误统一收敛
            raise PaymentSignatureError("微信支付签名校验异常") from exc

    def verify_response(self, headers: Mapping[str, str], body: bytes) -> None:
        """校验微信支付 API 响应签名。

        微信 APIv3 的响应签名头与通知相同，直接复用通知校验逻辑；单独命名
        是为了让调用方语义清晰。
        """
        self.verify_notification(headers, body)

    # ------------------------------------------------------------------
    # 3. resource 解密
    # ------------------------------------------------------------------
    def decrypt_resource_bytes(self, resource: Mapping[str, Any]) -> bytes:
        """解密 APIv3 ``resource`` 字段，返回原始明文字节。

        平台证书下载接口返回的 ``encrypt_certificate`` 也使用同一结构，因此
        该方法同时服务于「回调解密」和「证书解密」。
        """
        algorithm = str(resource.get("algorithm") or _WECHAT_PAY_RESOURCE_ALGORITHM)
        if algorithm != _WECHAT_PAY_RESOURCE_ALGORITHM:
            raise PaymentDecryptError(f"不支持的微信支付加密算法：{algorithm}")

        nonce = resource.get("nonce")
        ciphertext = resource.get("ciphertext")
        associated_data = resource.get("associated_data") or ""
        if not nonce or not ciphertext:
            raise PaymentDecryptError("微信支付 resource 缺少 nonce/ciphertext")

        try:
            aesgcm = AESGCM(self._api_v3_key)
            return aesgcm.decrypt(
                str(nonce).encode("utf-8"),
                base64.b64decode(str(ciphertext)),
                str(associated_data).encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 - 解密失败统一收敛
            raise PaymentDecryptError("微信支付 resource 解密失败") from exc

    def decrypt_resource(self, resource: Mapping[str, Any]) -> dict[str, Any]:
        """解密并解析 ``resource`` 为 JSON 字典。"""
        plaintext = self.decrypt_resource_bytes(resource)
        try:
            value = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentDecryptError("微信支付 resource 不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise PaymentDecryptError("微信支付 resource 顶层必须是对象")
        return value

    # ------------------------------------------------------------------
    # 4. 小程序 paySign
    # ------------------------------------------------------------------
    def sign_jsapi(self, app_id: str, time_stamp: str, nonce_str: str, package: str) -> str:
        """生成小程序 ``wx.requestPayment`` 的 ``paySign``。

        :param package: 固定格式 ``prepay_id=wx...``。
        """
        if not all([app_id, time_stamp, nonce_str, package]):
            raise ValueError("生成小程序 paySign 的参数不能为空")
        message = f"{app_id}\n{time_stamp}\n{nonce_str}\n{package}\n".encode("utf-8")
        return self._rsa_sign(message)

    def build_jsapi_pay_params(self, prepay_id: str) -> dict[str, str]:
        """生成小程序支付参数（不含订单号，由业务层补充）。"""
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        return {
            "appId": self.config.app_id,
            "timeStamp": timestamp,
            "nonceStr": nonce,
            "package": package,
            "signType": "RSA",
            "paySign": self.sign_jsapi(
                self.config.app_id,
                timestamp,
                nonce,
                package,
            ),
        }

    # ------------------------------------------------------------------
    # 5. 平台证书/公钥管理
    # ------------------------------------------------------------------
    def install_platform_certificate(self, serial_no: str, certificate_pem: bytes | str) -> None:
        """安装/更新一张微信支付平台证书。"""
        if not serial_no:
            raise PaymentConfigError("平台证书序列号不能为空")
        pem = certificate_pem.encode("utf-8") if isinstance(certificate_pem, str) else certificate_pem
        try:
            certificate = x509.load_pem_x509_certificate(pem)
        except Exception as exc:  # noqa: BLE001
            raise PaymentConfigError("微信支付平台证书格式无效") from exc
        self._platform_certs[str(serial_no)] = certificate

    def install_platform_certificates(self, payload: Mapping[str, Any]) -> None:
        """解析 ``GET /v3/certificates`` 响应并整体替换证书缓存。

        响应结构::

            {"data": [{"serial_no": "...", "encrypt_certificate": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": "...",
                "associated_data": "...",
                "ciphertext": "..."
            }}]}
        """
        data = payload.get("data")
        if not isinstance(data, list):
            raise PaymentConfigError("微信支付证书响应缺少 data 数组")

        certificates: dict[str, x509.Certificate] = {}
        for item in data:
            if not isinstance(item, Mapping):
                continue
            serial_no = str(item.get("serial_no") or "")
            encrypted = item.get("encrypt_certificate")
            if not serial_no or not isinstance(encrypted, Mapping):
                continue
            pem = self.decrypt_resource_bytes(encrypted)
            try:
                certificates[serial_no] = x509.load_pem_x509_certificate(pem)
            except Exception as exc:  # noqa: BLE001
                raise PaymentConfigError("微信支付平台证书解析失败") from exc
        if not certificates:
            raise PaymentConfigError("微信支付证书响应为空")
        self._platform_certs = certificates
        logger.info("wechat pay platform certificates refreshed count=%s", len(certificates))

    def install_public_key(self, key_id: str, public_key_pem: str) -> None:
        """安装微信支付公钥（公钥模式）。"""
        try:
            public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise PaymentConfigError("微信支付公钥格式无效") from exc
        self._public_keys[str(key_id)] = public_key

    @property
    def platform_certificate_serials(self) -> tuple[str, ...]:
        """返回当前缓存的平台证书序列号，便于日志/诊断。"""
        return tuple(self._platform_certs.keys())

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _rsa_sign(self, message: bytes) -> str:
        """使用商户私钥做 RSA-SHA256 签名并 Base64 编码。"""
        signature = self._private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def _verification_key(self, serial_no: str) -> Any:
        """按 ``Wechatpay-Serial`` 选择平台证书公钥或公钥模式公钥。"""
        public_key = self._public_keys.get(serial_no)
        if public_key is not None:
            return public_key
        certificate = self._platform_certs.get(serial_no)
        if certificate is not None:
            return certificate.public_key()
        raise PaymentUnknownSerialError(
            f"未找到微信支付平台证书/公钥：{serial_no}"
        )

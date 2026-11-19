"""虚拟支付发货推送：URL 校验、AES 解密、事件解析。

消息推送协议与小程序「消息推送」一致；虚拟支付事件：
``xpay_goods_deliver_notify`` / ``xpay_refund_notify`` 等。
"""

from __future__ import annotations

import base64
from hashlib import sha1
import json
import logging
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import settings
from app.services.payment.base import PaymentDecryptError, PaymentSignatureError, PaymentValidationError

logger = logging.getLogger(__name__)


def verify_url_signature(
    *,
    token: str,
    timestamp: str,
    nonce: str,
    echostr: str,
    signature: str,
) -> bool:
    """GET 接入校验：``sha1(sort(token,timestamp,nonce))``。"""
    pieces = sorted([token, timestamp, nonce])
    digest = sha1("".join(pieces).encode("utf-8")).hexdigest()
    return digest == (signature or "").lower()


def verify_msg_signature(
    *,
    token: str,
    timestamp: str,
    nonce: str,
    encrypt: str,
    msg_signature: str,
) -> bool:
    pieces = sorted([token, timestamp, nonce, encrypt])
    digest = sha1("".join(pieces).encode("utf-8")).hexdigest()
    return digest == (msg_signature or "").lower()


def _aes_key() -> bytes:
    raw = (
        settings.wechat_xpay_push_aes_key.get_secret_value().strip()
        if settings.wechat_xpay_push_aes_key
        else ""
    )
    if not raw:
        raise PaymentDecryptError("WECHAT_XPAY_PUSH_AES_KEY 未配置")
    # EncodingAESKey 为 43 字符，补 '=' 后 base64 解码得到 32 字节。
    try:
        return base64.b64decode(raw + "=")
    except Exception as exc:  # noqa: BLE001
        raise PaymentDecryptError("WECHAT_XPAY_PUSH_AES_KEY 无效") from exc


def decrypt_push_encrypt(encrypt_b64: str) -> str:
    """AES-256-CBC 解密消息推送 Encrypt 字段，返回明文 XML/JSON。"""
    key = _aes_key()
    iv = key[:16]
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plain = decryptor.update(base64.b64decode(encrypt_b64)) + decryptor.finalize()
    except Exception as exc:  # noqa: BLE001
        raise PaymentDecryptError("发货推送解密失败") from exc
    # PKCS#7 unpad
    pad = plain[-1]
    if isinstance(pad, str):
        pad = ord(pad)
    if pad < 1 or pad > 32:
        raise PaymentDecryptError("发货推送 padding 无效")
    content = plain[:-pad]
    # 结构：16 字节随机 + 4 字节网络序长度 + msg + appid
    if len(content) < 20:
        raise PaymentDecryptError("发货推送明文过短")
    msg_len = int.from_bytes(content[16:20], "big")
    msg = content[20 : 20 + msg_len]
    return msg.decode("utf-8")


def _xml_to_dict(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    data: dict[str, Any] = {}
    for child in root:
        if list(child):
            nested: dict[str, Any] = {}
            for grand in child:
                nested[grand.tag] = grand.text
            data[child.tag] = nested
        else:
            data[child.tag] = child.text
    return data


def parse_push_payload(
    raw_body: bytes,
    *,
    content_type: str | None = None,
    msg_signature: str | None = None,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """解析推送正文；兼容明文 JSON/XML 与安全模式 Encrypt。"""
    text = raw_body.decode("utf-8", errors="replace").strip()
    if not text:
        raise PaymentValidationError("发货推送正文为空")

    mode = settings.wechat_xpay_push_encrypt_mode
    token = (settings.wechat_xpay_push_token or "").strip()

    # JSON
    if text.startswith("{") or (content_type or "").lower().find("json") >= 0:
        data = json.loads(text)
        encrypt = data.get("Encrypt") or data.get("encrypt")
        if encrypt and mode in {"aes", "compatible"}:
            if token and msg_signature and timestamp and nonce:
                if not verify_msg_signature(
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                    encrypt=str(encrypt),
                    msg_signature=msg_signature,
                ):
                    raise PaymentSignatureError("发货推送签名校验失败")
            plain = decrypt_push_encrypt(str(encrypt))
            if plain.lstrip().startswith("{"):
                return json.loads(plain)
            return _xml_to_dict(plain)
        return data

    # XML
    data = _xml_to_dict(text)
    encrypt = data.get("Encrypt")
    if encrypt and mode in {"aes", "compatible"}:
        if token and msg_signature and timestamp and nonce:
            if not verify_msg_signature(
                token=token,
                timestamp=timestamp,
                nonce=nonce,
                encrypt=str(encrypt),
                msg_signature=msg_signature,
            ):
                raise PaymentSignatureError("发货推送签名校验失败")
        plain = decrypt_push_encrypt(str(encrypt))
        if plain.lstrip().startswith("{"):
            return json.loads(plain)
        return _xml_to_dict(plain)
    return data


def extract_goods_deliver(event: Mapping[str, Any]) -> dict[str, Any]:
    """从 ``xpay_goods_deliver_notify`` 提取发货字段。"""
    event_name = str(event.get("Event") or event.get("event") or "")
    if event_name and event_name != "xpay_goods_deliver_notify":
        raise PaymentValidationError(f"非道具发货事件: {event_name}")

    goods = event.get("GoodsInfo") or event.get("goods_info") or {}
    if isinstance(goods, str):
        try:
            goods = json.loads(goods)
        except json.JSONDecodeError:
            goods = {}
    wxpay = event.get("WeChatPayInfo") or event.get("wechat_pay_info") or {}
    if isinstance(wxpay, str):
        try:
            wxpay = json.loads(wxpay)
        except json.JSONDecodeError:
            wxpay = {}

    out_trade_no = str(
        event.get("OutTradeNo") or event.get("out_trade_no") or ""
    ).strip()
    return {
        "out_trade_no": out_trade_no,
        "openid": event.get("OpenId") or event.get("openid"),
        "env": event.get("Env") or event.get("env"),
        "transaction_id": (
            (wxpay or {}).get("TransactionId")
            or (wxpay or {}).get("transaction_id")
        ),
        "amount_cents": (
            (goods or {}).get("ActualPrice")
            or (goods or {}).get("actual_price")
        ),
        "product_id": (goods or {}).get("ProductId") or (goods or {}).get("product_id"),
        "attach": (goods or {}).get("Attach") or (goods or {}).get("attach"),
    }

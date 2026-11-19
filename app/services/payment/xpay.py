"""小程序虚拟支付（米大师）：签名参数、发货落库、查单兜底。

与 JSAPI ``PaymentOrderService.prepay`` 并列：
- 本模块**不**调用微信统一下单；客户端 ``wx.requestVirtualPayment`` 自行下单。
- 发货以 ``xpay_goods_deliver_notify`` 推送为准，查单仅作兜底。
- ``channel`` 落库为 ``wechat_xpay``，与普通 ``wechat`` 区分便于对账。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import hmac
import json
import logging
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.payment.base import (
    PaymentConfigError,
    PaymentValidationError,
)
from app.services.payment.orders import PaymentOrderService

logger = logging.getLogger(__name__)

XPAY_CHANNEL = "wechat_xpay"
REQUEST_VIRTUAL_PAYMENT_URI = "requestVirtualPayment"
QUERY_ORDER_URI = "/xpay/query_order"


@dataclass(frozen=True)
class VirtualPayParams:
    """前端 ``wx.requestVirtualPayment`` 所需字段。"""

    sign_data: str
    mode: str
    pay_sig: str
    signature: str
    offer_id: str
    product_id: str
    goods_price: int
    out_trade_no: str
    env: int


def calc_pay_sig(uri: str, post_body: str, app_key: str) -> str:
    """支付签名：``hmac_sha256(appKey, uri + '&' + signData)``。"""
    message = f"{uri}&{post_body}"
    return hmac.new(
        app_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def calc_user_signature(post_body: str, session_key: str) -> str:
    """用户态签名：``hmac_sha256(sessionKey, signData)``。"""
    return hmac.new(
        session_key.encode("utf-8"),
        post_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_sign_data(
    *,
    offer_id: str,
    product_id: str,
    goods_price: int,
    out_trade_no: str,
    env: int,
    buy_quantity: int = 1,
    attach: str = "",
) -> str:
    """构造稳定顺序的 signData JSON 字符串（参与签名与客户端透传必须一致）。"""
    payload = {
        "offerId": offer_id,
        "buyQuantity": int(buy_quantity),
        "env": int(env),
        "currencyType": "CNY",
        "productId": product_id,
        "goodsPrice": int(goods_price),
        "outTradeNo": out_trade_no,
        "attach": attach or out_trade_no,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def resolve_product_id(product_type: str) -> str:
    """``product_type`` → MP 道具 ID；映射缺失时回退同名。"""
    code = str(product_type or "").strip()
    if not code:
        raise PaymentValidationError("订单缺少 product_type，无法映射虚拟支付道具")
    mapped = settings.wechat_xpay_product_map().get(code)
    return mapped or code


async def exchange_session_key(js_code: str) -> tuple[str, str]:
    """``js_code`` → ``(openid, session_key)``。mock 登录返回固定值。"""
    code = (js_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 js_code，请先 uni.login")

    if settings.wechat_provider.lower() == "mock" or settings.wechat_xpay_mode == "mock":
        return (f"mock-openid-{code[-8:]}", f"mock-session-{code[-12:]}")

    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise PaymentConfigError("微信登录未配置，无法换取 session_key")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="微信登录服务暂时不可用") from exc

    if data.get("errcode") or not data.get("openid") or not data.get("session_key"):
        raise HTTPException(status_code=400, detail="微信登录凭证无效或已过期，请重试")
    return str(data["openid"]), str(data["session_key"])


class XpayService:
    """虚拟支付业务：出签名参数 + 发货落库。"""

    def __init__(self) -> None:
        self._orders = PaymentOrderService()

    def _require_enabled(self) -> None:
        if settings.wechat_xpay_mode == "disabled":
            raise PaymentConfigError("虚拟支付未启用（WECHAT_XPAY_MODE=disabled）")

    def _app_key(self) -> str:
        if settings.wechat_xpay_mode == "mock":
            return "mock-xpay-app-key"
        key = settings.wechat_xpay_app_key_for_env()
        if not key:
            raise PaymentConfigError("虚拟支付 AppKey 未配置")
        return key

    def _offer_id(self) -> str:
        if settings.wechat_xpay_mode == "mock":
            return (settings.wechat_xpay_offer_id or "mock-offer-id").strip()
        offer = (settings.wechat_xpay_offer_id or "").strip()
        if not offer:
            raise PaymentConfigError("WECHAT_XPAY_OFFER_ID 未配置")
        return offer

    async def build_virtual_pay_params(
        self,
        db: AsyncSession,
        user_id: int,
        order_no: str,
        js_code: str,
    ) -> VirtualPayParams:
        """校验订单并返回 ``wx.requestVirtualPayment`` 参数。"""
        self._require_enabled()
        openid_from_code, session_key = await exchange_session_key(js_code)

        result = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status, expire_at,
                          channel, product_type, product_name
                   FROM payment_order
                   WHERE order_no = :order_no AND user_id = :user_id"""
            ),
            {"order_no": order_no, "user_id": user_id},
        )
        order = result.mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        if int(order["status"]) != 0:
            raise HTTPException(status_code=409, detail="订单当前不可支付")
        if order["expire_at"] is not None:
            expire_at = order["expire_at"]
            if expire_at.tzinfo is not None:
                expire_at = expire_at.astimezone(UTC).replace(tzinfo=None)
            if expire_at <= datetime.now(UTC).replace(tzinfo=None):
                raise HTTPException(status_code=409, detail="订单已过期")

        stored_openid = await db.scalar(
            text("SELECT openid FROM users WHERE id = :user_id AND status = 1"),
            {"user_id": user_id},
        )
        if stored_openid and str(stored_openid) != openid_from_code and settings.wechat_xpay_mode == "real":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WECHAT_OPENID_MISMATCH",
                    "message": "当前微信登录与账号绑定不一致，请重新登录后再支付",
                    "retryable": False,
                },
            )

        amount_cents = int(
            (Decimal(str(order["amount"])) * Decimal(100)).to_integral_value()
        )
        if amount_cents <= 0:
            raise HTTPException(status_code=409, detail="订单金额无效")

        product_id = resolve_product_id(str(order["product_type"] or ""))
        offer_id = self._offer_id()
        env = int(settings.wechat_xpay_env)
        sign_data = build_sign_data(
            offer_id=offer_id,
            product_id=product_id,
            goods_price=amount_cents,
            out_trade_no=str(order["order_no"]),
            env=env,
            attach=str(order["order_no"]),
        )
        app_key = self._app_key()
        pay_sig = calc_pay_sig(REQUEST_VIRTUAL_PAYMENT_URI, sign_data, app_key)
        signature = calc_user_signature(sign_data, session_key)

        await db.execute(
            text(
                """UPDATE payment_order
                   SET channel = :channel,
                       payer_openid = COALESCE(payer_openid, :openid)
                   WHERE id = :id AND status = 0"""
            ),
            {
                "channel": XPAY_CHANNEL,
                "openid": openid_from_code,
                "id": order["id"],
            },
        )
        await db.commit()

        return VirtualPayParams(
            sign_data=sign_data,
            mode="short_series_goods",
            pay_sig=pay_sig,
            signature=signature,
            offer_id=offer_id,
            product_id=product_id,
            goods_price=amount_cents,
            out_trade_no=str(order["order_no"]),
            env=env,
        )

    async def mark_paid_from_deliver(
        self,
        db: AsyncSession,
        *,
        out_trade_no: str,
        transaction_id: str | None,
        amount_cents: int | None,
        payer_openid: str | None,
        product_id: str | None = None,
    ) -> dict[str, Any]:
        """道具发货推送 / 查单成功后落支付状态并投递履约任务。"""
        if not out_trade_no:
            raise PaymentValidationError("发货通知缺少 OutTradeNo")
        tx_id = (transaction_id or "").strip() or f"xpay:{out_trade_no}"

        result = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status, transaction_id,
                          channel, fulfillment_status, product_type
                   FROM payment_order
                   WHERE order_no = :order_no
                   FOR UPDATE"""
            ),
            {"order_no": out_trade_no},
        )
        order = result.mappings().first()
        if not order:
            raise PaymentValidationError("支付订单不存在")

        expected_cents = int(
            (Decimal(str(order["amount"])) * Decimal(100)).to_integral_value()
        )
        if amount_cents is not None and int(amount_cents) != expected_cents:
            raise PaymentValidationError("发货通知金额与订单金额不一致")

        if product_id:
            expected_product = resolve_product_id(str(order["product_type"] or ""))
            if product_id != expected_product:
                raise PaymentValidationError("发货通知道具与订单不匹配")

        if int(order["status"]) == 1:
            if order["transaction_id"] and order["transaction_id"] != tx_id:
                raise PaymentValidationError("已支付订单的交易号不一致")
            if str(order["fulfillment_status"] or "").upper() != "SUCCESS":
                await self._orders._ensure_fulfillment_job(db, order)
            return dict(order)
        if int(order["status"]) != 0:
            raise PaymentValidationError("订单当前状态不可支付")

        conflict = await db.scalar(
            text(
                """SELECT 1 FROM payment_order
                   WHERE channel = :channel AND transaction_id = :transaction_id
                     AND id <> :id
                   LIMIT 1"""
            ),
            {"channel": XPAY_CHANNEL, "transaction_id": tx_id, "id": order["id"]},
        )
        if conflict:
            raise PaymentValidationError("transaction_id 已被其他订单使用")

        paid_amount = (Decimal(expected_cents) / Decimal(100)).quantize(Decimal("0.01"))
        await db.execute(
            text(
                """UPDATE payment_order
                   SET status = 1,
                       channel = :channel,
                       transaction_id = :transaction_id,
                       trade_state = 'SUCCESS',
                       paid_amount = :paid_amount,
                       payer_openid = COALESCE(:payer_openid, payer_openid),
                       appid = :appid,
                       pay_time = UTC_TIMESTAMP(),
                       fulfillment_status = 'PENDING'
                   WHERE id = :id AND status = 0"""
            ),
            {
                "channel": XPAY_CHANNEL,
                "transaction_id": tx_id,
                "paid_amount": paid_amount,
                "payer_openid": payer_openid,
                "appid": (settings.wechat_app_id or "").strip() or None,
                "id": order["id"],
            },
        )
        await self._orders._ensure_fulfillment_job(db, order)
        updated = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status,
                          transaction_id, paid_amount, fulfillment_status
                   FROM payment_order WHERE id = :id"""
            ),
            {"id": order["id"]},
        )
        return dict(updated.mappings().one())

    async def confirm_by_query(
        self,
        db: AsyncSession,
        user_id: int,
        order_no: str,
    ) -> dict[str, Any]:
        """支付成功后前端轮询兜底：调微信查单，成功则发货落库。"""
        self._require_enabled()
        result = await db.execute(
            text(
                """SELECT id, order_no, user_id, status, amount, product_type
                   FROM payment_order
                   WHERE order_no = :order_no AND user_id = :user_id"""
            ),
            {"order_no": order_no, "user_id": user_id},
        )
        order = result.mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        if int(order["status"]) == 1:
            return dict(order)

        if settings.wechat_xpay_mode == "mock":
            paid = await self.mark_paid_from_deliver(
                db,
                out_trade_no=str(order["order_no"]),
                transaction_id=f"mock-xpay:{order['order_no']}",
                amount_cents=int(
                    (Decimal(str(order["amount"])) * Decimal(100)).to_integral_value()
                ),
                payer_openid=None,
                product_id=resolve_product_id(str(order["product_type"] or "")),
            )
            await db.commit()
            return paid

        # 外部 HTTP 在写事务外执行。
        await db.commit()
        remote = await query_xpay_order(str(order["order_no"]))
        if not remote.get("paid"):
            raise HTTPException(status_code=409, detail="微信侧尚未确认支付成功")
        paid = await self.mark_paid_from_deliver(
            db,
            out_trade_no=str(order["order_no"]),
            transaction_id=remote.get("transaction_id"),
            amount_cents=remote.get("amount_cents"),
            payer_openid=remote.get("openid"),
            product_id=remote.get("product_id"),
        )
        await db.commit()
        return paid


async def query_xpay_order(out_trade_no: str) -> dict[str, Any]:
    """调用 ``/xpay/query_order``；返回 ``paid`` 与交易字段。"""
    from app.services.auth import get_wechat_access_token
    import httpx

    offer_id = (settings.wechat_xpay_offer_id or "").strip()
    app_key = settings.wechat_xpay_app_key_for_env()
    if not offer_id or not app_key:
        raise PaymentConfigError("虚拟支付查单缺少 OfferID/AppKey")

    # 查单 body 以 out_trade_no + env 为主；参与 pay_sig 的字符串必须与 POST body 一致。
    body = json.dumps(
        {
            "env": int(settings.wechat_xpay_env),
            "out_trade_no": out_trade_no,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    token = await get_wechat_access_token()
    pay_sig = calc_pay_sig(QUERY_ORDER_URI, body, app_key)

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"https://api.weixin.qq.com{QUERY_ORDER_URI}",
            params={"access_token": token, "pay_sig": pay_sig},
            content=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        data = response.json()

    errcode = int(data.get("errcode") or data.get("ErrCode") or 0)
    if errcode != 0:
        logger.warning("xpay query_order failed: %s", data)
        return {"paid": False, "raw": data}

    status = str(
        data.get("status")
        or data.get("order_state")
        or data.get("pay_state")
        or ""
    ).upper()
    paid = status in {"SUCCESS", "ORDER_STATE_SUCCESS", "2", "PAID"} or bool(
        data.get("paid")
    )
    goods = data.get("goods_info") or data.get("GoodsInfo") or {}
    wxpay = data.get("wechat_pay_info") or data.get("WeChatPayInfo") or {}
    return {
        "paid": paid,
        "transaction_id": (
            wxpay.get("TransactionId")
            or wxpay.get("transaction_id")
            or data.get("transaction_id")
            or data.get("wx_order_id")
        ),
        "amount_cents": (
            goods.get("ActualPrice")
            or goods.get("actual_price")
            or data.get("actual_price")
        ),
        "product_id": goods.get("ProductId") or goods.get("product_id"),
        "openid": data.get("openid") or data.get("OpenId"),
        "raw": data,
    }

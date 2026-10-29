"""微信支付订单服务：预支付与支付成功落库。

本模块刻意不覆盖旧 ``app/services/membership.py`` / ``app/services/payments.py``：
- 旧测试支付链路继续可用；
- 新真实支付链路由 ``WECHAT_PAY_MODE=real`` 控制，便于灰度与回滚。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.payment import JsapiPayParams
from app.services.payment.base import (
    PaymentConfigError,
    PaymentGateway,
    PaymentNotification,
    PaymentValidationError,
)
from app.services.payment.crypto import WechatPayCrypto

logger = logging.getLogger(__name__)


class PaymentOrderService:
    """支付订单服务。

    ``prepay`` 需要 ``gateway + crypto``；``mark_paid`` 只操作数据库，不需要网关。
    这样回调路由可以只构造一个轻量实例，不必创建 HTTP 连接池。
    """

    def __init__(
        self,
        gateway: PaymentGateway | None = None,
        crypto: WechatPayCrypto | None = None,
    ) -> None:
        self.gateway = gateway
        self.crypto = crypto

    async def prepay(
        self,
        db: AsyncSession,
        user_id: int,
        order_no: str,
    ) -> JsapiPayParams:
        """校验订单并调用微信统一下单，返回小程序支付参数。

        外部 HTTP 调用前主动 ``commit`` 结束读事务，避免把微信请求包在数据库
        事务里。
        """
        if self.gateway is None or self.crypto is None:
            raise PaymentConfigError("微信支付网关未初始化")
        if not settings.wechat_pay_notify_url:
            raise PaymentConfigError("WECHAT_PAY_NOTIFY_URL 未配置")

        result = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status, expire_at,
                          channel, product_name
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
        if str(order["channel"] or "wechat") != "wechat":
            raise HTTPException(status_code=409, detail="订单不是微信支付订单")

        openid = await db.scalar(
            text("SELECT openid FROM users WHERE id = :user_id AND status = 1"),
            {"user_id": user_id},
        )
        if not openid:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WECHAT_OPENID_REQUIRED",
                    "message": "请先完成微信登录授权",
                    "retryable": False,
                },
            )

        amount_cents = int(
            (Decimal(str(order["amount"])) * Decimal(100)).to_integral_value()
        )
        if amount_cents <= 0:
            raise HTTPException(status_code=409, detail="订单金额无效")

        # 关键：外部 HTTP 调用前结束当前读事务。
        await db.commit()

        prepay = await self.gateway.create_jsapi_order(
            out_trade_no=str(order["order_no"]),
            description=str(order["product_name"] or "宣誓爱服务"),
            amount_cents=amount_cents,
            openid=str(openid),
            notify_url=settings.wechat_pay_notify_url,
        )

        updated = await db.execute(
            text(
                """UPDATE payment_order
                   SET prepay_id = :prepay_id, payer_openid = :payer_openid,
                       channel = 'wechat'
                   WHERE id = :id AND status = 0"""
            ),
            {
                "prepay_id": prepay.prepay_id,
                "payer_openid": str(openid),
                "id": order["id"],
            },
        )
        await db.commit()
        if updated.rowcount != 1:
            # 微信侧可能已生成预支付单；这里只拒绝本地状态已变化的请求，
            # 用户重新进入支付页会复用同一 out_trade_no。
            raise HTTPException(status_code=409, detail="订单状态已变化，请刷新后重试")

        params = self.crypto.build_jsapi_pay_params(prepay.prepay_id)
        return JsapiPayParams(
            app_id=params["appId"],
            time_stamp=params["timeStamp"],
            nonce_str=params["nonceStr"],
            package=params["package"],
            sign_type="RSA",
            pay_sign=params["paySign"],
        )

    async def mark_paid(
        self,
        db: AsyncSession,
        notification: PaymentNotification,
    ) -> dict:
        """校验微信通知并把订单置为已支付。

        只做支付状态与金额校验，不写会员/爆灯/服务单履约逻辑；履约由第三轮
        ``FulfillmentDispatcher`` 负责。调用方必须把本方法放在自己的事务中，
        本方法不 commit。
        """
        if not notification.out_trade_no:
            raise PaymentValidationError("支付通知缺少 out_trade_no")
        if not notification.transaction_id:
            raise PaymentValidationError("支付通知缺少 transaction_id")
        if not settings.wechat_pay_mch_id or not settings.wechat_pay_app_id:
            raise PaymentConfigError("微信支付商户号/AppID 未配置")
        if notification.mchid != settings.wechat_pay_mch_id:
            raise PaymentValidationError("支付通知 mchid 不匹配")
        if notification.appid != settings.wechat_pay_app_id:
            raise PaymentValidationError("支付通知 appid 不匹配")
        if notification.trade_state != "SUCCESS":
            raise PaymentValidationError("支付通知 trade_state 不是 SUCCESS")
        if notification.amount_total is None:
            raise PaymentValidationError("支付通知缺少金额")
        if (notification.currency or "CNY") != "CNY":
            raise PaymentValidationError("支付通知币种不是 CNY")

        result = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status, transaction_id,
                          channel, fulfillment_status
                   FROM payment_order
                   WHERE order_no = :order_no
                   FOR UPDATE"""
            ),
            {"order_no": notification.out_trade_no},
        )
        order = result.mappings().first()
        if not order:
            raise PaymentValidationError("支付订单不存在")
        if str(order["channel"] or "wechat") != "wechat":
            raise PaymentValidationError("订单渠道不是微信支付")

        expected_cents = int(
            (Decimal(str(order["amount"])) * Decimal(100)).to_integral_value()
        )
        if int(notification.amount_total) != expected_cents:
            raise PaymentValidationError("支付通知金额与订单金额不一致")

        if int(order["status"]) == 1:
            # 幂等：已支付订单只允许同一交易号重复通知。
            if order["transaction_id"] and order["transaction_id"] != notification.transaction_id:
                raise PaymentValidationError("已支付订单的交易号不一致")
            # 历史订单可能已支付但未写履约任务；补一条，Worker 会按唯一键去重。
            if str(order["fulfillment_status"] or "").upper() != "SUCCESS":
                await self._ensure_fulfillment_job(db, order)
            return dict(order)
        if int(order["status"]) != 0:
            raise PaymentValidationError("订单当前状态不可支付")

        conflict = await db.scalar(
            text(
                """SELECT 1 FROM payment_order
                   WHERE channel = 'wechat' AND transaction_id = :transaction_id
                     AND id <> :id
                   LIMIT 1"""
            ),
            {"transaction_id": notification.transaction_id, "id": order["id"]},
        )
        if conflict:
            raise PaymentValidationError("transaction_id 已被其他订单使用")

        paid_amount = (
            Decimal(int(notification.amount_total)) / Decimal(100)
        ).quantize(Decimal("0.01"))
        await db.execute(
            text(
                """UPDATE payment_order
                   SET status = 1,
                       transaction_id = :transaction_id,
                       trade_state = :trade_state,
                       paid_amount = :paid_amount,
                       payer_openid = :payer_openid,
                       mchid = :mchid,
                       appid = :appid,
                       pay_time = UTC_TIMESTAMP(),
                       fulfillment_status = 'PENDING'
                   WHERE id = :id AND status = 0"""
            ),
            {
                "transaction_id": notification.transaction_id,
                "trade_state": notification.trade_state,
                "paid_amount": paid_amount,
                "payer_openid": notification.payer_openid,
                "mchid": notification.mchid,
                "appid": notification.appid,
                "id": order["id"],
            },
        )
        # 支付成功与履约任务同事务写入：要么都成功，要么都回滚。
        await self._ensure_fulfillment_job(db, order)
        updated = await db.execute(
            text(
                """SELECT id, order_no, user_id, type, amount, status,
                          transaction_id, paid_amount, fulfillment_status
                   FROM payment_order WHERE id = :id"""
            ),
            {"id": order["id"]},
        )
        return dict(updated.mappings().one())

    async def _ensure_fulfillment_job(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
    ) -> None:
        """写入 FULFILL 任务；``biz_key`` 保证同一订单只投递一次。"""
        await db.execute(
            text(
                """INSERT INTO payment_fulfillment_job
                   (biz_key, order_id, order_no, order_type, job_type,
                    status, next_retry_at)
                   VALUES (:biz_key, :order_id, :order_no, :order_type,
                           'FULFILL', 'PENDING', UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
            ),
            {
                "biz_key": f"FULFILL:{int(order['id'])}",
                "order_id": int(order["id"]),
                "order_no": str(order["order_no"]),
                "order_type": int(order["type"]),
            },
        )

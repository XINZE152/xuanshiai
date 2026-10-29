"""type=3 红娘服务单履约。"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matchmaker import activate_paid_service_order
from app.services.payment.fulfillment.base import FulfillmentError


class MatchmakerServiceFulfillmentHandler:
    """付费红娘服务开通与退款关闭。

    复用项目现有的 ``activate_paid_service_order``；该函数内部按
    ``payment_order.service_request_id`` 幂等，重复调用返回同一个 service_id。
    """

    order_type = 3

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        if order.get("service_product_id") is None:
            raise FulfillmentError("红娘服务订单缺少 service_product_id")
        service_id = await activate_paid_service_order(db, int(order["id"]))
        return {"fulfilled": True, "service_id": service_id}

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        """关闭服务单及关联的约见/联系方式。

        与 ``app/services/finance.py:refund_order`` 的关闭口径保持一致；
        分成冲正与退款状态由 RefundService 负责，本 Handler 只回收业务权益。
        """
        reason = str(refund.get("reason") or "支付退款")
        services = (
            await db.execute(
                text(
                    """SELECT id FROM matchmaker_service
                       WHERE order_id = :order_id FOR UPDATE"""
                ),
                {"order_id": order["id"]},
            )
        ).mappings().all()

        for service in services:
            service_id = int(service["id"])
            await db.execute(
                text(
                    """UPDATE matchmaker_service
                       SET status = 3,
                           feedback = CONCAT(COALESCE(feedback, ''), '\n退款关闭：', :reason),
                           end_at = COALESCE(end_at, UTC_TIMESTAMP()),
                           updated_at = UTC_TIMESTAMP()
                       WHERE id = :service_id AND status <> 3"""
                ),
                {"service_id": service_id, "reason": reason},
            )
            await db.execute(
                text(
                    """UPDATE matchmaker_contact_exchange
                       SET status = 'HIDDEN', hidden_at = UTC_TIMESTAMP(),
                           hidden_reason = '订单退款', updated_at = UTC_TIMESTAMP()
                       WHERE service_id = :service_id
                         AND status NOT IN ('REVOKED', 'HIDDEN')"""
                ),
                {"service_id": service_id},
            )
            await db.execute(
                text(
                    """UPDATE meeting_request
                       SET status = 'CLOSED', updated_at = UTC_TIMESTAMP()
                       WHERE service_id = :service_id
                         AND status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED')"""
                ),
                {"service_id": service_id},
            )
            await db.execute(
                text(
                    """UPDATE meeting_record mr
                       JOIN meeting_request rq ON rq.id = mr.request_id
                       SET mr.status = 'CANCELLED',
                           mr.cancel_reason = '关联红娘服务已退款',
                           mr.updated_at = UTC_TIMESTAMP()
                       WHERE rq.service_id = :service_id
                         AND mr.status IN ('SCHEDULED', 'REMINDED')"""
                ),
                {"service_id": service_id},
            )

"""微信支付退款服务。

关键约束：
- 同一订单的退款创建必须锁 ``payment_order`` 行，防止并发超额退款；
- 外部微信退款调用必须在数据库事务之外；
- 微信超时/5xx 不直接置 FAILED，保留 CREATED/PROCESSING 供后续 query_refund 重试；
- 退款成功后的权益回收通过 ``payment_fulfillment_job`` 的 REVOKE 任务异步执行。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.base import (
    PaymentAPIError,
    PaymentConfigError,
    PaymentGateway,
    PaymentNotification,
    PaymentValidationError,
    RefundResult,
)

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_RESERVED_REFUND_STATUSES = ("CREATED", "PROCESSING", "SUCCESS")

# 退款查询补偿：单笔最多尝试 8 次，避免微信长期不可用时无限重试。
REFUND_MAX_ATTEMPTS = 8
REFUND_RETRY_BASE_SECONDS = 60
REFUND_RETRY_MAX_SECONDS = 3600


class RefundService:
    """退款申请、退款回调与退款查询。"""

    def __init__(
        self,
        gateway: PaymentGateway | None = None,
        notify_url: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.notify_url = notify_url

    async def create_refund(
        self,
        db: AsyncSession,
        *,
        order_id: int,
        amount: Decimal,
        reason: str,
        idempotency_key: str,
        operator_id: int,
    ) -> RefundResult:
        """创建退款单并调用微信退款。

        步骤：
        1. 锁订单，校验状态/渠道/可退余额；
        2. 按 ``order_id + idempotency_key`` 幂等；
        3. 插入 ``payment_refund``（CREATED）并 commit；
        4. 在事务外调用微信退款；
        5. 新事务回写退款状态；成功则更新订单并投递 REVOKE 任务。
        """
        if self.gateway is None:
            raise PaymentConfigError("微信支付网关未初始化")
        if not self.notify_url:
            raise PaymentConfigError("WECHAT_PAY_REFUND_NOTIFY_URL 未配置")
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise PaymentValidationError("退款幂等键长度必须为 8-128")
        refund_amount = Decimal(str(amount)).quantize(_CENT)
        if refund_amount <= 0:
            raise PaymentValidationError("退款金额必须大于 0")
        reason = (reason or "").strip()
        if not reason:
            raise PaymentValidationError("退款原因不能为空")

        order = await _lock_order(db, order_id)
        if not order:
            raise PaymentValidationError("支付订单不存在")
        if int(order["status"]) != 1:
            raise PaymentValidationError("只有支付成功订单才能退款")
        if str(order["channel"] or "wechat") != "wechat":
            raise PaymentValidationError("订单渠道不是微信支付")
        if str(order["refund_status"] or "NONE").upper() == "FULL":
            raise PaymentValidationError("订单已全额退款")

        existing = await _find_by_idempotency(db, order_id, idempotency_key)
        if existing:
            await db.commit()
            return _refund_result(existing)

        reserved = await _sum_refund_amount(
            db, order_id, statuses=_RESERVED_REFUND_STATUSES
        )
        order_amount = Decimal(str(order["amount"])).quantize(_CENT)
        refundable = order_amount - reserved
        if refund_amount > refundable:
            raise PaymentValidationError(
                f"退款金额超出可退余额：可退 {refundable:.2f} 元"
            )

        refund_no = _new_refund_no()
        refund_type = "FULL" if refund_amount == order_amount else "PARTIAL"
        await db.execute(
            text(
                """INSERT INTO payment_refund
                   (refund_no, order_id, order_no, user_id, channel, transaction_id,
                    order_amount, refund_amount, currency, refund_type, status,
                    reason, operator_id, idempotency_key)
                   VALUES (:refund_no, :order_id, :order_no, :user_id, 'wechat',
                           :transaction_id, :order_amount, :refund_amount, 'CNY',
                           :refund_type, 'CREATED', :reason, :operator_id, :idem)"""
            ),
            {
                "refund_no": refund_no,
                "order_id": order_id,
                "order_no": order["order_no"],
                "user_id": order["user_id"],
                "transaction_id": order["transaction_id"],
                "order_amount": order_amount,
                "refund_amount": refund_amount,
                "refund_type": refund_type,
                "reason": reason[:255],
                "operator_id": operator_id,
                "idem": idempotency_key,
            },
        )
        # 外部 HTTP 调用前结束事务。
        await db.commit()

        try:
            result = await self.gateway.create_refund(
                out_refund_no=refund_no,
                out_trade_no=str(order["order_no"]),
                refund_cents=int(refund_amount * 100),
                total_cents=int(order_amount * 100),
                reason=reason[:80],
                notify_url=self.notify_url,
            )
        except PaymentAPIError as exc:
            # 4xx 视为明确失败；超时/5xx 保留 CREATED，等待 query_refund 重试。
            if exc.status_code >= 500 or exc.status_code in (502, 504):
                await _mark_retry(db, refund_no, str(exc))
            else:
                await _mark_failed(db, refund_no, str(exc))
            raise
        except Exception as exc:
            # 未知异常同样不把退款单置 FAILED，保留 CREATED 供查询补偿。
            await _mark_retry(db, refund_no, f"unexpected: {exc}")
            raise

        async with db.begin():
            refund = await _lock_refund_by_no(db, refund_no)
            if not refund:
                raise PaymentValidationError("退款单不存在")
            status = _map_refund_status(result.status)
            await _update_refund_status(
                db,
                refund,
                status=status,
                wechat_refund_id=result.wechat_refund_id,
                fail_reason=None if status != "FAILED" else "微信退款返回失败",
            )
            if status == "SUCCESS":
                full = await _apply_order_refund(db, order_id)
                # 部分退款不回收全部权益；仅在订单全额退完时投递一次 REVOKE。
                if full:
                    await _enqueue_revoke(
                        db,
                        order=order,
                        refund_no=refund_no,
                        reason=reason,
                        refund_amount=refund_amount,
                    )
        return RefundResult(
            refund_no=refund_no,
            wechat_refund_id=result.wechat_refund_id,
            status=status,
            refund_type=refund_type,
            order_no=str(order["order_no"]),
            amount=refund_amount,
            raw=result.raw,
        )

    async def apply_notify(
        self,
        db: AsyncSession,
        notification: PaymentNotification,
    ) -> RefundResult:
        """处理微信退款回调；调用方负责事务边界，本方法不 commit。"""
        refund_no = (notification.refund_no or "").strip()
        if not refund_no:
            raise PaymentValidationError("退款通知缺少 out_refund_no")
        if not notification.refund_status:
            raise PaymentValidationError("退款通知缺少 refund_status")

        # 先读退款单拿 order_id，再按「订单 -> 退款单」固定顺序加锁，避免死锁。
        head = await _find_refund_head(db, refund_no)
        if not head:
            raise PaymentValidationError("退款单不存在")
        order = await _lock_order(db, int(head["order_id"]))
        if not order:
            raise PaymentValidationError("退款单关联订单不存在")
        refund = await _lock_refund_by_no(db, refund_no)
        if not refund:
            raise PaymentValidationError("退款单不存在")

        if notification.out_trade_no and notification.out_trade_no != order["order_no"]:
            raise PaymentValidationError("退款通知的商户订单号与退款单不一致")
        if notification.refund_amount is not None:
            notified_amount = (Decimal(int(notification.refund_amount)) / Decimal(100)).quantize(_CENT)
            local_amount = Decimal(str(refund["refund_amount"])).quantize(_CENT)
            if notified_amount != local_amount:
                raise PaymentValidationError("退款通知金额与退款单不一致")

        if str(refund["status"]) == "SUCCESS":
            return _refund_result(refund)
        if notification.notify_id and refund.get("notify_id") == notification.notify_id:
            return _refund_result(refund)

        status = _map_refund_status(notification.refund_status)
        await _update_refund_status(
            db,
            refund,
            status=status,
            wechat_refund_id=notification.refund_id,
            notify_id=notification.notify_id,
            fail_reason=(
                None if status == "SUCCESS" else f"微信退款状态：{notification.refund_status}"
            ),
        )
        if status == "SUCCESS":
            full = await _apply_order_refund(db, int(order["id"]))
            if full:
                await _enqueue_revoke(
                    db,
                    order=order,
                    refund_no=refund_no,
                    reason=str(refund["reason"]),
                    refund_amount=Decimal(str(refund["refund_amount"])),
                )
        return RefundResult(
            refund_no=refund_no,
            wechat_refund_id=notification.refund_id,
            status=status,
            refund_type=str(refund.get("refund_type") or "FULL"),
            order_no=str(refund["order_no"]),
            amount=Decimal(str(refund["refund_amount"])),
            raw={"notify_id": notification.notify_id, "resource": notification.raw},
        )

    async def query_refund(
        self,
        db: AsyncSession,
        refund_no: str,
    ) -> RefundResult:
        """查询微信退款状态并回写本地；用于超时后的补偿。"""
        if self.gateway is None:
            raise PaymentConfigError("微信支付网关未初始化")
        refund = await _find_refund_by_no(db, refund_no)
        if not refund:
            raise PaymentValidationError("退款单不存在")
        if str(refund["status"]) == "SUCCESS":
            return _refund_result(refund)
        order_id = int(refund["order_id"])

        await db.commit()
        result = await self.gateway.query_refund(refund_no)
        async with db.begin():
            # 固定「订单 -> 退款单」锁顺序，与 create_refund/apply_notify 一致。
            order = await _lock_order(db, order_id)
            if not order:
                raise PaymentValidationError("退款单关联订单不存在")
            refund = await _lock_refund_by_no(db, refund_no)
            if not refund:
                raise PaymentValidationError("退款单不存在")
            status = _map_refund_status(result.status)
            await _update_refund_status(
                db,
                refund,
                status=status,
                wechat_refund_id=result.wechat_refund_id,
                fail_reason=None if status != "FAILED" else "微信退款查询返回失败",
            )
            if status == "SUCCESS":
                full = await _apply_order_refund(db, int(order["id"]))
                if full:
                    await _enqueue_revoke(
                        db,
                        order=order,
                        refund_no=refund_no,
                        reason=str(refund["reason"]),
                        refund_amount=Decimal(str(refund["refund_amount"])),
                    )
        return RefundResult(
            refund_no=refund_no,
            wechat_refund_id=result.wechat_refund_id,
            status=status,
            refund_type=str(refund.get("refund_type") or "FULL"),
            order_no=str(refund["order_no"]),
            amount=Decimal(str(refund["refund_amount"])),
            raw=result.raw,
        )

    async def retry_pending(
        self,
        db: AsyncSession,
        limit: int = 20,
    ) -> dict[str, int]:
        """扫描并查询未完成退款，最多 8 次后置 FAILED。

        返回 ``{"claimed","success","retry","failed"}``，供 Worker 输出统计。
        """
        if self.gateway is None:
            raise PaymentConfigError("微信支付网关未初始化")
        claimed = await _claim_refund_retries(db, limit)
        stats = {"claimed": len(claimed), "success": 0, "retry": 0, "failed": 0}

        for row in claimed:
            refund_no = str(row["refund_no"])
            attempt = int(row["attempt_count"] or 0) + 1
            try:
                result = await self.query_refund(db, refund_no)
            except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其他退款
                if attempt >= REFUND_MAX_ATTEMPTS:
                    await _mark_refund_dead(
                        db,
                        refund_no,
                        f"退款查询重试次数耗尽: {type(exc).__name__}: {exc}",
                    )
                    logger.error(
                        "payment_refund_dead refund_no=%s attempt=%s error=%s",
                        refund_no,
                        attempt,
                        exc,
                    )
                    stats["failed"] += 1
                else:
                    await _schedule_refund_retry(db, refund_no, attempt, str(exc))
                    stats["retry"] += 1
                continue

            if result.status == "SUCCESS":
                stats["success"] += 1
            elif result.status in ("FAILED", "CLOSED"):
                await _mark_refund_dead(
                    db,
                    refund_no,
                    f"微信退款最终状态：{result.status}",
                )
                logger.error(
                    "payment_refund_dead refund_no=%s attempt=%s status=%s",
                    refund_no,
                    attempt,
                    result.status,
                )
                stats["failed"] += 1
            elif attempt >= REFUND_MAX_ATTEMPTS:
                # 多次查询仍 PROCESSING：不再无限重试，置 FAILED 并告警。
                await _mark_refund_dead(db, refund_no, "退款查询重试次数耗尽，仍为处理中")
                logger.error(
                    "payment_refund_dead refund_no=%s attempt=%s status=PROCESSING",
                    refund_no,
                    attempt,
                )
                stats["failed"] += 1
            else:
                # PROCESSING：继续等待下一次查询。
                await _schedule_refund_retry(db, refund_no, attempt, None)
                stats["retry"] += 1

        if claimed:
            logger.info("payment_refund_retry_round %s", stats)
        return stats


async def _claim_refund_retries(
    db: AsyncSession,
    limit: int,
) -> list[Mapping[str, Any]]:
    """领取到期的 CREATED/PROCESSING 退款单，并预占 60 秒防止并发查询。"""
    async with db.begin():
        result = await db.execute(
            text(
                """SELECT id, refund_no, attempt_count
                   FROM payment_refund
                   WHERE status IN ('CREATED', 'PROCESSING')
                     AND attempt_count < :max_attempts
                     AND (next_retry_at IS NULL OR next_retry_at <= UTC_TIMESTAMP())
                   ORDER BY id
                   LIMIT :limit
                   FOR UPDATE SKIP LOCKED"""
            ),
            {"max_attempts": REFUND_MAX_ATTEMPTS, "limit": int(limit)},
        )
        rows = [dict(row) for row in result.mappings().all()]
        for row in rows:
            await db.execute(
                text(
                    """UPDATE payment_refund
                       SET attempt_count = attempt_count + 1,
                           next_retry_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 60 SECOND),
                           updated_at = UTC_TIMESTAMP()
                       WHERE id = :id"""
                ),
                {"id": row["id"]},
            )
    return rows


async def _schedule_refund_retry(
    db: AsyncSession,
    refund_no: str,
    attempt: int,
    error: str | None,
) -> None:
    delay = min(
        REFUND_RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0)),
        REFUND_RETRY_MAX_SECONDS,
    )
    try:
        async with db.begin():
            await db.execute(
                text(
                    f"""UPDATE payment_refund
                        SET next_retry_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL {int(delay)} SECOND),
                            fail_reason = :error, updated_at = UTC_TIMESTAMP()
                        WHERE refund_no = :refund_no
                          AND status IN ('CREATED', 'PROCESSING')"""
                ),
                {
                    "refund_no": refund_no,
                    "error": (error or "微信退款处理中")[:255],
                },
            )
    except Exception:
        logger.exception("payment_refund_schedule_retry_failed refund_no=%s", refund_no)


async def _mark_refund_dead(db: AsyncSession, refund_no: str, error: str) -> None:
    try:
        async with db.begin():
            await db.execute(
                text(
                    """UPDATE payment_refund
                       SET status = 'FAILED', next_retry_at = NULL,
                           fail_reason = :error, updated_at = UTC_TIMESTAMP()
                       WHERE refund_no = :refund_no
                         AND status IN ('CREATED', 'PROCESSING')"""
                ),
                {"refund_no": refund_no, "error": error[:255]},
            )
    except Exception:
        logger.exception("payment_refund_mark_dead_failed refund_no=%s", refund_no)


async def _lock_order(db: AsyncSession, order_id: int) -> Mapping[str, Any] | None:
    result = await db.execute(
        text(
            """SELECT id, order_no, user_id, type, amount, status, transaction_id,
                      channel, refund_status
               FROM payment_order
               WHERE id = :id
               FOR UPDATE"""
        ),
        {"id": order_id},
    )
    return result.mappings().first()


async def _find_by_idempotency(
    db: AsyncSession,
    order_id: int,
    idempotency_key: str,
) -> Mapping[str, Any] | None:
    result = await db.execute(
        text(
            """SELECT * FROM payment_refund
               WHERE order_id = :order_id AND idempotency_key = :idem
               LIMIT 1"""
        ),
        {"order_id": order_id, "idem": idempotency_key},
    )
    return result.mappings().first()


async def _find_refund_head(db: AsyncSession, refund_no: str) -> Mapping[str, Any] | None:
    result = await db.execute(
        text(
            """SELECT id, order_id, status, notify_id
               FROM payment_refund WHERE refund_no = :refund_no LIMIT 1"""
        ),
        {"refund_no": refund_no},
    )
    return result.mappings().first()


async def _find_refund_by_no(db: AsyncSession, refund_no: str) -> Mapping[str, Any] | None:
    result = await db.execute(
        text("SELECT * FROM payment_refund WHERE refund_no = :refund_no LIMIT 1"),
        {"refund_no": refund_no},
    )
    return result.mappings().first()


async def _lock_refund_by_no(
    db: AsyncSession,
    refund_no: str,
) -> Mapping[str, Any] | None:
    result = await db.execute(
        text(
            """SELECT * FROM payment_refund
               WHERE refund_no = :refund_no FOR UPDATE"""
        ),
        {"refund_no": refund_no},
    )
    return result.mappings().first()


async def _sum_refund_amount(
    db: AsyncSession,
    order_id: int,
    *,
    statuses: tuple[str, ...],
) -> Decimal:
    # statuses 只来自内部常量，不使用外部输入拼接。
    placeholders = ", ".join(f":s{i}" for i in range(len(statuses)))
    params: dict[str, Any] = {"order_id": order_id}
    params.update({f"s{i}": value for i, value in enumerate(statuses)})
    result = await db.scalar(
        text(
            f"""SELECT COALESCE(SUM(refund_amount), 0)
                FROM payment_refund
                WHERE order_id = :order_id AND status IN ({placeholders})"""
        ),
        params,
    )
    return Decimal(str(result or 0)).quantize(_CENT)


async def _apply_order_refund(db: AsyncSession, order_id: int) -> bool:
    """按成功退款总额更新订单退款状态；返回是否已全额退完。"""
    order = await _lock_order(db, order_id)
    if not order:
        raise PaymentValidationError("支付订单不存在")
    refunded = await _sum_refund_amount(db, order_id, statuses=("SUCCESS",))
    order_amount = Decimal(str(order["amount"])).quantize(_CENT)
    full = refunded >= order_amount
    await db.execute(
        text(
            """UPDATE payment_order
               SET refunded_amount = :refunded,
                   refund_status = :refund_status,
                   status = :status,
                   refund_time = CASE WHEN :full = 1 THEN UTC_TIMESTAMP() ELSE refund_time END
               WHERE id = :id"""
        ),
        {
            "refunded": refunded,
            "refund_status": "FULL" if full else "PARTIAL",
            "status": 3 if full else 1,
            "full": 1 if full else 0,
            "id": order_id,
        },
    )
    return full


async def _update_refund_status(
    db: AsyncSession,
    refund: Mapping[str, Any],
    *,
    status: str,
    wechat_refund_id: str | None = None,
    notify_id: str | None = None,
    fail_reason: str | None = None,
) -> None:
    await db.execute(
        text(
            """UPDATE payment_refund
               SET status = :status,
                   wechat_refund_id = COALESCE(:wechat_refund_id, wechat_refund_id),
                   notify_id = COALESCE(:notify_id, notify_id),
                   success_time = CASE WHEN :status = 'SUCCESS' THEN UTC_TIMESTAMP() ELSE success_time END,
                   fail_reason = :fail_reason,
                   updated_at = UTC_TIMESTAMP()
               WHERE id = :id"""
        ),
        {
            "status": status,
            "wechat_refund_id": wechat_refund_id,
            "notify_id": notify_id,
            "fail_reason": fail_reason,
            "id": refund["id"],
        },
    )


async def _enqueue_revoke(
    db: AsyncSession,
    *,
    order: Mapping[str, Any],
    refund_no: str,
    reason: str,
    refund_amount: Decimal,
) -> None:
    payload = json.dumps(
        {
            "refund_no": refund_no,
            "reason": reason,
            "refund_amount": str(refund_amount),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    await db.execute(
        text(
            """INSERT INTO payment_fulfillment_job
               (biz_key, order_id, order_no, order_type, job_type,
                status, next_retry_at, payload_json)
               VALUES (:biz_key, :order_id, :order_no, :order_type, 'REVOKE',
                       'PENDING', UTC_TIMESTAMP(), :payload)
               ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""
        ),
        {
            "biz_key": f"REVOKE:{refund_no}",
            "order_id": int(order["id"]),
            "order_no": str(order["order_no"]),
            "order_type": int(order["type"]),
            "payload": payload,
        },
    )


async def _mark_retry(db: AsyncSession, refund_no: str, error: str) -> None:
    """微信超时/5xx：保留 CREATED，增加重试计数与下次重试时间。"""
    try:
        async with db.begin():
            await db.execute(
                text(
                    """UPDATE payment_refund
                       SET attempt_count = attempt_count + 1,
                           next_retry_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 60 SECOND),
                           fail_reason = :error,
                           updated_at = UTC_TIMESTAMP()
                       WHERE refund_no = :refund_no AND status = 'CREATED'"""
                ),
                {"refund_no": refund_no, "error": error[:255]},
            )
    except Exception:
        logger.exception("payment_refund_mark_retry_failed refund_no=%s", refund_no)


async def _mark_failed(db: AsyncSession, refund_no: str, error: str) -> None:
    """微信明确 4xx：标记本地退款失败并告警。"""
    try:
        async with db.begin():
            await db.execute(
                text(
                    """UPDATE payment_refund
                       SET status = 'FAILED', fail_reason = :error,
                           updated_at = UTC_TIMESTAMP()
                       WHERE refund_no = :refund_no AND status IN ('CREATED', 'PROCESSING')"""
                ),
                {"refund_no": refund_no, "error": error[:255]},
            )
        logger.error("payment_refund_failed refund_no=%s error=%s", refund_no, error)
    except Exception:
        logger.exception("payment_refund_mark_failed_error refund_no=%s", refund_no)


def _map_refund_status(status: str | None) -> str:
    """微信退款状态 -> 本地状态。"""
    value = str(status or "").upper()
    if value == "SUCCESS":
        return "SUCCESS"
    if value == "PROCESSING":
        return "PROCESSING"
    if value == "CLOSED":
        return "CLOSED"
    if value == "ABNORMAL":
        return "FAILED"
    return "PROCESSING"


def _new_refund_no() -> str:
    return f"RF{datetime.now(UTC):%Y%m%d%H%M%S}{secrets.token_hex(4).upper()}"


def _refund_result(refund: Mapping[str, Any]) -> RefundResult:
    return RefundResult(
        refund_no=str(refund["refund_no"]),
        wechat_refund_id=refund.get("wechat_refund_id"),
        status=str(refund["status"]),
        refund_type=str(refund.get("refund_type") or "FULL"),
        order_no=refund.get("order_no"),
        amount=(
            Decimal(str(refund["refund_amount"]))
            if refund.get("refund_amount") is not None
            else None
        ),
        raw=dict(refund),
    )

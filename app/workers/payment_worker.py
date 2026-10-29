"""微信支付履约 Worker。

消费 ``payment_fulfillment_job``：
- ``PENDING/FAILED`` 到重试时间后领取；
- 租约过期的 ``PROCESSING`` 任务也可被重新领取（Worker 崩溃恢复）；
- 每个任务在独立事务中执行 Handler，成功/失败分别回写任务与订单状态；
- 单个任务失败不会让 Worker 退出。

运行方式::

    python -m app.workers.payment_worker --once
    python -m app.workers.payment_worker --once --limit 50
    python -m app.workers.payment_worker --interval 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_factory
from app.services.payment.base import PaymentConfigError
from app.services.payment.fulfillment import (
    FulfillmentError,
    PermanentFulfillmentError,
    UnsupportedOrderType,
    get_handler,
    registered_order_types,
)
from app.services.payment.refunds import RefundService
from app.services.payment.wechat_v3 import build_payment_runtime

logger = logging.getLogger(__name__)

LEASE_SECONDS = 60
MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
_ERROR_MAX_LENGTH = 500
REFUND_RETRY_INTERVAL_SECONDS = 60


def worker_id() -> str:
    """稳定的 Worker 标识：主机名 + 进程号。"""
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_jobs(
    db: AsyncSession,
    worker: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """领取待处理任务并标记 PROCESSING。

    使用 ``FOR UPDATE SKIP LOCKED`` 让多个 Worker 不会抢到同一行；
    租约过期的 PROCESSING 行也允许重新领取，避免 Worker 崩溃后任务卡死。
    """
    lease_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=LEASE_SECONDS)
    result = await db.execute(
        text(
            """SELECT id, biz_key, order_id, order_no, order_type, job_type,
                      status, attempt_count, payload_json
               FROM payment_fulfillment_job
               WHERE (
                   status IN ('PENDING', 'FAILED')
                   AND (next_retry_at IS NULL OR next_retry_at <= UTC_TIMESTAMP())
               ) OR (
                   status = 'PROCESSING'
                   AND lease_expire_at IS NOT NULL
                   AND lease_expire_at < UTC_TIMESTAMP()
               )
               ORDER BY id
               LIMIT :limit
               FOR UPDATE SKIP LOCKED"""
        ),
        {"limit": int(limit)},
    )
    rows = [dict(row) for row in result.mappings().all()]
    for row in rows:
        await db.execute(
            text(
                """UPDATE payment_fulfillment_job
                   SET status = 'PROCESSING',
                       lease_owner = :worker,
                       lease_expire_at = :lease_until,
                       updated_at = UTC_TIMESTAMP()
                   WHERE id = :id"""
            ),
            {"worker": worker, "lease_until": lease_until, "id": row["id"]},
        )
    await db.commit()
    return rows


async def _load_order(db: AsyncSession, order_id: int) -> Mapping[str, Any] | None:
    result = await db.execute(
        text(
            """SELECT id, order_no, user_id, type, amount, status,
                      product_id, product_type, product_name,
                      service_product_id, transaction_id, paid_amount,
                      channel, fulfillment_status
               FROM payment_order
               WHERE id = :id"""
        ),
        {"id": order_id},
    )
    return result.mappings().first()


async def _execute_job(db: AsyncSession, job: Mapping[str, Any]) -> dict[str, Any]:
    """加载订单并执行 Handler；不 commit。"""
    order = await _load_order(db, int(job["order_id"]))
    if not order:
        raise PermanentFulfillmentError(f"支付订单不存在：order_id={job['order_id']}")
    if int(order["status"]) != 1:
        raise PermanentFulfillmentError(
            f"订单状态不是已支付：order_id={job['order_id']} status={order['status']}"
        )

    handler = get_handler(int(order["type"]))
    if str(job["job_type"]).upper() == "FULFILL":
        return await handler.fulfill(db, order)

    # REVOKE 任务由退款流程投递；payload 中带 refund_no/reason 等字段。
    refund = _load_payload(job)
    await handler.revoke(db, order, refund)
    return {"revoked": True, "order_id": int(order["id"])}


def _load_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    raw = job.get("payload_json")
    if raw is None:
        raise PermanentFulfillmentError("REVOKE 任务缺少退款 payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentFulfillmentError("REVOKE 任务 payload 不是合法 JSON") from exc
        if isinstance(value, dict):
            return value
    raise PermanentFulfillmentError("REVOKE 任务 payload 必须是对象")


async def _mark_success(
    db: AsyncSession,
    job: Mapping[str, Any],
    worker: str,
    result: Mapping[str, Any],
) -> None:
    updated = await db.execute(
        text(
            """UPDATE payment_fulfillment_job
               SET status = 'SUCCESS', last_error = NULL, next_retry_at = NULL,
                   lease_owner = NULL, lease_expire_at = NULL,
                   updated_at = UTC_TIMESTAMP()
               WHERE id = :id AND status = 'PROCESSING' AND lease_owner = :worker"""
        ),
        {"id": job["id"], "worker": worker},
    )
    if updated.rowcount != 1:
        raise FulfillmentError(f"任务租约已失效，拒绝覆盖：job_id={job['id']}")
    await db.execute(
        text(
            """UPDATE payment_order
               SET fulfillment_status = 'SUCCESS', fulfillment_error = NULL
               WHERE id = :order_id AND fulfillment_status <> 'SUCCESS'"""
        ),
        {"order_id": job["order_id"]},
    )
    logger.info(
        "payment_fulfillment_success job_id=%s order_id=%s order_type=%s result=%s",
        job["id"],
        job["order_id"],
        job["order_type"],
        result,
    )


async def _mark_failure(
    db: AsyncSession,
    job: Mapping[str, Any],
    worker: str,
    exc: Exception,
) -> None:
    attempt_count = int(job.get("attempt_count") or 0) + 1
    permanent = isinstance(exc, (PermanentFulfillmentError, UnsupportedOrderType))
    dead = permanent or attempt_count >= MAX_ATTEMPTS
    status = "DEAD" if dead else "FAILED"
    next_retry_at = None
    if not dead:
        delay = min(
            BASE_BACKOFF_SECONDS * (2 ** (attempt_count - 1)),
            MAX_BACKOFF_SECONDS,
        )
        next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=delay)

    error = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_LENGTH]
    updated = await db.execute(
        text(
            """UPDATE payment_fulfillment_job
               SET status = :status, attempt_count = :attempt_count,
                   next_retry_at = :next_retry_at, last_error = :error,
                   lease_owner = NULL, lease_expire_at = NULL,
                   updated_at = UTC_TIMESTAMP()
               WHERE id = :id AND status = 'PROCESSING' AND lease_owner = :worker"""
        ),
        {
            "status": status,
            "attempt_count": attempt_count,
            "next_retry_at": next_retry_at,
            "error": error,
            "id": job["id"],
            "worker": worker,
        },
    )
    if updated.rowcount != 1:
        logger.warning(
            "payment_fulfillment_failure_lease_lost job_id=%s worker=%s",
            job["id"],
            worker,
        )
        return
    await db.execute(
        text(
            """UPDATE payment_order
               SET fulfillment_status = :status, fulfillment_error = :error
               WHERE id = :order_id AND fulfillment_status <> 'SUCCESS'"""
        ),
        {"status": status, "error": error, "order_id": job["order_id"]},
    )
    if dead:
        # 生产告警入口：日志采集按 payment_fulfillment_dead 关键字告警。
        logger.error(
            "payment_fulfillment_dead job_id=%s order_id=%s order_type=%s attempt=%s error=%s",
            job["id"],
            job["order_id"],
            job["order_type"],
            attempt_count,
            error,
        )
    else:
        logger.warning(
            "payment_fulfillment_retry job_id=%s order_id=%s attempt=%s next_retry_at=%s error=%s",
            job["id"],
            job["order_id"],
            attempt_count,
            next_retry_at,
            error,
        )


async def process_job(
    db: AsyncSession,
    job: Mapping[str, Any],
    worker: str,
) -> str:
    """执行单个任务；返回 success/failed/dead，不向上抛异常。"""
    try:
        async with db.begin():
            result = await _execute_job(db, job)
            await _mark_success(db, job, worker, result)
        return "success"
    except Exception as exc:  # noqa: BLE001 - 单任务失败不能拖垮 Worker
        await db.rollback()
        try:
            async with db.begin():
                await _mark_failure(db, job, worker, exc)
        except Exception:
            logger.exception(
                "payment_fulfillment_failure_update_failed job_id=%s",
                job["id"],
            )
        if isinstance(exc, (PermanentFulfillmentError, UnsupportedOrderType)):
            return "dead"
        if int(job.get("attempt_count") or 0) + 1 >= MAX_ATTEMPTS:
            return "dead"
        return "failed"


async def run_once(worker: str, limit: int = 20) -> dict[str, int]:
    """领取并处理一轮履约任务。"""
    if session_factory is None:
        raise RuntimeError("数据库驱动未安装，无法启动 payment worker")

    stats = {"claimed": 0, "success": 0, "failed": 0, "dead": 0}
    async with session_factory() as db:
        jobs = await claim_jobs(db, worker, limit)
        stats["claimed"] = len(jobs)
        for job in jobs:
            outcome = await process_job(db, job, worker)
            stats[outcome] += 1
    return stats


async def run_refund_retry_once(limit: int = 20) -> dict[str, int]:
    """查询未完成退款；WECHAT_PAY_MODE 未启用时安全跳过。"""
    if session_factory is None:
        raise RuntimeError("数据库驱动未安装，无法启动 payment worker")
    try:
        runtime = build_payment_runtime()
    except PaymentConfigError as exc:
        logger.info("payment_refund_retry_skipped reason=%s", exc)
        return {"claimed": 0, "success": 0, "retry": 0, "failed": 0}

    try:
        service = RefundService(runtime.gateway)
        async with session_factory() as db:
            return await service.retry_pending(db, limit)
    finally:
        await runtime.gateway.aclose()


async def _run_forever(worker: str, limit: int, interval: float) -> None:
    logger.info(
        "payment_worker_start worker=%s limit=%s interval=%s registered_types=%s",
        worker,
        limit,
        interval,
        registered_order_types(),
    )
    last_refund_retry_at = float("-inf")
    while True:
        try:
            stats = await run_once(worker, limit)
            logger.info("payment_worker_round %s", stats)
        except Exception:
            logger.exception("payment_worker_round_failed")

        if time.monotonic() - last_refund_retry_at >= REFUND_RETRY_INTERVAL_SECONDS:
            try:
                refund_stats = await run_refund_retry_once(limit)
                logger.info("payment_refund_retry_round %s", refund_stats)
            except Exception:
                logger.exception("payment_refund_retry_round_failed")
            last_refund_retry_at = time.monotonic()

        await asyncio.sleep(interval)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="微信支付履约 Worker")
    parser.add_argument("--once", action="store_true", help="运行单轮后退出")
    parser.add_argument("--limit", type=int, default=20, help="单轮领取任务上限")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="循环模式两轮之间的间隔秒数",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印已注册 Handler，不访问数据库",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.dry_run:
        print(f"registered_order_types={registered_order_types()}")
        return 0

    current_worker = worker_id()
    if args.once:
        stats = asyncio.run(run_once(current_worker, args.limit))
        try:
            refund_stats = asyncio.run(run_refund_retry_once(args.limit))
        except Exception:
            logger.exception("payment_refund_retry_once_failed")
            refund_stats = {"claimed": 0, "success": 0, "retry": 0, "failed": 0}
        print(
            "claimed=%d success=%d failed=%d dead=%d "
            "refund_claimed=%d refund_success=%d refund_retry=%d refund_failed=%d"
            % (
                stats["claimed"],
                stats["success"],
                stats["failed"],
                stats["dead"],
                refund_stats["claimed"],
                refund_stats["success"],
                refund_stats["retry"],
                refund_stats["failed"],
            )
        )
        return 0

    try:
        asyncio.run(_run_forever(current_worker, args.limit, args.interval))
    except KeyboardInterrupt:
        logger.info("payment_worker_stopped worker=%s", current_worker)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(main())

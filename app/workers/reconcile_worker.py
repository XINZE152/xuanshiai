"""微信支付账单对账 CLI / 定时 Worker。

用法::

    python -m app.workers.reconcile_worker --once
    python -m app.workers.reconcile_worker --once --bill-date 2026-10-29
    python -m app.workers.reconcile_worker --interval 86400

默认核对 UTC+8（商户本地时区）昨日的交易账单。生产建议由 systemd timer 或
现有调度器每天调用 ``--once``，不要与 API 进程共用事件循环。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from app.db.session import session_factory
from app.services.payment.reconcile import ReconcileService
from app.services.payment.wechat_v3 import build_payment_runtime

logger = logging.getLogger(__name__)


async def run_once(bill_date: date) -> dict:
    if session_factory is None:
        raise RuntimeError("数据库驱动未安装，无法执行对账")

    runtime = build_payment_runtime()
    try:
        service = ReconcileService(runtime.gateway)
        async with session_factory() as db:
            return await service.download_and_reconcile_bill(db, bill_date)
    finally:
        await runtime.gateway.aclose()


def _yesterday_bill_date() -> date:
    """微信账单日期按 UTC+8；返回商户本地时区的昨日。"""
    return (datetime.now(UTC) + timedelta(hours=8)).date() - timedelta(days=1)


async def _run_forever(bill_date: date | None, interval: float) -> None:
    while True:
        target = bill_date or _yesterday_bill_date()
        try:
            summary = await run_once(target)
            logger.info("payment_reconcile_round %s", summary)
        except Exception:
            logger.exception("payment_reconcile_round_failed bill_date=%s", target)
        await asyncio.sleep(interval)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="微信支付账单对账")
    parser.add_argument("--once", action="store_true", help="运行一次后退出")
    parser.add_argument(
        "--bill-date",
        default=None,
        help="账单日期 YYYY-MM-DD，默认 UTC 昨日",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=86400.0,
        help="循环模式间隔秒数，默认 86400",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bill_date = date.fromisoformat(args.bill_date) if args.bill_date else None

    if args.once:
        target = bill_date or _yesterday_bill_date()
        summary = asyncio.run(run_once(target))
        print(
            "bill_date=%(bill_date)s batch_id=%(batch_id)s "
            "record_count=%(record_count)s diff_count=%(diff_count)s "
            "status=%(status)s" % summary
        )
        return 0

    try:
        asyncio.run(_run_forever(bill_date, args.interval))
    except KeyboardInterrupt:
        logger.info("payment_reconcile_stopped")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(main())

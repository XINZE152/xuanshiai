"""微信支付基础对账框架。

流程：
1. 读取（或跳过）已完成的批次；
2. 调微信获取交易账单下载链接并下载（外部 HTTP 不在数据库事务内）；
3. 解压/解码 CSV，按表头解析；
4. 与本地 ``payment_order`` / ``payment_refund`` 比对；
5. 写入 ``payment_reconcile_batch`` 与 ``payment_reconcile_diff``。

账单格式在不同微信版本/商户类型下可能有列增减，因此解析器**按表头名取列**，
不使用固定下标；缺列/空值/非法金额都按容错处理，不因一行脏数据中断整批。
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.wechat_v3 import WechatPayV3Client

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_BILL_TYPE = "TRADE"
_CHANNEL = "wechat"
# 微信账单日期按商户本地时区（中国大陆为 UTC+8）生成；本地 pay_time 存 UTC。
_BILL_TZ_OFFSET_HOURS = 8


class ReconcileError(Exception):
    """账单下载/解析/对账失败。"""


class ReconcileService:
    """交易账单下载与基础差异识别。"""

    def __init__(self, gateway: WechatPayV3Client) -> None:
        self.gateway = gateway

    async def download_and_reconcile_bill(
        self,
        db: AsyncSession,
        bill_date: date | str,
    ) -> dict[str, Any]:
        """下载并核对指定日期的交易账单。"""
        bill_date_value = _normalize_bill_date(bill_date)
        existing = await db.execute(
            text(
                """SELECT id, status, record_count, diff_count, total_amount
                   FROM payment_reconcile_batch
                   WHERE bill_date = :bill_date
                     AND bill_type = :bill_type
                     AND channel = :channel"""
            ),
            {"bill_date": bill_date_value, "bill_type": _BILL_TYPE, "channel": _CHANNEL},
        )
        batch = existing.mappings().first()
        # 结束读事务，确保后面的 HTTP 调用不在事务内。
        await db.commit()
        if batch and str(batch["status"]) == "RECONCILED":
            return _summary(batch_date=bill_date_value, batch=batch)

        try:
            download_url = await self.gateway.get_bill_download_url(
                bill_date_value.isoformat(),
                _BILL_TYPE,
            )
            raw = await self.gateway.download_bill(download_url)
            text_data = _decode_bill(raw)
            rows = _parse_trade_bill(text_data)
        except Exception as exc:
            await _record_batch_failure(db, bill_date_value, str(exc))
            raise

        file_sha256 = hashlib.sha256(raw).hexdigest()
        total_amount = sum((row["order_amount"] or Decimal("0")) for row in rows)
        file_name = f"tradebill_{bill_date_value.isoformat()}.csv"

        async with db.begin():
            batch_id = await _upsert_batch(
                db,
                bill_date=bill_date_value,
                file_name=file_name,
                file_sha256=file_sha256,
                record_count=len(rows),
                total_amount=total_amount,
            )
            await db.execute(
                text("DELETE FROM payment_reconcile_diff WHERE batch_id = :batch_id"),
                {"batch_id": batch_id},
            )
            diffs = await _build_diffs(
                db,
                batch_id=batch_id,
                bill_date=bill_date_value,
                rows=rows,
            )
            for diff in diffs:
                await db.execute(
                    text(
                        """INSERT INTO payment_reconcile_diff
                           (batch_id, bill_date, diff_type, order_no, refund_no,
                            transaction_id, local_amount, remote_amount,
                            local_status, remote_status, resolution, remark)
                           VALUES (:batch_id, :bill_date, :diff_type, :order_no,
                                   :refund_no, :transaction_id, :local_amount,
                                   :remote_amount, :local_status, :remote_status,
                                   'OPEN', :remark)"""
                    ),
                    {**diff, "batch_id": batch_id, "bill_date": bill_date_value},
                )
            await db.execute(
                text(
                    """UPDATE payment_reconcile_batch
                       SET status = 'RECONCILED', record_count = :record_count,
                           total_amount = :total_amount, diff_count = :diff_count,
                           error = NULL, updated_at = UTC_TIMESTAMP()
                       WHERE id = :id"""
                ),
                {
                    "record_count": len(rows),
                    "total_amount": total_amount,
                    "diff_count": len(diffs),
                    "id": batch_id,
                },
            )

        logger.info(
            "payment_reconcile_done bill_date=%s record_count=%s diff_count=%s",
            bill_date_value,
            len(rows),
            len(diffs),
        )
        return {
            "bill_date": bill_date_value.isoformat(),
            "batch_id": batch_id,
            "record_count": len(rows),
            "diff_count": len(diffs),
            "status": "RECONCILED",
        }


def _normalize_bill_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ReconcileError("账单日期必须是 YYYY-MM-DD") from exc


def _decode_bill(raw: bytes) -> str:
    """解压 + 解码；微信账单可能是 gzip，编码通常 UTF-8（带 BOM）。"""
    payload = raw
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise ReconcileError("微信账单 gzip 解压失败") from exc
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # 极少数历史账单为 GB18030；用 replace 防止单行乱码中断整批。
        logger.warning("微信账单 UTF-8 解码失败，回退 GB18030")
        return payload.decode("gb18030", errors="replace")


def _parse_trade_bill(text_data: str) -> list[dict[str, Any]]:
    """按表头解析交易账单。

    容错策略：
    - 允许表头前有说明行；
    - 按列名而不是下标取字段；
    - 空行/缺列/非法金额跳过并记录 warning；
    - 底部汇总行没有商户订单号，直接跳过。
    """
    lines = [line for line in text_data.splitlines() if line.strip()]
    if not lines:
        return []

    header_index = None
    for index, line in enumerate(lines):
        if "商户订单号" in line and ("交易状态" in line or "订单金额" in line):
            header_index = index
            break
    if header_index is None:
        raise ReconcileError("未找到微信账单表头（缺少商户订单号/交易状态）")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    if not reader.fieldnames:
        return []

    fieldnames = [(name or "").strip().lstrip("\ufeff") for name in reader.fieldnames]
    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        row = {
            fieldnames[index]: value
            for index, value in enumerate(raw_row.values())
            if index < len(fieldnames)
        }
        order_no = _text(row.get("商户订单号"))
        if not order_no:
            # 底部汇总行或空行。
            continue
        remote_status = _text(row.get("交易状态")) or ""
        order_amount = _decimal(
            row.get("订单金额") or row.get("应结订单金额") or row.get("应结订单金额(元)")
        )
        if order_amount is None:
            logger.warning("微信账单行金额无法解析 order_no=%s", order_no)
            continue
        refund_no = _text(row.get("商户退款单号"))
        if refund_no and refund_no in {"0", "0.0"}:
            refund_no = None
        rows.append(
            {
                "order_no": order_no,
                "transaction_id": _text(row.get("微信订单号")),
                "remote_status": remote_status,
                "order_amount": order_amount,
                "refund_no": refund_no,
                "refund_amount": _decimal(row.get("退款金额")) or Decimal("0"),
                "refund_status": _text(row.get("退款状态")),
            }
        )
    return rows


async def _upsert_batch(
    db: AsyncSession,
    *,
    bill_date: date,
    file_name: str,
    file_sha256: str,
    record_count: int,
    total_amount: Decimal,
) -> int:
    result = await db.execute(
        text(
            """INSERT INTO payment_reconcile_batch
               (bill_date, bill_type, channel, file_name, file_sha256,
                record_count, total_amount, status, diff_count)
               VALUES (:bill_date, :bill_type, :channel, :file_name, :file_sha256,
                       :record_count, :total_amount, 'PARSED', 0)
               ON DUPLICATE KEY UPDATE
                   id = LAST_INSERT_ID(id),
                   file_name = :file_name,
                   file_sha256 = :file_sha256,
                   record_count = :record_count,
                   total_amount = :total_amount,
                   status = 'PARSED',
                   diff_count = 0,
                   error = NULL"""
        ),
        {
            "bill_date": bill_date,
            "bill_type": _BILL_TYPE,
            "channel": _CHANNEL,
            "file_name": file_name,
            "file_sha256": file_sha256,
            "record_count": record_count,
            "total_amount": total_amount,
        },
    )
    batch_id = int(result.lastrowid or 0)
    if batch_id <= 0:
        batch_id = int(
            await db.scalar(
                text(
                    """SELECT id FROM payment_reconcile_batch
                       WHERE bill_date = :bill_date
                         AND bill_type = :bill_type
                         AND channel = :channel"""
                ),
                {"bill_date": bill_date, "bill_type": _BILL_TYPE, "channel": _CHANNEL},
            )
            or 0
        )
    if batch_id <= 0:
        raise ReconcileError("对账批次写入失败")
    return batch_id


async def _build_diffs(
    db: AsyncSession,
    *,
    batch_id: int,
    bill_date: date,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    local_orders = (
        await db.execute(
            text(
                f"""SELECT order_no, amount, status, transaction_id,
                          refund_status, refunded_amount
                   FROM payment_order
                   WHERE channel = :channel
                     AND DATE(DATE_ADD(pay_time, INTERVAL {_BILL_TZ_OFFSET_HOURS} HOUR)) = :bill_date"""
            ),
            {"channel": _CHANNEL, "bill_date": bill_date},
        )
    ).mappings().all()
    local_order_map = {str(row["order_no"]): row for row in local_orders}

    local_refunds = (
        await db.execute(
            text(
                f"""SELECT refund_no, order_no, refund_amount, status
                   FROM payment_refund
                   WHERE channel = :channel
                     AND DATE(DATE_ADD(COALESCE(success_time, created_at),
                                       INTERVAL {_BILL_TZ_OFFSET_HOURS} HOUR)) = :bill_date"""
            ),
            {"channel": _CHANNEL, "bill_date": bill_date},
        )
    ).mappings().all()
    local_refund_map = {str(row["refund_no"]): row for row in local_refunds}

    diffs: list[dict[str, Any]] = []
    remote_order_nos: set[str] = set()
    remote_refund_nos: set[str] = set()

    for row in rows:
        order_no = row["order_no"]
        remote_order_nos.add(order_no)
        local = local_order_map.get(order_no)
        if local is None:
            diffs.append(
                _diff(
                    diff_type="REMOTE_ONLY",
                    order_no=order_no,
                    transaction_id=row["transaction_id"],
                    remote_amount=row["order_amount"],
                    remote_status=row["remote_status"],
                    remark="微信有本地无",
                )
            )
        else:
            local_amount = Decimal(str(local["amount"])).quantize(_CENT)
            remote_amount = row["order_amount"].quantize(_CENT)
            if local_amount != remote_amount:
                diffs.append(
                    _diff(
                        diff_type="AMOUNT_MISMATCH",
                        order_no=order_no,
                        transaction_id=row["transaction_id"],
                        local_amount=local_amount,
                        remote_amount=remote_amount,
                        local_status=str(local["status"]),
                        remote_status=row["remote_status"],
                        remark="订单金额不一致",
                    )
                )
            expected_local_status = _expected_local_status(row["remote_status"])
            if expected_local_status is not None and int(local["status"]) != expected_local_status:
                diffs.append(
                    _diff(
                        diff_type="STATUS_MISMATCH",
                        order_no=order_no,
                        transaction_id=row["transaction_id"],
                        local_amount=local_amount,
                        remote_amount=remote_amount,
                        local_status=str(local["status"]),
                        remote_status=row["remote_status"],
                        remark="订单状态不一致",
                    )
                )

        refund_no = row.get("refund_no")
        if not refund_no and (row.get("refund_amount") or Decimal("0")) <= 0:
            continue
        if refund_no:
            remote_refund_nos.add(refund_no)
        local_refund = local_refund_map.get(refund_no) if refund_no else None
        if local_refund is None:
            diffs.append(
                _diff(
                    diff_type="REFUND_MISMATCH",
                    order_no=order_no,
                    refund_no=refund_no,
                    transaction_id=row["transaction_id"],
                    local_amount=None,
                    remote_amount=row.get("refund_amount"),
                    remote_status=row.get("refund_status"),
                    remark="微信有退款本地无",
                )
            )
        else:
            local_refund_amount = Decimal(str(local_refund["refund_amount"])).quantize(_CENT)
            remote_refund_amount = (row.get("refund_amount") or Decimal("0")).quantize(_CENT)
            if local_refund_amount != remote_refund_amount:
                diffs.append(
                    _diff(
                        diff_type="REFUND_MISMATCH",
                        order_no=order_no,
                        refund_no=refund_no,
                        transaction_id=row["transaction_id"],
                        local_amount=local_refund_amount,
                        remote_amount=remote_refund_amount,
                        local_status=str(local_refund["status"]),
                        remote_status=row.get("refund_status"),
                        remark="退款金额不一致",
                    )
                )

    for order_no, local in local_order_map.items():
        if order_no not in remote_order_nos:
            diffs.append(
                _diff(
                    diff_type="LOCAL_ONLY",
                    order_no=order_no,
                    transaction_id=local["transaction_id"],
                    local_amount=Decimal(str(local["amount"])).quantize(_CENT),
                    local_status=str(local["status"]),
                    remark="本地有微信无",
                )
            )

    for refund_no, local in local_refund_map.items():
        if refund_no not in remote_refund_nos:
            diffs.append(
                _diff(
                    diff_type="REFUND_MISMATCH",
                    order_no=local["order_no"],
                    refund_no=refund_no,
                    local_amount=Decimal(str(local["refund_amount"])).quantize(_CENT),
                    local_status=str(local["status"]),
                    remark="本地有退款微信无",
                )
            )
    return diffs


def _expected_local_status(remote_status: str) -> int | None:
    value = (remote_status or "").upper()
    if value == "SUCCESS":
        return 1
    if value == "REFUND":
        return 3
    return None


def _diff(
    *,
    diff_type: str,
    order_no: str | None = None,
    refund_no: str | None = None,
    transaction_id: str | None = None,
    local_amount: Decimal | None = None,
    remote_amount: Decimal | None = None,
    local_status: str | None = None,
    remote_status: str | None = None,
    remark: str = "",
) -> dict[str, Any]:
    return {
        "diff_type": diff_type,
        "order_no": order_no,
        "refund_no": refund_no,
        "transaction_id": transaction_id,
        "local_amount": local_amount,
        "remote_amount": remote_amount,
        "local_status": local_status,
        "remote_status": remote_status,
        "remark": remark[:255],
    }


async def _record_batch_failure(
    db: AsyncSession,
    bill_date: date,
    error: str,
) -> None:
    """下载/解析失败时记录 FAILED 批次，便于后台看到失败日期。"""
    try:
        await db.rollback()
        async with db.begin():
            await db.execute(
                text(
                    """INSERT INTO payment_reconcile_batch
                       (bill_date, bill_type, channel, status, error)
                       VALUES (:bill_date, :bill_type, :channel, 'FAILED', :error)
                       ON DUPLICATE KEY UPDATE
                           status = 'FAILED', error = :error,
                           updated_at = UTC_TIMESTAMP()"""
                ),
                {
                    "bill_date": bill_date,
                    "bill_type": _BILL_TYPE,
                    "channel": _CHANNEL,
                    "error": error[:500],
                },
            )
    except Exception:
        logger.exception("payment_reconcile_batch_failure_write_failed")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text_value = str(value).strip().replace(",", "")
    if not text_value:
        return None
    try:
        return Decimal(text_value).quantize(_CENT)
    except (InvalidOperation, ValueError):
        return None


def _summary(*, batch_date: date, batch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bill_date": batch_date.isoformat(),
        "batch_id": int(batch["id"]),
        "record_count": int(batch["record_count"] or 0),
        "diff_count": int(batch["diff_count"] or 0),
        "status": str(batch["status"]),
    }

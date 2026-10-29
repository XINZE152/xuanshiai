"""支付回调日志与防重放。

``payment_notify_log.notify_id`` 是唯一键：
- 首次通知：插入日志，允许后续业务处理；
- 已处理成功（processed=1）：直接视为重复通知，不重复履约；
- 上次处理失败（processed=0）：允许微信重试时再次处理。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment.base import PaymentNotification, PaymentValidationError

logger = logging.getLogger(__name__)


class PaymentNotifyLogService:
    """写入/更新 ``payment_notify_log``。"""

    async def record_once(
        self,
        db: AsyncSession,
        *,
        notification: PaymentNotification,
        raw_body: bytes,
        signature_serial: str | None = None,
    ) -> bool:
        """记录通知；返回 True 表示本次应继续处理，False 表示已成功处理过。

        必须在调用方的事务内执行；重复插入使用 SAVEPOINT 隔离，不污染
        外层事务。
        """
        notify_id = (notification.notify_id or "").strip()
        if not notify_id:
            raise PaymentValidationError("支付通知缺少 notify_id")

        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        payload_json = _payload_json(notification)

        # 已存在时锁住该行，避免并发重复处理。
        existing = await db.execute(
            text(
                """SELECT id, processed
                   FROM payment_notify_log
                   WHERE notify_id = :notify_id
                   FOR UPDATE"""
            ),
            {"notify_id": notify_id},
        )
        row = existing.mappings().first()
        if row:
            if int(row["processed"] or 0) == 1:
                return False
            await db.execute(
                text(
                    """UPDATE payment_notify_log
                       SET event_type=:event_type, signature_serial=:signature_serial,
                           body_sha256=:body_sha256, payload_json=:payload_json,
                           verified=1, error=NULL
                       WHERE id=:id"""
                ),
                {
                    "id": row["id"],
                    "event_type": _truncate(notification.event_type, 64),
                    "signature_serial": _truncate(signature_serial, 64),
                    "body_sha256": body_sha256,
                    "payload_json": payload_json,
                },
            )
            return True

        try:
            # SQLAlchemy AsyncSession 的 begin_nested() 是异步上下文管理器，
            # 不能用 ``await db.begin_nested()``；异常时上下文自动回滚 SAVEPOINT，
            # 外层事务仍然可用。
            async with db.begin_nested():
                await db.execute(
                    text(
                        """INSERT INTO payment_notify_log
                           (notify_id, event_type, channel, out_trade_no, transaction_id,
                            refund_no, signature_serial, body_sha256, payload_json,
                            verified, processed)
                           VALUES (:notify_id, :event_type, 'wechat', :out_trade_no,
                                   :transaction_id, :refund_no, :signature_serial,
                                   :body_sha256, :payload_json, 1, 0)"""
                    ),
                    {
                        "notify_id": notify_id,
                        "event_type": _truncate(notification.event_type, 64),
                        "out_trade_no": _truncate(notification.out_trade_no, 64),
                        "transaction_id": _truncate(notification.transaction_id, 64),
                        "refund_no": _truncate(notification.refund_no, 64),
                        "signature_serial": _truncate(signature_serial, 64),
                        "body_sha256": body_sha256,
                        "payload_json": payload_json,
                    },
                )
            return True
        except IntegrityError:
            # 并发通知同时插入：SAVEPOINT 已回滚，再判断对方是否已处理成功。
            current = await db.execute(
                text(
                    """SELECT processed FROM payment_notify_log
                       WHERE notify_id = :notify_id"""
                ),
                {"notify_id": notify_id},
            )
            current_row = current.mappings().first()
            return bool(current_row and int(current_row["processed"] or 0) == 0)

    async def mark_processed(self, db: AsyncSession, notify_id: str) -> None:
        """标记通知已成功处理。"""
        await db.execute(
            text(
                """UPDATE payment_notify_log
                   SET processed=1, processed_at=UTC_TIMESTAMP(), error=NULL
                   WHERE notify_id=:notify_id"""
            ),
            {"notify_id": notify_id},
        )

    async def record_failed(
        self,
        db: AsyncSession,
        *,
        notification: PaymentNotification,
        raw_body: bytes,
        signature_serial: str | None = None,
        error: str,
    ) -> None:
        """记录处理失败；保留 processed=0，允许微信重试。"""
        notify_id = (notification.notify_id or "").strip()
        if not notify_id:
            return
        await db.execute(
            text(
                """INSERT INTO payment_notify_log
                   (notify_id, event_type, channel, out_trade_no, transaction_id,
                    refund_no, signature_serial, body_sha256, payload_json,
                    verified, processed, error)
                   VALUES (:notify_id, :event_type, 'wechat', :out_trade_no,
                           :transaction_id, :refund_no, :signature_serial,
                           :body_sha256, :payload_json, 1, 0, :error)
                   ON DUPLICATE KEY UPDATE
                       error=:error,
                       verified=1,
                       payload_json=:payload_json"""
            ),
            {
                "notify_id": notify_id,
                "event_type": _truncate(notification.event_type, 64),
                "out_trade_no": _truncate(notification.out_trade_no, 64),
                "transaction_id": _truncate(notification.transaction_id, 64),
                "refund_no": _truncate(notification.refund_no, 64),
                "signature_serial": _truncate(signature_serial, 64),
                "body_sha256": hashlib.sha256(raw_body).hexdigest(),
                "payload_json": _payload_json(notification),
                "error": _truncate(error, 500),
            },
        )


def _payload_json(notification: PaymentNotification) -> str:
    """只保存解密后的 resource 明文，便于排障；不保存原始密文和密钥。"""
    raw: Any = notification.raw.get("resource") if notification.raw else {}
    if not isinstance(raw, dict):
        raw = {}
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text_value = str(value)
    return text_value[:max_length] if len(text_value) > max_length else text_value

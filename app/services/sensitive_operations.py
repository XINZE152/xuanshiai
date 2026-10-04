"""敏感后台操作的幂等编排（退款、提现审核、重置密码、资源调整）。

复用 `app/services/idempotency.py` 的持久化幂等记录，把「预留 → 执行 → 完成/中止」
三步收敛到一处，避免每个端点各写一遍：

- 首次请求：预留成功 → 执行业务动作 → 写入响应快照；
- 重复请求（同键同载荷）：直接返回上次的响应，不重复执行；
- 同键不同载荷：409；
- 业务动作抛错：删除预留（允许重试）后把异常抛给调用方。

调用方约定：`begin` 返回 `replayed` 非空时表示命中重放，必须直接返回、
不得再执行业务动作。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.idempotency import (
    IdempotencyReservation,
    abort,
    complete,
    reserve_or_replay,
)


async def begin_sensitive_operation(
    db: AsyncSession,
    *,
    actor_id: int,
    operation: str,
    idempotency_key: str,
    payload: Any,
) -> tuple[IdempotencyReservation | None, dict[str, Any] | None]:
    """预留幂等键；命中重放时返回 (None, 上次响应)。"""
    reservation = await reserve_or_replay(db, actor_id, operation, idempotency_key, payload)
    if reservation.response is not None:
        return None, reservation.response
    return reservation, None


async def finish_sensitive_operation(
    db: AsyncSession,
    reservation: IdempotencyReservation,
    response: dict[str, Any],
) -> None:
    await complete(db, reservation, response)


async def abort_sensitive_operation(
    db: AsyncSession,
    reservation: IdempotencyReservation,
) -> None:
    await abort(db, reservation)

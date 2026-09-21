"""记忆生命周期的两个用户可见入口（修复清单 §3.17.5，决策 2(a)）。

决策 2(a) 要求「暂停使用」与「删除并忘记」是**两个入口、两种生命周期**：

- **暂停使用**（:func:`pause_memory_for_owner`）：只撤销该 owner 的全部投影
  授权，读端立刻不可读（``read_active`` 复核投影授权）；**不删任何数据、不建
  清理任务**。重新授权（consent 重新授予）即一键恢复。
- **删除并忘记**（:func:`forget_memory_for_owner`）：同一事务内撤销全部投影
  授权并创建 ``cleanup`` 任务（``scope="memory"``），由 Worker 执行物理清理；
  **不可恢复**。

两者都**不**删除 ``ai_consent_grant``：撤回记录本身是合规证据（§3.17.1 清单）。
「暂停使用」保留数据正是它与「删除并忘记」的全部差别，因此绝不允许同一入口
既承诺删除又只撤许可。

本模块**绝不** commit：调用方（路由）拥有事务与原子性。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.memory.consent_producers import (
    revoke_projection_dimensions_for_owner,
)
from app.services.ai.memory.purge import current_owner_sequence
from app.services.ai.tasks import CLEANUP_TASK_TYPE, enqueue_task
from app.services.revisions import RevisionVector

logger = logging.getLogger(__name__)

__all__ = [
    "FORGET_CLEANUP_SCOPE",
    "MemoryForgetResult",
    "forget_memory_for_owner",
    "pause_memory_for_owner",
]

#: 「删除并忘记」使用的清理 scope（``purge_ai_resources`` / ``cleanup_handler``）。
FORGET_CLEANUP_SCOPE = "memory"


class MemoryForgetResult:
    """「删除并忘记」的同步半部结果（异步物理清理由 cleanup 任务完成）。"""

    __slots__ = ("task_id", "revoked_dimensions", "fence_seq")

    def __init__(
        self, *, task_id: str, revoked_dimensions: int, fence_seq: int
    ) -> None:
        self.task_id = task_id
        self.revoked_dimensions = revoked_dimensions
        self.fence_seq = fence_seq


def _forget_resource_id(owner_user_id: int) -> str:
    return f"{FORGET_CLEANUP_SCOPE}:{owner_user_id}"


def _forget_request_digest(owner_user_id: int) -> str:
    """请求摘要：同一 owner 的重复 forget 视为同一请求（可回放同一任务）。"""
    return hashlib.sha256(
        f"memory-forget:{owner_user_id}".encode("utf-8")
    ).hexdigest()


def _forget_task_idempotency_key(idempotency_key: str) -> str:
    """派生任务幂等键（``ai_task.idempotency_key`` 上限 128 字符）。

    与 ``_enqueue_consent_cleanup`` 同法：先对完整键做 SHA256 再截断，避免长键
    直接截断后碰撞（前 128 字符相同但尾部不同的键会坍缩成同一任务）。
    """
    return hashlib.sha256(
        f"memory:forget:{idempotency_key}".encode("utf-8")
    ).hexdigest()[:128]


async def pause_memory_for_owner(db: AsyncSession, owner_user_id: int) -> int:
    """「暂停使用」：撤销全部投影授权，数据保留、可一键恢复；返回撤销维度数。

    幂等：无 active 授权时返回 0（读取端本就不可读）。不建清理任务、不删数据、
    不动 ``ai_consent_grant``——重新授权后生产者会重新授予维度，读取即恢复。
    """
    revoked = await revoke_projection_dimensions_for_owner(
        db, owner_user_id=owner_user_id
    )
    logger.info("memory_pause_owner owner=%s revoked=%s", owner_user_id, revoked)
    return revoked


async def forget_memory_for_owner(
    db: AsyncSession,
    owner_user_id: int,
    *,
    idempotency_key: str,
) -> MemoryForgetResult:
    """「删除并忘记」：同步撤全部读取许可 + 建 ``cleanup`` 任务（不可恢复）。

    同一事务内先撤销全部投影授权（同步立刻不可读），再幂等入队 ``cleanup``
    任务并由 ``payload_summary`` 声明 ``scope="memory"``、
    ``resource_id="memory:{owner}"`` 与 ``fence_seq``；物理清理由 Worker 的
    ``cleanup_handler`` 在 ``purge_ai_resources(scope="memory")`` 中完成。

    ``fence_seq`` 在**本事务内**读取并冻结进任务 payload：它是撤回时刻的序号
    水位，清理只删除 ``<= fence_seq`` 的行。若改为在清理执行时读取，期间用户
    可能已重新授权并写入新记忆，那些新行会被一并删除且不可恢复。

    重复请求（同一 Idempotency-Key）回放同一任务，不重复撤销。
    """
    task_key = _forget_task_idempotency_key(idempotency_key)
    request_digest = _forget_request_digest(owner_user_id)
    fence_seq = await current_owner_sequence(db, owner_user_id)
    task = await enqueue_task(
        db,
        owner_user_id=owner_user_id,
        task_type=CLEANUP_TASK_TYPE,
        idempotency_key=task_key,
        request_hash=request_digest,
        revisions=RevisionVector(),
        consent=None,
    )
    await db.execute(
        text(
            "UPDATE ai_task SET payload_summary = :payload_summary, "
            "updated_at = UTC_TIMESTAMP() WHERE task_id = :task_id"
        ),
        {
            "payload_summary": json.dumps(
                _forget_payload(owner_user_id, fence_seq=fence_seq),
                ensure_ascii=False,
            ),
            "task_id": task.task_id,
        },
    )
    revoked = await revoke_projection_dimensions_for_owner(
        db, owner_user_id=owner_user_id
    )
    logger.info(
        "memory_forget_owner owner=%s task_id=%s fence_seq=%s revoked=%s",
        owner_user_id,
        task.task_id,
        fence_seq,
        revoked,
    )
    return MemoryForgetResult(
        task_id=task.task_id, revoked_dimensions=revoked, fence_seq=fence_seq
    )


def _forget_payload(owner_user_id: int, *, fence_seq: int) -> dict[str, Any]:
    """cleanup 任务 payload（与既有 scope 同构；只含 id/scope/围栏，无用户内容）。"""
    return {
        "scope": FORGET_CLEANUP_SCOPE,
        "resource_id": _forget_resource_id(owner_user_id),
        "version": RevisionVector().as_dict(),
        "fence_seq": int(fence_seq),
        "purge_deadline": (
            datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15)
        ).isoformat(),
    }

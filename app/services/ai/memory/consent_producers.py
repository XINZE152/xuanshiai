"""Consent → ProjectionGrant 授权生产者（Phase 3 Task 1）。

Core v1 账本仍是唯一写入源；本模块只把「现有 consent 动作」翻译成
``ai_memory_projection_grant`` 的授予/撤销，使 Phase 2 投影读路径与
Phase 3 AI 消费者（军师/分身）真正有授权可用：

- ``profile_text_extract`` consent 成功授予 → 为全部消费维度 grant
  （snapshot 由服务端从当前 consent 行派生，客户端不能自行声明）；
- 撤回（公开 API / 画像删除）/ 账号注销 → 同步撤销全部授权并立即失效
  对应 active 投影（read 端另有 consent/snapshot/policy 复核门，双保险）；
- 失败不影响原 consent 状态：安全日志（只含 id/计数）+ outbox 重试事件
  （幂等键 ``consent:{owner}:{scope}:{snapshot_id}:{action}``）。

锁序：grant/revoke 只取 grant 行锁（grant → projection 行），全程不取
owner 序列锁，与 projections 模块的锁序约定一致。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.memory.projections import (
    PROFILE_CONSENT_SCOPE,
    MemoryProjectionService,
    ProjectionGrantDenied,
    ProjectionGrantNotFound,
    derive_consent_snapshot_id,
)
from app.services.derivation_outbox import (
    CleanupHandler,
    DerivationEvent,
    register_cleanup_handler,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CONSENT_PRODUCER_DIMENSIONS",
    "PRODUCER_CONSENT_SCOPE",
    "PRODUCER_RETRY_EVENT_TYPE",
    "enqueue_projection_producer_retry",
    "grant_projection_dimensions_for_consent",
    "handle_projection_producer_retry",
    "revoke_projection_dimensions_for_owner",
    "run_projection_producer_safely",
]

PRODUCER_CONSENT_SCOPE = PROFILE_CONSENT_SCOPE

# 授权生产者覆盖的全部消费维度。Phase 2 下游（search/compatibility/recommend）
# 与 Phase 3 消费者（counselor_context / persona_context）一次授齐；重授时
# upsert 幂等复活，不产生重复行。
CONSENT_PRODUCER_DIMENSIONS: tuple[dict[str, str], ...] = (
    {"function_key": "search", "purpose": "candidate_filter", "data_category": "personal_profile"},
    {"function_key": "compatibility", "purpose": "candidate_rank", "data_category": "compatibility_features"},
    {"function_key": "recommend", "purpose": "candidate_rank", "data_category": "ideal_partner_preference"},
    {"function_key": "counselor_context", "purpose": "session_context", "data_category": "personal_profile"},
    {"function_key": "counselor_context", "purpose": "session_context", "data_category": "ideal_partner_preference"},
    {"function_key": "counselor_context", "purpose": "explanation", "data_category": "personal_profile"},
    {"function_key": "counselor_context", "purpose": "explanation", "data_category": "ideal_partner_preference"},
    {"function_key": "persona_context", "purpose": "session_context", "data_category": "public_profile_summary"},
)

# outbox 重试事件类型（event_type 列 varchar(64)，26 字符）。
PRODUCER_RETRY_EVENT_TYPE = "memory_projection_producer"

_SQL_ACTIVE_CONSENT_READ = (
    "SELECT scope, version, policy_revision, granted_at FROM ai_consent_grant "
    "WHERE user_id = :user_id AND scope = :scope AND revoked_at IS NULL "
    "ORDER BY granted_at DESC LIMIT 1"
)
_SQL_ACTIVE_GRANT_SCAN = (
    "SELECT grant_id, function_key, purpose, data_category FROM "
    "ai_memory_projection_grant WHERE owner_user_id = :owner_user_id "
    "AND status = 'active'"
)
_SQL_RETRY_ENQUEUE = (
    "INSERT INTO derivation_outbox "
    "(event_id, aggregate_type, aggregate_id, event_type, changed_fields, "
    "source_revision_json, privacy_revision, payload_minimal, priority, published_at) "
    "VALUES (:event_id, 'ai_memory', :aggregate_id, :event_type, '[]', "
    ":source_revision_json, :privacy_revision, :payload_minimal, 30, UTC_TIMESTAMP()) "
    "ON DUPLICATE KEY UPDATE event_id = event_id"
)


def _producer_idempotency_key(
    owner_user_id: int, scope: str, snapshot_id: str, action: str
) -> str:
    return f"consent:{owner_user_id}:{scope}:{snapshot_id}:{action}"


# ---------------------------------------------------------------------------
# 授予 / 撤销
# ---------------------------------------------------------------------------


async def grant_projection_dimensions_for_consent(
    db: AsyncSession,
    *,
    owner_user_id: int,
    scope: str,
    consent_row: dict[str, Any],
) -> int:
    """consent 授予后为全部消费维度创建 ProjectionGrant；返回授予维度数。

    ``consent_row`` 只用于派生 snapshot 引用；服务端在 grant 内部重读当前
    active consent 并复核 snapshot/policy 一致，陈旧引用直接拒绝。
    非 ``profile_text_extract`` scope 不产生任何授权（返回 0）。
    """

    if scope != PRODUCER_CONSENT_SCOPE:
        return 0
    # 以数据库当前 active consent 作为 snapshot 来源。调用方传入的
    # consent_row 可能来自 INSERT 前的 Python datetime，而 MySQL DATETIME
    # 落库后精度可能不同；若直接用调用方值计算 snapshot，随后 service.grant
    # 的数据库复核会稳定得到 snapshot mismatch。
    current_result = await db.execute(
        text(_SQL_ACTIVE_CONSENT_READ),
        {"user_id": owner_user_id, "scope": scope},
    )
    current_row = current_result.mappings().first()
    if current_row is None:
        raise ProjectionGrantDenied(
            "no active profile_text_extract consent for projection grant"
        )
    current_consent = dict(current_row)
    snapshot_id = derive_consent_snapshot_id(current_consent)
    policy_revision = str(current_consent.get("policy_revision") or "")
    service = MemoryProjectionService(db)
    granted = 0
    for dimension in CONSENT_PRODUCER_DIMENSIONS:
        await service.grant(
            owner_user_id=owner_user_id,
            consent_snapshot_id=snapshot_id,
            policy_revision=policy_revision,
            **dimension,
        )
        granted += 1
    return granted


async def revoke_projection_dimensions_for_owner(
    db: AsyncSession, *, owner_user_id: int
) -> int:
    """撤销 owner 全部 active 投影授权并立即失效对应投影；返回撤销维度数。

    幂等：无 active 授权时返回 0。并发撤销竞态按「谁先锁到行谁生效」
    处理（行已 revoked 时计 0，不报错）。
    """

    rows = (
        await db.execute(text(_SQL_ACTIVE_GRANT_SCAN), {"owner_user_id": owner_user_id})
    ).mappings().all()
    service = MemoryProjectionService(db)
    revoked = 0
    for row in rows:
        dimension = {
            "function_key": str(row["function_key"]),
            "purpose": str(row["purpose"]),
            "data_category": str(row["data_category"]),
        }
        try:
            await service.revoke(owner_user_id=owner_user_id, **dimension)
        except ProjectionGrantNotFound:
            continue
        revoked += 1
    if revoked:
        # 撤权主动失效 AI 分身公开画像缓存（best-effort；Redis 不可用仅记日志，
        # 缓存键含 privacy revision，TTL 到期同样收敛）。
        try:
            from app.services.ai.memory.consumers import invalidate_persona_memory_cache

            await invalidate_persona_memory_cache(target_user_id=owner_user_id)
        except Exception:
            logger.warning(
                "persona_cache_invalidate_on_revoke_failed owner=%s", owner_user_id
            )
    return revoked


# ---------------------------------------------------------------------------
# 安全执行 + outbox 重试
# ---------------------------------------------------------------------------


async def enqueue_projection_producer_retry(
    db: AsyncSession,
    *,
    owner_user_id: int,
    scope: str,
    action: str,
    snapshot_id: str,
    revision: Any = None,
) -> str:
    """producer 失败后的 outbox 重试事件；返回 event_id。

    幂等键 ``consent:{owner}:{scope}:{snapshot_id}:{action}`` 哈希进
    event_id（列 varchar(64)，拼接短随机后缀避免「已消费收据挡住新重试」）。
    payload 只含 action/scope/snapshot_id，不携带任何用户内容。
    """

    key = _producer_idempotency_key(owner_user_id, scope, snapshot_id, action)
    event_id = f"prjp:{hashlib.sha256(key.encode()).hexdigest()[:24]}:{uuid.uuid4().hex[:8]}"
    vector = revision.as_dict() if hasattr(revision, "as_dict") else dict(revision or {})
    privacy = int(vector.get("privacy") or 0)
    payload = {"action": action, "scope": scope, "snapshot_id": snapshot_id}
    await db.execute(
        text(_SQL_RETRY_ENQUEUE),
        {
            "event_id": event_id,
            "aggregate_id": owner_user_id,
            "event_type": PRODUCER_RETRY_EVENT_TYPE,
            "source_revision_json": json.dumps(vector),
            "privacy_revision": privacy,
            "payload_minimal": json.dumps(payload, ensure_ascii=False),
        },
    )
    return event_id


async def run_projection_producer_safely(
    db: AsyncSession,
    *,
    action: str,
    owner_user_id: int,
    scope: str,
    revision: Any = None,
    consent_row: dict[str, Any] | None = None,
) -> bool:
    """事务内同步执行 producer；失败不阻断 consent 状态，转安全日志 + 重试。

    返回 True 表示 producer 本身成功；False 表示失败且已尽力入队重试
    （入队再失败只记日志，consent 主流程继续）。
    """

    try:
        if action == "grant":
            if consent_row is None:
                raise ValueError("grant producer requires the consent row")
            await grant_projection_dimensions_for_consent(
                db, owner_user_id=owner_user_id, scope=scope, consent_row=consent_row
            )
        elif action == "revoke":
            await revoke_projection_dimensions_for_owner(db, owner_user_id=owner_user_id)
        else:
            raise ValueError(f"unknown projection producer action: {action!r}")
        return True
    except Exception:
        logger.warning(
            "memory_projection_producer_failed owner=%s scope=%s action=%s",
            owner_user_id,
            scope,
            action,
            exc_info=True,
        )
        snapshot_id = (
            derive_consent_snapshot_id(consent_row)
            if consent_row
            else "n/a"
        )
        try:
            await enqueue_projection_producer_retry(
                db,
                owner_user_id=owner_user_id,
                scope=scope,
                action=action,
                snapshot_id=snapshot_id,
                revision=revision,
            )
        except Exception:
            logger.exception(
                "memory_projection_producer_retry_enqueue_failed owner=%s action=%s",
                owner_user_id,
                action,
            )
        return False


async def handle_projection_producer_retry(
    db: AsyncSession, event: DerivationEvent
) -> str:
    """cleanup 消费者 handler：重放失败的授权生产者动作（幂等）。"""

    payload = event.payload or {}
    action = str(payload.get("action") or "")
    scope = str(payload.get("scope") or "")
    if (
        event.event_type != PRODUCER_RETRY_EVENT_TYPE
        or action not in {"grant", "revoke"}
        or scope != PRODUCER_CONSENT_SCOPE
    ):
        return "noop"
    owner_user_id = int(event.aggregate_id)
    if action == "revoke":
        await revoke_projection_dimensions_for_owner(db, owner_user_id=owner_user_id)
        return "processed"
    row = (
        await db.execute(
            text(_SQL_ACTIVE_CONSENT_READ),
            {"user_id": owner_user_id, "scope": PRODUCER_CONSENT_SCOPE},
        )
    ).mappings().first()
    if row is None:
        # 授权已不存在（撤销/过期/注销）：无需授予。
        return "noop"
    await grant_projection_dimensions_for_consent(
        db,
        owner_user_id=owner_user_id,
        scope=scope,
        consent_row=dict(row),
    )
    return "processed"


# ---------------------------------------------------------------------------
# 账号注销链：先撤投影授权，再走既有 user/account 清理
# ---------------------------------------------------------------------------

# 既有 user_deleted / account_deleted 清理 handler（purge_ai_resources）。
# 模块导入时 derivation_outbox 已完成自身注册，这里先摘引用再覆盖注册表。
_LEGACY_ACCOUNT_DELETED_HANDLERS: dict[str, CleanupHandler] = {
    "user_deleted": None,  # type: ignore[dict-item]
    "account_deleted": None,  # type: ignore[dict-item]
}


async def _handle_account_deleted_with_producer(
    db: AsyncSession, event: DerivationEvent
) -> str:
    owner_user_id = int(event.aggregate_id)
    await run_projection_producer_safely(
        db, action="revoke", owner_user_id=owner_user_id, scope=PRODUCER_CONSENT_SCOPE
    )
    legacy = _LEGACY_ACCOUNT_DELETED_HANDLERS.get(event.event_type)
    if legacy is not None:
        return str(await legacy(db, event) or "processed")
    return "processed"


def _register_cleanup_handlers() -> None:
    from app.services.derivation_outbox import CLEANUP_HANDLERS

    for event_type in ("user_deleted", "account_deleted"):
        _LEGACY_ACCOUNT_DELETED_HANDLERS[event_type] = CLEANUP_HANDLERS.get(
            event_type
        )  # type: ignore[assignment]
        register_cleanup_handler(
            event_type, _handle_account_deleted_with_producer
        )
    register_cleanup_handler(PRODUCER_RETRY_EVENT_TYPE, handle_projection_producer_retry)


_register_cleanup_handlers()

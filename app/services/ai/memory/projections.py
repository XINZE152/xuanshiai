"""Memory Projection service (Phase 2): grant / revoke / build / read.

The service is the only entry point for projection grants and reads.  Every
gate is re-checked server-side on every call:

- ``grant`` re-reads the active ``profile_text_extract`` consent (same read
  contract as ``consents.py``) and requires the caller to reference the
  *current* snapshot id plus the current policy revision; the grant row is
  idempotent per (owner, function, purpose, data category) — re-granting
  reactivates;
- ``revoke`` is owner-scoped (foreign owners get NotFound) and immediately
  invalidates every active projection in the dimension;
- ``read_active`` re-validates owner, grant, consent, policy revision,
  status and the optional version pin; any failed gate returns ``None`` so
  resource existence never leaks.  Future function keys (AI 军师 / AI 分身)
  fail closed with :class:`ProjectionFeatureNotEnabled`.

Build / invalidation (Task 4) complete the §2 contract.  None of the
functions call ``commit()`` — the caller's transaction owns durability.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_memory_projection import ProjectionDocument
from app.services.ai.memory.projection_policy import (
    ProjectionPolicy,
    ProjectionPolicyDenied,
)

__all__ = [
    "ProjectionContractError",
    "ProjectionGrantDenied",
    "ProjectionGrantNotFound",
    "MemoryProjectionService",
    "derive_consent_snapshot_id",
    "PROFILE_CONSENT_SCOPE",
]

# 复用 consents.py 的读取契约：同一张 ai_consent_grant、同一组列
# （scope/version/policy_revision/granted_at，revoked_at IS NULL 为有效）。
PROFILE_CONSENT_SCOPE = "profile_text_extract"


class ProjectionGrantDenied(Exception):
    """Grant/read 被授权或策略门拒绝（稳定语义，不泄露资源存在性）。"""


class ProjectionContractError(Exception):
    """构建产物违反冻结契约（schema 校验失败）；消息只含计数不携带原文。"""


class ProjectionGrantNotFound(Exception):
    """owner 范围内不存在该维度的授权（owner-scoped 404）。"""


def derive_consent_snapshot_id(consent_row: dict[str, Any]) -> str:
    """服务端派生授权快照 ID：绑定当前 (scope, version, policy_revision, granted_at)。

    调用方传入的 ``consent_snapshot_id`` 必须等于 freshly derived 的值，
    因此 grant/read 永远引用当前快照，过期引用直接拒绝。
    """

    material = [
        str(consent_row.get("scope") or ""),
        str(consent_row.get("version") or ""),
        str(consent_row.get("policy_revision") or ""),
        str(consent_row.get("granted_at") or ""),
    ]
    digest = hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"cs_{digest[:32]}"


def _current_policy_revision() -> str:
    # 延迟导入避免循环（profile.py 的转发路径会引用 memory 包）。
    from app.services.ai.profile import PROFILE_POLICY_REVISION

    return PROFILE_POLICY_REVISION


class MemoryProjectionService:
    """记忆投影服务：grant / revoke / build / read_active / invalidate。"""

    def __init__(self, db: AsyncSession, *, policy_revision: str | None = None) -> None:
        self._db = db
        self._policy_revision = policy_revision

    # ------------------------------------------------------------------
    # SQL（子串路由友好：假会话与真实 DB 走同一语句形状）
    # ------------------------------------------------------------------

    _SQL_CONSENT_READ = (
        "SELECT scope, version, policy_revision, granted_at FROM ai_consent_grant "
        "WHERE user_id = :user_id AND scope = :scope AND revoked_at IS NULL "
        "ORDER BY granted_at DESC LIMIT 1"
    )
    _SQL_GRANT_UPSERT = (
        "INSERT INTO ai_memory_projection_grant "
        "(grant_id, owner_user_id, function_key, purpose, data_category, status, "
        "consent_snapshot_id, policy_revision, granted_at, revoked_at) "
        "VALUES (:grant_id, :owner_user_id, :function_key, :purpose, :data_category, "
        "'active', :consent_snapshot_id, :policy_revision, UTC_TIMESTAMP(), NULL) "
        "ON DUPLICATE KEY UPDATE status = 'active', "
        "consent_snapshot_id = VALUES(consent_snapshot_id), "
        "policy_revision = VALUES(policy_revision), granted_at = VALUES(granted_at), "
        "revoked_at = NULL"
    )
    _SQL_GRANT_READ = (
        "SELECT grant_id, owner_user_id, function_key, purpose, data_category, "
        "status, consent_snapshot_id, policy_revision, granted_at, revoked_at "
        "FROM ai_memory_projection_grant WHERE owner_user_id = :owner_user_id "
        "AND function_key = :function_key AND purpose = :purpose "
        "AND data_category = :data_category FOR UPDATE"
    )
    _SQL_GRANT_REVOKE = (
        "UPDATE ai_memory_projection_grant SET status = 'revoked', "
        "revoked_at = UTC_TIMESTAMP() WHERE grant_id = :grant_id AND status = 'active'"
    )
    _SQL_PROJECTION_READ = (
        "SELECT projection_id, owner_user_id, function_key, purpose, data_category, "
        "subject, projection_version, projection_input_hash, status, invalidated_at, "
        "invalidated_reason, entries_json, policy_revision, consent_snapshot_id, "
        "built_at FROM ai_memory_projection WHERE owner_user_id = :owner_user_id "
        "AND function_key = :function_key AND purpose = :purpose "
        "AND data_category = :data_category AND status = 'active'"
    )
    _SQL_PROJECTION_LATEST_SUFFIX = " ORDER BY projection_version DESC LIMIT 1"
    _SQL_PROJECTION_INVALIDATE = (
        "UPDATE ai_memory_projection SET status = 'invalidated', "
        "invalidated_at = UTC_TIMESTAMP(), invalidated_reason = :invalidated_reason "
        "WHERE owner_user_id = :owner_user_id AND function_key = :function_key "
        "AND purpose = :purpose AND data_category = :data_category "
        "AND status = 'active'"
    )
    _SQL_PROJECTION_INVALIDATE_BY_ID = (
        "UPDATE ai_memory_projection SET status = 'invalidated', "
        "invalidated_at = UTC_TIMESTAMP(), invalidated_reason = :invalidated_reason "
        "WHERE projection_id = :projection_id AND status = 'active'"
    )
    _SQL_PROJECTION_SUPERSEDE = (
        "UPDATE ai_memory_projection SET status = 'invalidated', "
        "invalidated_at = UTC_TIMESTAMP(), invalidated_reason = :invalidated_reason "
        "WHERE owner_user_id = :owner_user_id AND function_key = :function_key "
        "AND purpose = :purpose AND data_category = :data_category "
        "AND status = 'active' AND projection_version < :projection_version"
    )
    _SQL_PROJECTION_READ_BY_HASH = (
        "SELECT projection_id, owner_user_id, function_key, purpose, data_category, "
        "subject, projection_version, projection_input_hash, status, invalidated_at, "
        "invalidated_reason, entries_json, policy_revision, consent_snapshot_id, "
        "built_at FROM ai_memory_projection WHERE owner_user_id = :owner_user_id "
        "AND function_key = :function_key AND purpose = :purpose "
        "AND data_category = :data_category AND projection_input_hash = :input_hash "
        "ORDER BY projection_version DESC LIMIT 1"
    )
    _SQL_PROJECTION_REACTIVATE = (
        "UPDATE ai_memory_projection SET status = 'active', invalidated_at = NULL, "
        "invalidated_reason = NULL WHERE projection_id = :projection_id "
        "AND status = 'invalidated'"
    )
    _SQL_PROJECTION_MAX_VERSION = (
        "SELECT COALESCE(MAX(projection_version), 0) AS max_version "
        "FROM ai_memory_projection WHERE owner_user_id = :owner_user_id "
        "AND function_key = :function_key AND purpose = :purpose "
        "AND data_category = :data_category"
    )
    _SQL_PROJECTION_INSERT = (
        "INSERT INTO ai_memory_projection "
        "(projection_id, owner_user_id, function_key, purpose, data_category, "
        "subject, projection_version, projection_input_hash, status, "
        "invalidated_at, invalidated_reason, entries_json, policy_revision, "
        "consent_snapshot_id, built_at) VALUES (:projection_id, :owner_user_id, "
        ":function_key, :purpose, :data_category, :subject, :projection_version, "
        ":projection_input_hash, 'active', NULL, NULL, :entries_json, "
        ":policy_revision, :consent_snapshot_id, UTC_TIMESTAMP())"
    )
    _SQL_CLAIMS_CONFIRMED = (
        "SELECT claim_id, owner_user_id, subject, namespace, canonical_key, "
        "dimension, value_json, confidence, stability, importance, "
        "constraint_type, importance_confirmed, fact_kind, status, source_kind, "
        "last_event_id, last_event_seq FROM ai_memory_claim "
        "WHERE owner_user_id = :owner_user_id "
        "AND subject IN ({subjects}) AND status = 'confirmed' ORDER BY claim_id"
    )
    _SQL_GRANTS_ACTIVE_SCAN = (
        "SELECT grant_id, owner_user_id, function_key, purpose, data_category, "
        "status, consent_snapshot_id, policy_revision FROM "
        "ai_memory_projection_grant WHERE owner_user_id = :owner_user_id "
        "AND status = 'active'"
    )
    _SQL_PROJECTION_ACTIVE_ALL = (
        "SELECT projection_id, owner_user_id, function_key, purpose, "
        "data_category, subject, projection_version, projection_input_hash, "
        "status, invalidated_at, invalidated_reason, entries_json, "
        "policy_revision, consent_snapshot_id, built_at FROM ai_memory_projection "
        "WHERE owner_user_id = :owner_user_id AND status = 'active'"
    )

    _SQL_SUPPRESSION_ACTIVE = (
        "SELECT subject, canonical_key FROM ai_memory_suppression "
        "WHERE owner_user_id = :owner_user_id AND status = 'active'"
    )

    # ------------------------------------------------------------------
    # 授权快照（服务端重读，绝不信任调用方内容）
    # ------------------------------------------------------------------

    async def _load_active_consent(self, owner_user_id: int) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(self._SQL_CONSENT_READ),
            {"user_id": owner_user_id, "scope": PROFILE_CONSENT_SCOPE},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    @staticmethod
    def _snapshot_id(consent_row: dict[str, Any]) -> str:
        return derive_consent_snapshot_id(consent_row)

    def _current_revision(self) -> str:
        return self._policy_revision or _current_policy_revision()

    async def _lock_owner(self, owner_user_id: int) -> None:
        # 延迟导入避免循环（ledger 链路较重且引用本包其它模块）。
        from app.services.ai.memory.ledger import MemoryLedger

        await MemoryLedger(self._db).lock_owner(owner_user_id)

    @staticmethod
    def _dimension(function_key: str, purpose: str, data_category: str) -> dict[str, str]:
        return {
            "function_key": function_key,
            "purpose": purpose,
            "data_category": data_category,
        }

    # ------------------------------------------------------------------
    # grant / revoke
    # ------------------------------------------------------------------

    async def grant(
        self,
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
        consent_snapshot_id: str,
        policy_revision: str,
    ) -> dict[str, Any]:
        """授权一个投影维度；必须引用当前授权快照与当前策略版本。

        重复 grant 幂等（同维度 upsert 复活）；future key fail closed。
        """

        ProjectionPolicy.assert_function_enabled(function_key)
        consent = await self._load_active_consent(owner_user_id)
        if consent is None:
            raise ProjectionGrantDenied(
                "no active profile_text_extract consent for projection grant"
            )
        snapshot_id = self._snapshot_id(consent)
        if consent_snapshot_id != snapshot_id:
            raise ProjectionGrantDenied(
                "consent_snapshot_id does not reference the current consent snapshot"
            )
        current_revision = self._current_revision()
        if policy_revision != current_revision:
            raise ProjectionGrantDenied(
                f"policy_revision {policy_revision!r} does not match the current "
                f"policy {current_revision!r}"
            )
        if str(consent.get("policy_revision") or "") != current_revision:
            raise ProjectionGrantDenied(
                "the active consent was granted under a different policy revision"
            )
        # The database column is varchar(64). Keep the grant identifier opaque
        # and deterministic so long dimensions such as
        # ``compatibility/candidate_rank/compatibility_features`` cannot
        # overflow the column while replay and revoke still address the same
        # grant. The owner and dimension remain queryable in dedicated columns.
        grant_material = (
            f"{owner_user_id}:{function_key}:{purpose}:{data_category}"
        )
        grant_id = f"prj-grant:{hashlib.sha256(grant_material.encode()).hexdigest()[:48]}"
        params = {
            "grant_id": grant_id,
            "owner_user_id": owner_user_id,
            "consent_snapshot_id": snapshot_id,
            "policy_revision": policy_revision,
            **self._dimension(function_key, purpose, data_category),
        }
        await self._db.execute(text(self._SQL_GRANT_UPSERT), params)
        row = await self._db.execute(
            text(self._SQL_GRANT_READ),
            {
                "owner_user_id": owner_user_id,
                **self._dimension(function_key, purpose, data_category),
            },
        )
        grant_row = row.mappings().first()
        if grant_row is None:  # pragma: no cover - upsert 之后行必存在
            raise ProjectionGrantDenied("grant row missing after upsert")
        return dict(grant_row)

    async def revoke(
        self, *, owner_user_id: int, function_key: str, purpose: str, data_category: str
    ) -> int:
        """撤销授权并立即失效该维度全部 active 投影；返回失效条数。"""

        ProjectionPolicy.assert_function_enabled(function_key)
        row = await self._db.execute(
            text(self._SQL_GRANT_READ),
            {
                "owner_user_id": owner_user_id,
                **self._dimension(function_key, purpose, data_category),
            },
        )
        grant_row = row.mappings().first()
        if grant_row is None:
            raise ProjectionGrantNotFound(
                f"projection grant not found for owner {owner_user_id} "
                f"{function_key}/{purpose}/{data_category}"
            )
        # ``_SQL_GRANT_READ`` already acquires the row lock. A concurrent
        # revocation that wakes after the first transaction commits must be a
        # no-op; otherwise its caller could propagate privacy revision twice.
        if str(grant_row["status"]) != "active":
            return 0
        await self._db.execute(
            text(self._SQL_GRANT_REVOKE), {"grant_id": str(grant_row["grant_id"])}
        )
        result = await self._db.execute(
            text(self._SQL_PROJECTION_INVALIDATE),
            {
                "owner_user_id": owner_user_id,
                "invalidated_reason": "grant_revoked",
                **self._dimension(function_key, purpose, data_category),
            },
        )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # read_active：任何门失败返回 None（不泄露资源存在性）
    # ------------------------------------------------------------------

    async def read_active(
        self,
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
        projection_version: int | None = None,
    ) -> dict[str, Any] | None:
        ProjectionPolicy.assert_function_enabled(function_key)
        sql = self._SQL_PROJECTION_READ + (
            " AND projection_version = :projection_version"
            if projection_version is not None
            else self._SQL_PROJECTION_LATEST_SUFFIX
        )
        params: dict[str, Any] = {
            "owner_user_id": owner_user_id,
            **self._dimension(function_key, purpose, data_category),
        }
        if projection_version is not None:
            params["projection_version"] = projection_version
        result = await self._db.execute(text(sql), params)
        row = result.mappings().first()
        if row is None:
            return None
        projection = dict(row)

        grant_row = (
            await self._db.execute(
                text(self._SQL_GRANT_READ),
                {
                    "owner_user_id": owner_user_id,
                    **self._dimension(function_key, purpose, data_category),
                },
            )
        ).mappings().first()
        if grant_row is None or str(grant_row["status"]) != "active":
            return None

        consent = await self._load_active_consent(owner_user_id)
        if consent is None:
            return None
        if self._snapshot_id(consent) != str(projection["consent_snapshot_id"]):
            return None

        current_revision = self._current_revision()
        if str(projection["policy_revision"]) != current_revision:
            return None
        if str(grant_row["policy_revision"]) != current_revision:
            return None

        projection["entries"] = json.loads(str(projection["entries_json"]))
        try:
            ProjectionPolicy.assert_readable(
                projection,
                owner_user_id=owner_user_id,
                function_key=function_key,
                purpose=purpose,
                data_category=data_category,
                policy_revision=current_revision,
            )
        except ProjectionPolicyDenied:
            return None
        projection.pop("entries_json", None)
        return projection

    # ------------------------------------------------------------------
    # build / rebuild / invalidate（Task 4）
    # ------------------------------------------------------------------

    _SQL_OUTBOX_ENQUEUE = (
        "INSERT INTO derivation_outbox "
        "(event_id, aggregate_type, aggregate_id, event_type, changed_fields, "
        "source_revision_json, privacy_revision, payload_minimal, priority, published_at) "
        "VALUES (:event_id, 'ai_memory', :aggregate_id, 'memory_projection', '[]', '{}', 0, "
        ":payload_minimal, 50, UTC_TIMESTAMP()) "
        "ON DUPLICATE KEY UPDATE event_id = event_id"
    )

    async def build(
        self,
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
    ) -> dict[str, Any]:
        """从 confirmed Claim 构建维度投影；同 input hash 幂等，内容变化加版本。"""

        ProjectionPolicy.assert_function_enabled(function_key)
        # 统一锁序：owner 序列锁先行（与 Core 变更路径一致），并发同维度
        # build 在此串行化，max_version 竞读不可能发生。
        await self._lock_owner(owner_user_id)
        consent = await self._load_active_consent(owner_user_id)
        if consent is None:
            raise ProjectionGrantDenied(
                "no active profile_text_extract consent for projection build"
            )
        snapshot_id = self._snapshot_id(consent)
        entries = await self._collect_entries(owner_user_id, data_category)
        input_hash = ProjectionPolicy.projection_input_hash(entries)
        return await self._ensure_projection_version(
            owner_user_id=owner_user_id,
            function_key=function_key,
            purpose=purpose,
            data_category=data_category,
            entries=entries,
            input_hash=input_hash,
            snapshot_id=snapshot_id,
        )

    async def rebuild_dimensions_for_owner(
        self, owner_user_id: int, *, trigger_ref: str = "manual"
    ) -> int:
        """claim confirm/correct/suppress 后重建 owner 的全部已授权维度。

        返回发生实际动作的维度数（新建版本或补发幂等通知）。revoke 过的
        维度不会重建；input hash 未变化的维度只补一次幂等 outbox 通知。
        """

        grants = (
            await self._db.execute(
                text(self._SQL_GRANTS_ACTIVE_SCAN), {"owner_user_id": owner_user_id}
            )
        ).mappings().all()
        touched = 0
        for grant in grants:
            function_key = str(grant["function_key"])
            purpose = str(grant["purpose"])
            data_category = str(grant["data_category"])
            try:
                ProjectionPolicy.assert_function_enabled(function_key)
                # 锁序统一：owner 锁先行（build 内部也各自持锁，同事务重入无害）。
                await self._lock_owner(owner_user_id)
                consent = await self._load_active_consent(owner_user_id)
                if consent is None:
                    continue
                entries = await self._collect_entries(owner_user_id, data_category)
                input_hash = ProjectionPolicy.projection_input_hash(entries)
                current = await self._read_active_row(
                    owner_user_id=owner_user_id,
                    function_key=function_key,
                    purpose=purpose,
                    data_category=data_category,
                )
                if current is not None and str(current["projection_input_hash"]) == input_hash:
                    await self._enqueue_rebuild_notification(
                        owner_user_id=owner_user_id,
                        function_key=function_key,
                        purpose=purpose,
                        data_category=data_category,
                        input_hash=input_hash,
                        trigger_ref=trigger_ref,
                    )
                    touched += 1
                    continue
                await self._ensure_projection_version(
                    owner_user_id=owner_user_id,
                    function_key=function_key,
                    purpose=purpose,
                    data_category=data_category,
                    entries=entries,
                    input_hash=input_hash,
                    snapshot_id=self._snapshot_id(consent),
                )
                await self._enqueue_rebuild_notification(
                    owner_user_id=owner_user_id,
                    function_key=function_key,
                    purpose=purpose,
                    data_category=data_category,
                    input_hash=input_hash,
                    trigger_ref=trigger_ref,
                )
                touched += 1
            except ProjectionPolicyDenied:
                continue
        return touched

    async def invalidate_for_claim(
        self, owner_user_id: int, claim_id: str, *, reason: str
    ) -> int:
        """entries 引用该 claim 的全部 active 投影立即失效（correct/suppress 路径）。"""

        rows = (
            await self._db.execute(
                text(self._SQL_PROJECTION_ACTIVE_ALL), {"owner_user_id": owner_user_id}
            )
        ).mappings().all()
        invalidated = 0
        for row in rows:
            entries = json.loads(str(row["entries_json"]))
            if not any(str(entry.get("claim_id")) == claim_id for entry in entries):
                continue
            await self._db.execute(
                text(self._SQL_PROJECTION_INVALIDATE_BY_ID),
                {
                    "projection_id": str(row["projection_id"]),
                    "invalidated_reason": reason,
                },
            )
            invalidated += 1
        return invalidated

    # ------------------------------------------------------------------
    # build 内部
    # ------------------------------------------------------------------

    async def _collect_entries(
        self, owner_user_id: int, data_category: str
    ) -> list[dict[str, Any]]:
        subjects = _subjects_for_category(data_category)
        if not subjects:
            raise ProjectionPolicyDenied(f"unknown projection data_category: {data_category!r}")
        subject_list = ", ".join(f"'{subject}'" for subject in subjects)
        rows = (
            await self._db.execute(
                text(
                    self._SQL_CLAIMS_CONFIRMED.format(subjects=subject_list)
                ),
                {"owner_user_id": owner_user_id},
            )
        ).mappings().all()
        # 活动墓碑命中的事实不出现在投影里（删除对下游同样生效）。
        suppressed = {
            (str(row["subject"]), str(row["canonical_key"]))
            for row in (
                await self._db.execute(
                    text(self._SQL_SUPPRESSION_ACTIVE),
                    {"owner_user_id": owner_user_id},
                )
            ).mappings().all()
        }
        entries: list[dict[str, Any]] = []
        for row in rows:
            claim = dict(row)
            if (str(claim["subject"]), str(claim["canonical_key"])) in suppressed:
                continue
            ProjectionPolicy.assert_subject_category(data_category, str(claim["subject"]))
            ProjectionPolicy.assert_confirmed_claim("claim", str(claim["status"]))
            entry = _claim_to_entry(claim)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda item: (item["field_key"], item["claim_id"]))
        return entries

    async def _read_active_row(
        self, *, owner_user_id: int, function_key: str, purpose: str, data_category: str
    ) -> dict[str, Any] | None:
        row = (
            await self._db.execute(
                text(self._SQL_PROJECTION_READ + self._SQL_PROJECTION_LATEST_SUFFIX),
                {
                    "owner_user_id": owner_user_id,
                    **self._dimension(function_key, purpose, data_category),
                },
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def _ensure_projection_version(
        self,
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
        entries: list[dict[str, Any]],
        input_hash: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        existing = await self._read_active_row(
            owner_user_id=owner_user_id,
            function_key=function_key,
            purpose=purpose,
            data_category=data_category,
        )
        if existing is not None and str(existing["projection_input_hash"]) == input_hash:
            return existing
        # 同内容的历史版本（如 revoke→re-grant 后重建）：接管激活，不插新版。
        same_hash = (
            await self._db.execute(
                text(self._SQL_PROJECTION_READ_BY_HASH),
                {
                    "owner_user_id": owner_user_id,
                    "input_hash": input_hash,
                    **self._dimension(function_key, purpose, data_category),
                },
            )
        ).mappings().first()
        if same_hash is not None:
            await self._db.execute(
                text(self._SQL_PROJECTION_INVALIDATE),
                {
                    "owner_user_id": owner_user_id,
                    "invalidated_reason": "superseded_by_reactivation",
                    **self._dimension(function_key, purpose, data_category),
                },
            )
            await self._db.execute(
                text(self._SQL_PROJECTION_REACTIVATE),
                {"projection_id": str(same_hash["projection_id"])},
            )
            reactivated = dict(same_hash)
            reactivated["status"] = "active"
            reactivated["invalidated_at"] = None
            reactivated["invalidated_reason"] = None
            reactivated["entries"] = json.loads(str(reactivated["entries_json"]))
            reactivated.pop("entries_json", None)
            return reactivated
        max_row = (
            await self._db.execute(
                text(self._SQL_PROJECTION_MAX_VERSION),
                {
                    "owner_user_id": owner_user_id,
                    **self._dimension(function_key, purpose, data_category),
                },
            )
        ).mappings().first()
        next_version = int(max_row["max_version"]) + 1 if max_row else 1
        projection_id = (
            f"prj:{owner_user_id}:{function_key}:{purpose}:{data_category}:{next_version}"
        )
        # input hash 与版本无关；存储的 entries 回填真实版本号。
        versioned_entries = [
            dict(entry, projection_version=next_version) for entry in entries
        ]
        # 写入时契约校验：坏 entries 在构建期即响亮失败（worker bounded retry
        # 可见），而不是静默落库后读取端 fail closed 退化为 100% 回退。
        # 错误消息只含计数，绝不携带字段原文。
        try:
            ProjectionDocument(
                subject=_subject_for_dimension(data_category),  # type: ignore[arg-type]
                function_key=function_key,  # type: ignore[arg-type]
                purpose=purpose,  # type: ignore[arg-type]
                data_category=data_category,  # type: ignore[arg-type]
                policy_revision=self._current_revision(),
                consent_snapshot_id=snapshot_id,
                projection_version=next_version,
                projection_input_hash=input_hash,
                entries=tuple(versioned_entries),  # type: ignore[arg-type]
            )
        except ValidationError as exc:
            raise ProjectionContractError(
                f"projection document contract violation: "
                f"{exc.error_count()} validation error(s) rejected at build time"
            ) from exc
        params = {
            "projection_id": projection_id,
            "owner_user_id": owner_user_id,
            "subject": _subject_for_dimension(data_category),
            "projection_version": next_version,
            "projection_input_hash": input_hash,
            "entries_json": json.dumps(
                versioned_entries, ensure_ascii=False, sort_keys=True
            ),
            "policy_revision": self._current_revision(),
            "consent_snapshot_id": snapshot_id,
            **self._dimension(function_key, purpose, data_category),
        }
        try:
            await self._db.execute(text(self._SQL_PROJECTION_INSERT), params)
        except IntegrityError:
            # 并发同 input hash / 同版本：让位先到者，回读返回已有行。
            row = (
                await self._db.execute(
                    text(self._SQL_PROJECTION_READ + " AND projection_input_hash = :input_hash"),
                    {
                        "owner_user_id": owner_user_id,
                        "input_hash": input_hash,
                        **self._dimension(function_key, purpose, data_category),
                    },
                )
            ).mappings().first()
            if row is not None:
                projection = dict(row)
                projection["entries"] = json.loads(str(projection["entries_json"]))
                projection.pop("entries_json", None)
                return projection
            raise
        # 新版本落地后失效旧 active 版本（payload 不动，历史可回溯）。
        await self._db.execute(
            text(self._SQL_PROJECTION_SUPERSEDE),
            {
                "owner_user_id": owner_user_id,
                "projection_version": next_version,
                "invalidated_reason": "superseded_by_new_version",
                **self._dimension(function_key, purpose, data_category),
            },
        )
        return dict(
            params,
            status="active",
            invalidated_at=None,
            invalidated_reason=None,
            entries=versioned_entries,
            built_at=None,
        )

    async def _enqueue_rebuild_notification(
        self,
        *,
        owner_user_id: int,
        function_key: str,
        purpose: str,
        data_category: str,
        input_hash: str,
        trigger_ref: str,
    ) -> None:
        """按 projection:{owner}:{function}:{purpose}:{category}:{input_hash} 幂等入队。"""

        idempotency = f"projection:{owner_user_id}:{function_key}:{purpose}:{data_category}:{input_hash}"
        event_id = "prj-" + hashlib.sha256(idempotency.encode("utf-8")).hexdigest()[:40]
        payload_minimal = json.dumps(
            {
                "owner_user_id": owner_user_id,
                "function_key": function_key,
                "purpose": purpose,
                "data_category": data_category,
                "input_hash": input_hash,
                "trigger_ref": trigger_ref,
            },
            ensure_ascii=False,
        )
        await self._db.execute(
            text(self._SQL_OUTBOX_ENQUEUE),
            {
                "event_id": event_id,
                "aggregate_id": owner_user_id,
                "payload_minimal": payload_minimal,
            },
        )


# ---------------------------------------------------------------------------
# 构建器纯函数（field_key 反解 / 主体映射 / entry 形状）
# ---------------------------------------------------------------------------


def _subjects_for_category(data_category: str) -> tuple[str, ...]:
    return {
        "personal_profile": ("personal",),
        "public_profile_summary": ("personal",),
        "ideal_partner_preference": ("ideal_partner",),
        # compatibility_features 是派生匹配特征集：两个主体的 confirmed Claim
        # 都可以作为输入（subject 列仍为 personal，即投影属主本人）。
        "compatibility_features": ("personal", "ideal_partner"),
    }.get(data_category, ())


def _subject_for_dimension(data_category: str) -> str:
    return "ideal_partner" if data_category == "ideal_partner_preference" else "personal"


def _safe_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value))


def _claim_to_entry(claim: dict[str, Any]) -> dict[str, Any] | None:
    """Claim 行 → 10 字段 allowlist entry；不可表示的值返回 None。

    field_key 反解（canonical_key 只存 digest，one-way）：
    - structured：identity == field_key，对 PROFILE_ALLOWLIST 逐 key 重算 digest；
    - entry：identity == "{category}:{content_hash}"，对 9 个类别 × value
      （即原 content）重算 content_hash + digest；
    - 都不命中时退回 dimension（六维词表本身是安全标识符）。
    value 必须可表示为 string/number/boolean/string_list，否则跳过。
    """

    from app.services.ai.candidates import compute_candidate_content_hash
    from app.services.ai.memory.policy import MemoryPolicy

    canonical_key = str(claim["canonical_key"])
    subject = str(claim["subject"])
    dimension = str(claim["dimension"]) if claim["dimension"] is not None else "general"
    raw_value = claim["value_json"]
    if isinstance(raw_value, str):
        try:
            value: Any = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
    else:
        value = raw_value

    field_key: str | None = None
    if isinstance(value, (str, int, float, bool)) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    ):
        for candidate_key in sorted(_STRUCTURED_FIELD_KEYS):
            digest = MemoryPolicy.identity_digest(
                MemoryPolicy.candidate_identity("structured", candidate_key, None, None)
            )
            if canonical_key.endswith(":" + digest):
                field_key = candidate_key
                break
        if field_key is None and isinstance(value, str):
            # content_hash 与 category 相关，逐类别重算（与 Shadow Write 的
            # identity 规则一致，content 以 strip 后的规范形参与 hash）。
            for category in sorted(_ENTRY_CATEGORIES):
                entry_hash = compute_candidate_content_hash(
                    subject,
                    "entry",
                    None,
                    category,
                    None,
                    value.strip() if isinstance(value, str) else value,
                )
                digest = MemoryPolicy.identity_digest(
                    MemoryPolicy.candidate_identity("entry", None, category, entry_hash)
                )
                if canonical_key.endswith(":" + digest):
                    field_key = category
                    break
        if field_key is None:
            field_key = dimension if _safe_identifier(dimension) else None
    if field_key is None:
        return None

    if isinstance(value, bool):
        value_type = "boolean"
    elif isinstance(value, (int, float)):
        value_type = "number"
    elif isinstance(value, str):
        value_type = "string"
    else:
        value_type = "string_list"

    importance_confirmed = bool(claim.get("importance_confirmed"))
    return {
        "field_key": field_key,
        "value": value,
        "value_type": value_type,
        "source_kind": str(claim["source_kind"]),
        "claim_id": str(claim["claim_id"]),
        "stability": float(claim["stability"]),
        # 未确认 importance 以中性 0.5 入投影；constraint_type 只在用户确认
        # 动作之后才允许携带——投影边界强制兜底，下游不得据此硬排除。
        "importance": float(claim["importance"]) if importance_confirmed else 0.5,
        "constraint_type": claim["constraint_type"] if importance_confirmed else None,
        "projection_version": 0,  # 由 _ensure_projection_version 回填真实版本
        "evidence_ref": f"memory:claim:{claim['claim_id']}",
    }


def _load_vocabulary() -> tuple[frozenset[str], frozenset[str]]:
    from app.services.ai.features import PROFILE_ALLOWLIST
    from app.schemas.ai_profile import PROFILE_ENTRY_CATEGORIES

    return PROFILE_ALLOWLIST, PROFILE_ENTRY_CATEGORIES


_STRUCTURED_FIELD_KEYS: frozenset[str] = frozenset()
_ENTRY_CATEGORIES: frozenset[str] = frozenset()


def _ensure_vocabulary() -> None:
    global _STRUCTURED_FIELD_KEYS, _ENTRY_CATEGORIES
    if not _STRUCTURED_FIELD_KEYS:
        _STRUCTURED_FIELD_KEYS, _ENTRY_CATEGORIES = _load_vocabulary()


_ensure_vocabulary()

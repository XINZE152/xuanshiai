"""Task 14：Search/Recommend correctness 回归（批次三）。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 14）：
- 确认 memory mode 的 source_revision 和 consent 数据语义；
- 修复/明确 _candidate_projection_is_current() 的业务语义；
- fail closed、可见性、consent、revision 回归测试。

memory 模式语义（本文件锁定）：
- memory 投影没有 revision 向量（claim 驱动重建），source_revision=None
  是**结构事实**而非数据缺失；物化行校验不得套用 legacy 的 5 键向量
  比较，否则 memory 模式结果被全量过滤（correctness 缺陷）。
- memory 模式的正确校验 = 当前投影经 read_active(_batch) 全量门禁重验
  （grant/consent 快照/policy revision/可读性，任一失败即无投影 → 上层
  fail closed）+ 物化行与当前投影同 id 同 input hash。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import settings
from app.services.ai import search as search_mod

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# legacy 模式：既有 fail-closed 语义回归
# ---------------------------------------------------------------------------


def _legacy_consent() -> dict[str, Any]:
    return {
        "scope": "profile_text_extract",
        "version": "profile-text-v1",
        "policy_revision": "ai-policy-2026-08-07-v1",
        "granted_at": "2026-09-05T08:00:00",
    }


class _LegacyDb:
    """legacy 校验链 fake：revision 向量 / 投影 / consent 可注入。"""

    def __init__(
        self,
        *,
        revision: dict[str, int],
        projection: dict[str, Any] | None,
        consent: dict[str, Any] | None,
    ) -> None:
        self._revision = revision
        self._projection = projection
        self._consent = consent

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "FROM user_revision_state" in sql:
            return _rows(
                {
                    "profile_revision": self._revision["profile"],
                    "preference_revision": self._revision["preference"],
                    "privacy_revision": self._revision["privacy"],
                    "relationship_revision": self._revision["relationship"],
                    "policy_revision": self._revision["policy"],
                }
            )
        if "FROM ai_feature_projection" in sql:
            return _rows([dict(self._projection)] if self._projection else [])
        if "FROM ai_consent_grant" in sql:
            return _rows([dict(self._consent)] if self._consent else [])
        raise AssertionError(f"unhandled sql: {sql}")


def _rows(rows: list[dict[str, Any]] | dict[str, Any] | None):
    from tests.test_ai_memory_projections import _MappingResult

    if rows is None:
        return _MappingResult([])
    if isinstance(rows, dict):
        return _MappingResult([rows])
    return _MappingResult(rows)


def _legacy_projection_row(
    *,
    projection_id: int,
    subject_user_id: int,
    source_hash: str,
    source_revision: dict[str, int],
    consent: dict[str, Any],
) -> dict[str, Any]:
    """构造 _load_legacy_projections 期望的完整投影行。"""
    return {
        "id": projection_id,
        "subject_user_id": subject_user_id,
        "source_hash": source_hash,
        "fields_json": json.dumps({"height_cm": 175}),
        "profile_revision": int(source_revision.get("profile") or 0),
        "preference_revision": int(source_revision.get("preference") or 0),
        "privacy_revision": int(source_revision.get("privacy") or 0),
        "relationship_revision": int(source_revision.get("relationship") or 0),
        "policy_revision": int(source_revision.get("policy") or 0),
        "source_revision_json": json.dumps(source_revision),
        "consent_snapshot_json": json.dumps(consent),
        "status": "active",
        "expires_at": None,
    }


_FULL_REVISION = {
    "profile": 0, "preference": 0, "privacy": 0,
    "relationship": 0, "policy": 0,
}


async def test_legacy_current_check_rejects_revision_drift() -> None:
    """投影 revision 向量 ≠ 当前版本向量 → 校验失败（旧结果不得透出）。"""
    stored = {
        "projection_id": 7,
        "source_hash": "h-1",
        "source_revision_json": json.dumps({**_FULL_REVISION, "profile": 1}),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    projection_row = _legacy_projection_row(
        projection_id=7,
        subject_user_id=501,
        source_hash="h-1",
        source_revision={**_FULL_REVISION, "profile": 1},
        consent=_legacy_consent(),
    )
    db = _LegacyDb(
        revision={**_FULL_REVISION, "profile": 2},  # publish 后 revision 前进
        projection=projection_row,
        consent=_legacy_consent(),
    )
    assert (
        await search_mod._candidate_projection_is_current(db, 501, stored) is False
    ), "revision 向量漂移的物化行必须 fail closed"


async def test_legacy_current_check_rejects_revoked_consent() -> None:
    """consent 快照内容一致但当前授权已撤销 → 校验失败。"""
    stored = {
        "projection_id": 7,
        "source_hash": "h-1",
        "source_revision_json": json.dumps(_FULL_REVISION),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    projection_row = _legacy_projection_row(
        projection_id=7,
        subject_user_id=501,
        source_hash="h-1",
        source_revision=_FULL_REVISION,
        consent=_legacy_consent(),
    )
    db = _LegacyDb(
        revision=_FULL_REVISION,
        projection=projection_row,
        consent=None,  # 已撤销/不存在
    )
    assert (
        await search_mod._candidate_projection_is_current(db, 501, stored) is False
    ), "consent 撤销后物化行必须 fail closed"


async def test_legacy_current_check_accepts_fully_matching_row() -> None:
    """全部一致（id/hash/向量/consent 快照/当前授权）→ True。"""
    stored = {
        "projection_id": 7,
        "source_hash": "h-1",
        "source_revision_json": json.dumps(_FULL_REVISION),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    projection_row = _legacy_projection_row(
        projection_id=7,
        subject_user_id=501,
        source_hash="h-1",
        source_revision=_FULL_REVISION,
        consent=_legacy_consent(),
    )
    db = _LegacyDb(
        revision=_FULL_REVISION,
        projection=projection_row,
        consent=_legacy_consent(),
    )
    assert await search_mod._candidate_projection_is_current(db, 501, stored) is True


async def test_legacy_current_check_rejects_missing_projection() -> None:
    """投影缺失（被重建中/已失效）→ fail closed。"""
    stored = {
        "projection_id": 7,
        "source_hash": "h-1",
        "source_revision_json": json.dumps(_FULL_REVISION),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    db = _LegacyDb(revision=_FULL_REVISION, projection=None, consent=_legacy_consent())
    assert await search_mod._candidate_projection_is_current(db, 501, stored) is False


async def test_legacy_current_check_rejects_id_or_hash_mismatch() -> None:
    """物化行与当前投影 id 或 source_hash 不一致 → fail closed。"""
    projection_row = _legacy_projection_row(
        projection_id=7,
        subject_user_id=501,
        source_hash="h-1",
        source_revision=_FULL_REVISION,
        consent=_legacy_consent(),
    )
    db = _LegacyDb(
        revision=_FULL_REVISION, projection=projection_row, consent=_legacy_consent()
    )
    stale_id = {
        "projection_id": 999,
        "source_hash": "h-1",
        "source_revision_json": json.dumps(_FULL_REVISION),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    stale_hash = {
        "projection_id": 7,
        "source_hash": "rebuilt-hash",
        "source_revision_json": json.dumps(_FULL_REVISION),
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    assert await search_mod._candidate_projection_is_current(db, 501, stale_id) is False
    assert await search_mod._candidate_projection_is_current(db, 501, stale_hash) is False


async def test_legacy_current_check_rejects_malformed_revision_payload() -> None:
    """revision 载荷缺键（旧格式）→ fail closed，不做宽松补齐。"""
    stored = {
        "projection_id": 7,
        "source_hash": "h-1",
        "source_revision_json": json.dumps({"profile": 0}),  # 缺 4 键
        "consent_snapshot_json": json.dumps(_legacy_consent()),
    }
    projection_row = _legacy_projection_row(
        projection_id=7,
        subject_user_id=501,
        source_hash="h-1",
        source_revision=_FULL_REVISION,
        consent=_legacy_consent(),
    )
    db = _LegacyDb(
        revision=_FULL_REVISION,
        projection=projection_row,
        consent=_legacy_consent(),
    )
    assert await search_mod._candidate_projection_is_current(db, 501, stored) is False


# ---------------------------------------------------------------------------
# memory 模式：语义修正后的行为
# ---------------------------------------------------------------------------


async def test_memory_current_check_accepts_consistent_projection(monkeypatch) -> None:
    """memory 模式：物化行 id/hash 与当前投影一致 + 门禁全过 → True。

    修正前：legacy 校验因 source_revision=None / 精简 consent 全量拒绝。
    """
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store, projection_id, input_hash = await _memory_mode_fixture()
    stored = {
        "projection_id": projection_id,
        "source_hash": input_hash,
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    assert (
        await search_mod._candidate_projection_is_current(session, 501, stored)
        is True
    ), "memory 模式一致的物化行不得被误杀"


async def test_memory_current_check_fails_when_grant_revoked(monkeypatch) -> None:
    """memory 模式：grant 撤销（撤权事件到达）→ 当前投影读不到 → fail closed。"""
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store, projection_id, input_hash = await _memory_mode_fixture()
    stored = {
        "projection_id": projection_id,
        "source_hash": input_hash,
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    grant = store.grants[(501, "search", "candidate_filter", "personal_profile")]
    grant["status"] = "revoked"
    assert (
        await search_mod._candidate_projection_is_current(session, 501, stored)
        is False
    )


async def test_memory_current_check_fails_when_consent_rotated(monkeypatch) -> None:
    """memory 模式：consent 轮换（快照 id 不再匹配当前授权）→ fail closed。"""
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store, projection_id, input_hash = await _memory_mode_fixture()
    stored = {
        "projection_id": projection_id,
        "source_hash": input_hash,
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    store.consents[501] = dict(store.consents[501], version="profile_text_extract-v4")
    assert (
        await search_mod._candidate_projection_is_current(session, 501, stored)
        is False
    )


async def test_memory_current_check_fails_on_projection_rebuild(monkeypatch) -> None:
    """memory 模式：投影重建（新版本新 id/hash）→ 旧物化行 fail closed。"""
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store, projection_id, input_hash = await _memory_mode_fixture()
    stale = {
        "projection_id": "prj:501:search:candidate_filter:personal_profile:0",
        "source_hash": "older-input-hash",
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    assert (
        await search_mod._candidate_projection_is_current(session, 501, stale)
        is False
    ), "重建后旧 id/hash 物化行不得透出"
    current = {
        "projection_id": projection_id,
        "source_hash": input_hash,
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    assert (
        await search_mod._candidate_projection_is_current(session, 501, current)
        is True
    )


async def test_memory_current_check_fails_when_projection_missing(monkeypatch) -> None:
    """memory 模式：投影被整体失效/删除 → fail closed。"""
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store, projection_id, input_hash = await _memory_mode_fixture()
    stored = {
        "projection_id": projection_id,
        "source_hash": input_hash,
        "source_revision_json": None,
        "consent_snapshot_json": None,
    }
    for rows in store.projections.values():
        for row in rows:
            row["status"] = "invalidated"
    assert (
        await search_mod._candidate_projection_is_current(session, 501, stored)
        is False
    )


# ---------------------------------------------------------------------------
# 批量读取（read_active_batch）：与 read_active 等价 + 数量级
# ---------------------------------------------------------------------------


async def test_read_active_batch_matches_read_active(monkeypatch) -> None:
    """批量读取结果与逐用户 read_active 完全一致（同门同果）。"""
    from app.services.ai.memory.projections import MemoryProjectionService

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store, _pid, _hash = await _memory_mode_fixture()
    service = MemoryProjectionService(
        session, policy_revision="ai-policy-2026-08-07-v1"
    )

    batch = await service.read_active_batch(
        owner_user_ids=[501, 502, 503, 504],
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    single_501 = await service.read_active(
        owner_user_id=501,
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    assert single_501 is not None
    assert 501 in batch
    assert batch[501]["projection_id"] == single_501["projection_id"]
    assert batch[501]["entries"] == single_501["entries"]
    # 502 grant 被撤销 / 503 无 personal 投影 / 504 完全缺失 → 均不出现。
    assert set(batch) == {501}


async def test_read_active_batch_three_queries_total(monkeypatch) -> None:
    """批量路径恰好 3 条 SQL（projection/grant/consent），不随人数线性增长。"""
    from app.services.ai.memory.projections import MemoryProjectionService

    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store, _pid, _hash = await _memory_mode_fixture()
    baseline = len(session.calls)
    service = MemoryProjectionService(
        session, policy_revision="ai-policy-2026-08-07-v1"
    )
    await service.read_active_batch(
        owner_user_ids=[501, 502, 503],
        function_key="search",
        purpose="candidate_filter",
        data_category="personal_profile",
    )
    executed = session.calls[baseline:]
    assert len(executed) == 3, (
        f"批量读取应恰好 3 条 SQL（projection/grant/consent），实际 {len(executed)}"
    )
    joined = " ".join(sql for sql, _ in executed)
    assert "owner_user_id IN (" in joined


async def test_load_memory_projection_fields_is_batched(monkeypatch) -> None:
    """search 的 memory 投影读取走批量入口（200 候选 = 3 条 SQL）。"""
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store, _pid, _hash = await _memory_mode_fixture()
    baseline = len(session.calls)
    result = await search_mod._load_memory_projection_fields(
        session, [501, 502, 503]
    )
    executed = session.calls[baseline:]
    assert len(executed) == 3
    assert set(result) == {501}


# ---------------------------------------------------------------------------
# fixture：memory 投影 store
# ---------------------------------------------------------------------------


async def _memory_mode_fixture():
    """构造带 search/candidate_filter/personal_profile 投影的 memory store。

    返回 (session, store, projection_id, input_hash)。501 有 personal 投影；
    502 personal 投影存在但 grant 被撤销；503 只有 ideal_partner 投影；
    504 完全无投影。
    """
    from app.services.ai.memory.projections import (
        MemoryProjectionService,
        derive_consent_snapshot_id,
    )
    from tests.test_ai_memory_projections import (
        FakeProjectionSession,
        ProjectionStore,
        seed_claim,
    )

    policy_revision = "ai-policy-2026-08-07-v1"
    store = ProjectionStore()
    consent = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": policy_revision,
        "granted_at": "2026-09-05T08:00:00",
    }
    snapshot_id = derive_consent_snapshot_id(consent)
    for uid in (501, 502, 503):
        store.consents[uid] = dict(consent)
        store.grants[(uid, "search", "candidate_filter", "personal_profile")] = {
            "grant_id": f"g-{uid}-personal",
            "owner_user_id": uid,
            "function_key": "search",
            "purpose": "candidate_filter",
            "data_category": "personal_profile",
            "status": "active",
            "consent_snapshot_id": snapshot_id,
            "policy_revision": policy_revision,
            "granted_at": "2026-09-05T08:00:00",
            "revoked_at": None,
        }
        store.grants[(uid, "recommend", "candidate_rank", "ideal_partner_preference")] = {
            "grant_id": f"g-{uid}-ideal",
            "owner_user_id": uid,
            "function_key": "recommend",
            "purpose": "candidate_rank",
            "data_category": "ideal_partner_preference",
            "status": "active",
            "consent_snapshot_id": snapshot_id,
            "policy_revision": policy_revision,
            "granted_at": "2026-09-05T08:00:00",
            "revoked_at": None,
        }
    store.grants[(502, "search", "candidate_filter", "personal_profile")][
        "status"
    ] = "revoked"
    seed_claim(
        store,
        "c-501-h",
        owner_user_id=501,
        canonical_key="personal:lifestyle:structured:height_cm",
        value=175,
    )
    session = FakeProjectionSession(store)
    service = MemoryProjectionService(session, policy_revision=policy_revision)
    for uid in (501, 502):
        await service.build(
            owner_user_id=uid,
            function_key="search",
            purpose="candidate_filter",
            data_category="personal_profile",
        )
    await service.build(
        owner_user_id=503,
        function_key="recommend",
        purpose="candidate_rank",
        data_category="ideal_partner_preference",
    )
    projection = store.projections[
        (501, "search", "candidate_filter", "personal_profile")
    ][-1]
    return (
        session,
        store,
        str(projection["projection_id"]),
        str(projection["projection_input_hash"]),
    )

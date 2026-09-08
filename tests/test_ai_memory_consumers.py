"""AI 军师消费者适配（CounselorMemoryAdapter）测试（Phase 3 Task 3）。

边界：
- 仅允许当前用户 personal 与 ideal_partner 的 confirmed active Projection；
- purpose 仅允许 session_context / explanation；
- 上下文只含冻结的最小字段集（field_key/value/value_type/stability/
  importance/constraint_type/claim_id/projection_version）；
- 撤权、版本不一致、Projection 缺失或 subject 不符 → 空上下文 fail closed；
- Provider 输入（prompt payload）不含 raw quote、transcript 或 State/Insight。
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.test_ai_memory_projections import (
    FakeProjectionSession,
    ProjectionStore,
    _MappingResult,
    seed_claim,
)

pytestmark = pytest.mark.asyncio

OWNER_ID = 42
OTHER_ID = 43
POLICY_REVISION = "ai-policy-2026-08-07-v1"

RAW_QUOTE = "我每天早上都要喝一杯咖啡，而且我不吃香菜"


async def _grant_and_build(
    store: ProjectionStore,
    session: FakeProjectionSession,
    *,
    owner_id: int = OWNER_ID,
    purpose: str = "session_context",
) -> None:
    from app.services.ai.memory.projections import MemoryProjectionService, derive_consent_snapshot_id

    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    snapshot_id = derive_consent_snapshot_id(store.consents[owner_id])
    for category in ("personal_profile", "ideal_partner_preference"):
        await service.grant(
            owner_user_id=owner_id,
            function_key="counselor_context",
            purpose=purpose,
            data_category=category,
            consent_snapshot_id=snapshot_id,
            policy_revision=POLICY_REVISION,
        )
        await service.build(
            owner_user_id=owner_id,
            function_key="counselor_context",
            purpose=purpose,
            data_category=category,
        )


def _seeded_store() -> tuple[FakeProjectionSession, ProjectionStore]:
    store = ProjectionStore()
    store.consents[OWNER_ID] = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-06T08:00:00",
    }
    session = FakeProjectionSession(store)
    seed_claim(store, "c1", dimension="lifestyle", value="每天喝咖啡")
    seed_claim(store, "c2", dimension="personality_social", value="性格外向",
               subject="personal")
    seed_claim(store, "c3", dimension="lifestyle", value="希望对方也爱运动",
               subject="ideal_partner")
    return session, store


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_counselor_context_returns_confirmed_entries() -> None:
    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session)
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    assert not context.is_empty
    subjects = set(context.subjects())
    assert subjects <= {"personal", "ideal_partner"}
    assert subjects  # 至少一个维度可读
    for entry in context.iter_entries():
        assert set(entry) == {
            "field_key", "value", "value_type", "stability", "importance",
            "constraint_type", "claim_id", "projection_version",
        }


async def test_counselor_context_includes_both_subjects() -> None:
    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session, purpose="explanation")
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="explanation")
    assert "personal" in context.subjects()
    assert "ideal_partner" in context.subjects()


async def test_counselor_context_rejects_invalid_purpose() -> None:
    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    adapter = CounselorMemoryAdapter(session)
    with pytest.raises(ValueError):
        await adapter.build_context(OWNER_ID, purpose="candidate_rank")


# ---------------------------------------------------------------------------
# fail closed
# ---------------------------------------------------------------------------


async def test_counselor_context_empty_without_grant() -> None:
    """未授权（无 grant）：空上下文，Provider 不得被调用。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    assert context.is_empty


async def test_counselor_context_empty_after_grant_revoked() -> None:
    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session)
    from app.services.ai.memory.projections import MemoryProjectionService

    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    for category in ("personal_profile", "ideal_partner_preference"):
        await service.revoke(
            owner_user_id=OWNER_ID,
            function_key="counselor_context",
            purpose="session_context",
            data_category=category,
        )
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    assert context.is_empty


async def test_counselor_context_owner_isolated() -> None:
    """他人不能读 owner 的上下文（owner 隔离 fail closed）。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session)
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OTHER_ID, purpose="session_context")
    assert context.is_empty


async def test_counselor_context_empty_on_purpose_dimension_missing() -> None:
    """purpose 维度缺授权/投影：该维度 fail closed，不串读其它 purpose。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    # 只授了 session_context，explanation 维度无授权。
    await _grant_and_build(store, session, purpose="session_context")
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="explanation")
    assert context.is_empty


async def test_counselor_context_empty_when_one_required_category_is_unavailable() -> None:
    """军师不是部分授权降级：任一冻结类别缺失都不得调用 Provider。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session)
    missing = next(
        grant_id
        for grant_id, grant in store.grants.items()
        if grant["data_category"] == "ideal_partner_preference"
    )
    del store.grants[missing]

    context = await CounselorMemoryAdapter(session).build_context(
        OWNER_ID, purpose="session_context"
    )
    assert context.is_empty


async def test_counselor_context_empty_when_consent_revoked() -> None:
    """consent 撤销后（快照门失效）：空上下文。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    await _grant_and_build(store, session)
    store.consents[OWNER_ID] = None
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    assert context.is_empty


async def test_sanitizer_discards_nonmapping_or_wrongly_typed_projection_entries() -> None:
    """投影 JSON 或缓存被破坏也必须是空上下文，不能抛出 500 或宽松转换。"""

    from app.services.ai.memory.consumers import _sanitize_entries

    assert _sanitize_entries(
        {
            "owner_user_id": OWNER_ID,
            "entries": [
                "not-a-mapping",
                {
                    "field_key": "interest_tags",
                    "value": ["hiking"],
                    "value_type": "string_list",
                    "stability": "0.9",
                    "importance": 0.8,
                    "constraint_type": None,
                    "claim_id": "bad-type",
                    "projection_version": 1,
                },
            ],
        },
        OWNER_ID,
    ) == ()


# ---------------------------------------------------------------------------
# Provider 输入边界（prompt payload / 敏感信息）
# ---------------------------------------------------------------------------


async def test_prompt_payload_contains_no_raw_quote_or_transcript() -> None:
    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    # 在事件 provenance 与 claim value 中埋入原文短语。
    store.events["evt_c1"] = {
        "event_id": "evt_c1",
        "source_quote": RAW_QUOTE,
        "source_ref": "candidate:abc",
    }
    await _grant_and_build(store, session)
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    payload = context.to_prompt_payload()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert RAW_QUOTE not in serialized
    assert "transcript" not in serialized
    # 只允许冻结字段集。
    allowed = {
        "field_key", "value", "value_type", "stability", "importance",
        "constraint_type", "claim_id", "projection_version",
        "function_key", "purpose", "owner_user_id", "subjects", "entries",
        "subject", "projection_versions",
    }

    def _keys(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key in allowed, f"unexpected provider input key: {key}"
                _keys(value)
        elif isinstance(node, list):
            for item in node:
                _keys(item)

    _keys(payload)


async def test_provider_mock_receives_sanitized_input_only() -> None:
    """Mock Provider 集成：Provider 看到的消息不含原文。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    store.events["evt_c1"] = {
        "event_id": "evt_c1",
        "source_quote": RAW_QUOTE,
        "source_ref": "candidate:abc",
    }
    await _grant_and_build(store, session)
    adapter = CounselorMemoryAdapter(session)
    context = await adapter.build_context(OWNER_ID, purpose="session_context")
    messages = adapter.to_provider_messages(context, task_hint="ADVISOR_ADVICE")

    captured: list[list[dict[str, str]]] = []

    class RecordingMockProvider:
        async def complete(self, messages, *, json_mode=False):  # 模拟 Provider 入口
            captured.append(messages)
            return "ok"

    provider = RecordingMockProvider()
    for message in messages:
        await provider.complete([message])
    assert captured, "provider must be invoked with the sanitized messages"
    for batch in captured:
        for message in batch:
            assert RAW_QUOTE not in message.get("content", "")


async def test_no_sensitive_values_in_logs(caplog) -> None:
    """敏感日志扫描：适配器执行路径的日志不携带字段值/原文。"""

    from app.services.ai.memory.consumers import CounselorMemoryAdapter

    session, store = _seeded_store()
    store.events["evt_c1"] = {
        "event_id": "evt_c1",
        "source_quote": RAW_QUOTE,
        "source_ref": "candidate:abc",
    }
    await _grant_and_build(store, session)
    adapter = CounselorMemoryAdapter(session)
    with caplog.at_level(logging.DEBUG, logger="app.services.ai.memory"):
        context = await adapter.build_context(OWNER_ID, purpose="session_context")
        adapter.to_provider_messages(context, task_hint="ADVISOR_ADVICE")
    for record in caplog.records:
        message = record.getMessage()
        assert RAW_QUOTE not in message
        assert "每天喝咖啡" not in message


# ---------------------------------------------------------------------------
# Task 4：AI 分身（PersonaMemoryAdapter）
# ---------------------------------------------------------------------------


class FakeCache:
    """dict 版异步缓存（get/set/delete/scan_iter 与 redis_client 同形）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.data[key] = value

    async def delete(self, *keys: str):
        for key in keys:
            self.data.pop(key, None)

    async def scan_iter(self, match: str):
        import fnmatch

        for key in list(self.data):
            if fnmatch.fnmatch(key, match):
                yield key


class PersonaProjectionSession(FakeProjectionSession):
    """为 persona 缓存模拟真实的 privacy revision 读取。"""

    def _route(self, sql: str, values: dict):
        if "FROM user_revision_state" in sql:
            return _MappingResult([{"privacy_revision": 0}])
        return super()._route(sql, values)


PERSONA_PUBLIC_FIELDS = frozenset(
    {
        "age",
        "city_code",
        "marriage_status",
        "education_level",
        "height_cm",
        "income_band",
        "occupation_group",
        "interest_tags",
        "lifestyle_tags",
        "relationship_goal",
    }
)


async def _allow_decide(self, db, viewer_id, candidate_id, scene):
    from app.services.candidate_visibility import VisibilityDecision

    return VisibilityDecision(True, None)


async def _deny_decide(self, db, viewer_id, candidate_id, scene):
    from app.services.candidate_visibility import VisibilityDecision

    return VisibilityDecision(False, "BLOCKED_RELATIONSHIP")


def _persona_store() -> tuple[FakeProjectionSession, ProjectionStore]:
    store = ProjectionStore()
    store.consents[OWNER_ID] = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-06T08:00:00",
    }
    session = PersonaProjectionSession(store)
    # 公开结构化字段（field_key 反解命中公开 allowlist）+ 私密自由文本条目
    # （dimension 反解不在 allowlist，必须被过滤）。
    from tests.test_ai_memory_projections import structured_canonical

    seed_claim(
        store,
        "p1",
        dimension="lifestyle",
        value=25,
        canonical_key=structured_canonical("personal", "lifestyle", "age"),
    )
    seed_claim(
        store,
        "p2",
        dimension="lifestyle",
        value=175,
        canonical_key=structured_canonical("personal", "lifestyle", "height_cm"),
    )
    seed_claim(store, "p3", dimension="personality_social", value="住在XX路3号楼")
    return session, store


async def _grant_build_persona(
    store: ProjectionStore, session: FakeProjectionSession
) -> None:
    from app.services.ai.memory.projections import (
        MemoryProjectionService,
        derive_consent_snapshot_id,
    )

    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    snapshot_id = derive_consent_snapshot_id(store.consents[OWNER_ID])
    await service.grant(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
        consent_snapshot_id=snapshot_id,
        policy_revision=POLICY_REVISION,
    )
    await service.build(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )


async def test_persona_context_only_public_fields(monkeypatch) -> None:
    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    monkeypatch.setattr(consumers_mod.CandidateVisibilityService, "decide", _allow_decide)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    context = await adapter.build_public_context(
        43, OWNER_ID, purpose="session_context"
    )
    assert not context.is_empty
    for entry in context.iter_entries():
        assert entry["field_key"] in PERSONA_PUBLIC_FIELDS
        assert "住在XX路" not in json.dumps(entry, ensure_ascii=False)


async def test_persona_context_fail_closed_on_visibility_denial(monkeypatch) -> None:
    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    monkeypatch.setattr(consumers_mod.CandidateVisibilityService, "decide", _deny_decide)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    context = await adapter.build_public_context(
        43, OWNER_ID, purpose="session_context"
    )
    assert context.is_empty


async def test_persona_context_fail_closed_on_visibility_error(monkeypatch) -> None:
    """可见性未知（查询异常）：空上下文。"""

    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    async def broken_decide(self, db, viewer_id, candidate_id, scene):
        raise RuntimeError("visibility unknown")

    monkeypatch.setattr(consumers_mod.CandidateVisibilityService, "decide", broken_decide)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    context = await adapter.build_public_context(
        43, OWNER_ID, purpose="session_context"
    )
    assert context.is_empty


async def test_persona_context_no_grant_empty(monkeypatch) -> None:
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    monkeypatch.setattr(
        consumers_mod_ref().CandidateVisibilityService, "decide", _allow_decide
    )
    session, store = _persona_store()
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    context = await adapter.build_public_context(
        43, OWNER_ID, purpose="session_context"
    )
    assert context.is_empty


def consumers_mod_ref():
    from app.services.ai.memory import consumers as consumers_mod

    return consumers_mod


async def test_persona_context_rejects_invalid_purpose() -> None:
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    session, store = _persona_store()
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    with pytest.raises(ValueError):
        await adapter.build_public_context(43, OWNER_ID, purpose="explanation")


async def test_persona_cache_isolation_and_invalidation(monkeypatch) -> None:
    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter
    from app.services.ai.memory.projections import MemoryProjectionService

    monkeypatch.setattr(consumers_mod.CandidateVisibilityService, "decide", _allow_decide)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = PersonaMemoryAdapter(session, cache=cache)
    first = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not first.is_empty
    assert len(cache.data) == 1
    # 不同 viewer 缓存隔离。
    second = await adapter.build_public_context(44, OWNER_ID, purpose="session_context")
    assert not second.is_empty
    assert len(cache.data) == 2
    # 同 viewer 命中缓存（无新增键）。
    await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert len(cache.data) == 2
    # 即使主动失效事件尚未抵达，读取也必须先重验授权，不能命中旧缓存。
    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    await service.revoke(
        owner_user_id=OWNER_ID,
        function_key="persona_context",
        purpose="session_context",
        data_category="public_profile_summary",
    )
    context = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert context is not None and context.is_empty
    assert len(cache.data) == 2
    # 撤权事件到达后，target 维度的缓存键全部清除。
    await consumers_mod.invalidate_persona_memory_cache(
        target_user_id=OWNER_ID, cache=cache
    )
    assert not cache.data


async def test_persona_cache_rejects_malformed_or_nonpublic_cached_shape(monkeypatch) -> None:
    """缓存是非信任输入：类型、主体、版本或公开 allowlist 不符均按 miss。"""

    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    monkeypatch.setattr(
        consumers_mod.CandidateVisibilityService, "decide", _allow_decide
    )
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    cache = FakeCache()
    adapter = PersonaMemoryAdapter(session, cache=cache)
    context = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    version = context.projection_versions()[0]
    cache_key = next(iter(cache.data))
    cache.data[cache_key] = json.dumps(
        {
            "function_key": "persona_context",
            "purpose": "session_context",
            "owner_user_id": OWNER_ID,
            "subjects": [
                {
                    "subject": "ideal_partner",
                    "entries": [
                        {
                            "field_key": "private_note",
                            "value": "not public",
                            "value_type": "string",
                            "stability": "not-a-number",
                            "importance": 0.8,
                            "constraint_type": None,
                            "claim_id": "bad-cache",
                            "projection_version": version + 1,
                        }
                    ],
                }
            ],
            "projection_versions": [version + 1],
        }
    )

    cached = await adapter._cache_get(
        cache_key,
        expected_owner_user_id=OWNER_ID,
        expected_purpose="session_context",
        expected_projection_version=version,
    )
    assert cached is None
    # miss 后从实时、授权投影重新构建，不能返回伪造缓存。
    rebuilt = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    assert not rebuilt.is_empty
    assert all(entry["field_key"] in PERSONA_PUBLIC_FIELDS for entry in rebuilt.iter_entries())


async def test_persona_provider_payload_public_only(monkeypatch) -> None:
    from app.services.ai.memory import consumers as consumers_mod
    from app.services.ai.memory.consumers import PersonaMemoryAdapter

    monkeypatch.setattr(consumers_mod.CandidateVisibilityService, "decide", _allow_decide)
    session, store = _persona_store()
    await _grant_build_persona(store, session)
    adapter = PersonaMemoryAdapter(session, cache=FakeCache())
    context = await adapter.build_public_context(43, OWNER_ID, purpose="session_context")
    payload = context.to_prompt_payload()
    serialized = json.dumps(payload, ensure_ascii=False)
    # 私密自由文本、ideal_partner 主体、原文摘录不得进入 Provider 输入。
    assert "住在XX路" not in serialized
    assert "ideal_partner" not in serialized
    assert RAW_QUOTE not in serialized

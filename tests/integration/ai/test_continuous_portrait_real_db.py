"""continuous_v2 的真实 MySQL/Worker 事务测试；模型使用确定性测试 Provider。"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.schemas.ai_common import AiConsentGrantRequest
from app.schemas.ai_profile import ProfileDraftFieldPatchRequest, ProfileFieldPatchAction, ProfileSubject
from app.services.ai import continuous, profile
from app.services.ai.consents import grant_consent, revoke_consent
from app.services.ai.tasks import TaskError
from app.workers import ai_worker

USER = 9_876_548_701
VERSION = "profile-text-v1"
POLICY = "ai-policy-2026-08-07-v1"


@pytest_asyncio.fixture(autouse=True)
async def close_audit_flusher():
    yield
    from app.services.ai.audit import shutdown_audit_flusher
    await shutdown_audit_flusher()

async def seed(db, subject="personal"):
    # 通用 suite 清扫尚不包含这两张既有表；仅清理本文件专属测试用户。
    await db.execute(text("DELETE FROM api_idempotency_record WHERE user_id=:user"), {"user": USER})
    await db.execute(text("DELETE FROM ai_profile_preview WHERE user_id=:user"), {"user": USER})
    await grant_consent(db, USER, "profile_text_extract", AiConsentGrantRequest(
        consent_version=VERSION, policy_revision=POLICY), uuid.uuid4().hex, 0)
    session = await profile.create_master_session(db, USER, ProfileSubject.PERSONAL, VERSION)
    turn = uuid.uuid4().hex
    await db.execute(text(
        "INSERT INTO ai_profile_turn (turn_id,session_id,client_turn_id,user_id,turn_no,answer_text) "
        "VALUES (:turn,:session,:turn,:user,1,'合成测试：喜欢阅读，重视沟通，认真交往')"
    ), {"turn": turn, "session": session.session_id, "user": USER})
    for dimension, category, content in (
        ("personality_social", "personality", "喜欢安静阅读"),
        ("emotional_expression", "values", "重视平等沟通"),
        ("future_expectations", "life_plan", "认真交往"),
    ):
        await add_candidate(db, session.session_id, turn, subject, dimension, category, content)
    await db.commit()
    return session.session_id, turn


async def add_candidate(db, session_id, turn, subject, dimension, category, content):
    from app.services.ai.candidates import compute_candidate_content_hash
    await db.execute(text(
        "INSERT INTO ai_profile_candidate (candidate_id,session_id,user_id,subject,profile_dimension,"
        "field_kind,category,content,confidence,source_turn_ids,consent_version,policy_revision,content_hash) "
        "VALUES (:id,:session,:user,:subject,:dimension,'entry',:category,:content,0.95,:sources,:version,:policy,:hash)"
    ), {"id": uuid.uuid4().hex, "session": session_id, "user": USER, "subject": subject,
        "dimension": dimension, "category": category, "content": content, "sources": json.dumps([turn]),
        "version": VERSION, "policy": POLICY,
        "hash": compute_candidate_content_hash(subject, "entry", None, category, None, content)})


async def build(db, subject="personal", refresh=False):
    state = await continuous.build_continuous_draft(db, USER, subject, refresh=refresh, idempotency_key=uuid.uuid4().hex)
    await db.commit()
    return state[subject]


async def generate(db, engine, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    monkeypatch.setattr(ai_worker, "session_factory", async_sessionmaker(engine, expire_on_commit=False))
    result = await ai_worker._run_round("continuous-integration", 10)
    await db.rollback()  # MySQL REPEATABLE READ: 下一次读取看见 Worker 的提交。
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["personal", "ideal_partner"])
async def test_frozen_preview_atomic_confirmation_and_replay(real_db_session, real_db_engine, monkeypatch, subject):
    db = real_db_session
    session, turn = await seed(db, subject)
    state = await build(db, subject)
    assert state["status"] == "generating"
    waiting = await continuous.get_continuous_preview(db, state["preview_id"], USER)
    assert waiting["content"] == "" and waiting["fields"] == []
    with pytest.raises(LookupError, match="PREVIEW_NOT_READY"):
        await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "early")
    await db.rollback()
    with pytest.raises(TaskError, match="整份确认"):
        await profile.publish_profile_draft(db, state["draft_id"], USER, 0, "legacy-bypass")
    await db.rollback()
    await generate(db, real_db_engine, monkeypatch)
    preview = await continuous.get_continuous_preview(db, state["preview_id"], USER)
    assert preview["generation_status"] == "completed", preview
    assert preview["content"] and len(preview["fields"]) == 3
    await add_candidate(db, session, turn, subject, "lifestyle", "routine", "周末散步")
    await db.commit()
    unchanged = await build(db, subject)
    assert unchanged["preview_id"] == state["preview_id"] and unchanged["has_updates"]
    assert (await continuous.get_continuous_preview(db, state["preview_id"], USER))["content"] == preview["content"]
    result = await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "confirm-one")
    await db.commit()
    replay = await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "confirm-one")
    other_key = await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "confirm-two")
    await db.commit()
    assert replay["replayed"] and other_key["revision_id"] == result["revision_id"]
    narrative = await profile.load_published_narrative(db, USER, subject)
    assert narrative["status"] == "confirmed" and narrative["revision_id"] == result["revision_id"]
    assert continuous._narrative_text(narrative["data"]) == preview["content"]
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_profile_revision WHERE user_id=:user"), {"user": USER})).scalar_one() == 1
    assert (await db.execute(text("SELECT active_status FROM ai_profile_session WHERE session_id=:id"), {"id": session})).scalar_one() == 1
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_task WHERE owner_user_id=:user AND task_type='profile_narrative'"), {"user": USER})).scalar_one() == 0
    with pytest.raises(TaskError, match="重新审阅"):
        await profile.request_narrative_regenerate(db, USER, subject, "legacy-regenerate")
    await db.rollback()


@pytest.mark.asyncio
async def test_edit_invalidates_preview_without_confirming_memory(real_db_session, real_db_engine, monkeypatch):
    db = real_db_session
    await seed(db)
    state = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    preview = await continuous.get_continuous_preview(db, state["preview_id"], USER)
    action = ProfileDraftFieldPatchRequest(field_key=preview["fields"][0]["field_key"],
        action=ProfileFieldPatchAction.REPLACE, value="更喜欢安静阅读", expected_revision=0)
    updated = await profile.confirm_profile_draft(db, state["draft_id"], USER, [action], 0, "edit-one")
    await db.commit()
    assert updated.revision == 1
    assert all(field.confirmation_status == "suggested" for field in updated.fields)
    assert (await continuous.get_continuous_preview(db, state["preview_id"], USER))["status"] == "stale"
    with pytest.raises(ValueError, match="DRAFT_VERSION_CONFLICT"):
        await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "stale-confirm")
    await db.rollback()
    revised = await continuous.ensure_continuous_preview(db, state["draft_id"], USER, 1, "preview-edit")
    await db.commit()
    assert revised["preview_id"] != state["preview_id"]
    await generate(db, real_db_engine, monkeypatch)
    assert (await continuous.get_continuous_preview(db, revised["preview_id"], USER))["generation_status"] == "completed"


@pytest.mark.asyncio
async def test_build_key_conflict_and_revocation_hides_preview(real_db_session, real_db_engine, monkeypatch):
    db = real_db_session
    await seed(db)
    state = await continuous.build_continuous_draft(db, USER, "personal", refresh=False, idempotency_key="build-stable")
    await db.commit()
    with pytest.raises(TaskError):
        await continuous.build_continuous_draft(db, USER, "personal", refresh=True, idempotency_key="build-stable")
    await db.rollback()
    await db.execute(text("UPDATE ai_consent_grant SET revoked_at=UTC_TIMESTAMP() WHERE user_id=:user"), {"user": USER})
    await db.commit()
    await generate(db, real_db_engine, monkeypatch)
    assert not (await continuous.build_state(db, USER))["consent_granted"]
    with pytest.raises(PermissionError):
        await continuous.get_continuous_preview(db, state["personal"]["preview_id"], USER)
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_profile_revision WHERE user_id=:user"), {"user": USER})).scalar_one() == 0


@pytest.mark.asyncio
async def test_other_subject_task_and_session_survive_confirmation(real_db_session, real_db_engine, monkeypatch):
    db = real_db_session
    session, turn = await seed(db)
    personal = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    for dimension, category, content in (
        ("personality_social", "personality", "希望对方开朗"),
        ("emotional_expression", "values", "希望对方尊重沟通"),
        ("future_expectations", "life_plan", "希望认真交往"),
    ):
        await add_candidate(db, session, turn, "ideal_partner", dimension, category, content)
    await db.commit()
    ideal = await build(db, "ideal_partner")
    await continuous.confirm_continuous_preview(db, personal["preview_id"], USER, 0, "personal-first")
    await db.commit()
    active = await profile.load_owned_active_session(db, session, USER, continuous=True)
    assert active.session_id == session
    await db.commit()
    await generate(db, real_db_engine, monkeypatch)
    second = await continuous.get_continuous_preview(db, ideal["preview_id"], USER)
    assert second["generation_status"] == "completed", second
    result = await continuous.confirm_continuous_preview(db, ideal["preview_id"], USER, 0, "ideal-second")
    await db.commit()
    assert result["subject"] == "ideal_partner"
    state = await continuous.build_state(db, USER)
    assert state["personal"]["status"] == state["ideal_partner"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_concurrent_confirmation_creates_one_revision(real_db_session, real_db_engine, monkeypatch):
    db = real_db_session
    await seed(db)
    state = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)

    async def confirm(key):
        async with factory() as connection:
            result = await continuous.confirm_continuous_preview(connection, state["preview_id"], USER, 0, key)
            await connection.commit()
            return result

    left, right = await asyncio.gather(confirm("concurrent-a"), confirm("concurrent-b"))
    assert left["revision_id"] == right["revision_id"]
    assert left["replayed"] != right["replayed"]
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_profile_revision WHERE user_id=:user"), {"user": USER})).scalar_one() == 1


@pytest.mark.asyncio
async def test_confirmed_edit_is_not_overwritten_by_consumed_candidate(real_db_session, real_db_engine, monkeypatch):
    db = real_db_session
    session, turn = await seed(db)
    state = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    preview = await continuous.get_continuous_preview(db, state["preview_id"], USER)
    key = preview["fields"][0]["field_key"]
    await profile.confirm_profile_draft(db, state["draft_id"], USER, [ProfileDraftFieldPatchRequest(
        field_key=key, action=ProfileFieldPatchAction.REPLACE, value="本人修订的表达", expected_revision=0)], 0, "edit-before-confirm")
    revised = await continuous.ensure_continuous_preview(db, state["draft_id"], USER, 1, "new-review")
    await db.commit()
    await generate(db, real_db_engine, monkeypatch)
    await continuous.confirm_continuous_preview(db, revised["preview_id"], USER, 1, "confirm-edit")
    await db.commit()
    with pytest.raises(LookupError, match="CONTINUOUS_BUILD_NOT_READY"):
        await build(db)
    await db.rollback()
    await add_candidate(db, session, turn, "personal", "lifestyle", "routine", "喜欢早睡")
    await db.commit()
    update = await build(db)
    fields = (await db.execute(text("SELECT field_key,content FROM ai_profile_draft_field WHERE draft_id=:id"), {"id": update["draft_id"]})).mappings().all()
    assert len(fields) == 4
    assert next(field["content"] for field in fields if field["field_key"] == key) == "本人修订的表达"


@pytest.mark.asyncio
async def test_revoke_regrant_does_not_restore_portraits_or_history(real_db_session, real_db_engine, monkeypatch):
    from app.services.ai.journey import compose_continuous_context
    db = real_db_session
    await seed(db)
    state = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "before-revoke")
    vector = await profile._load_revision_vector(db, USER)
    revoked = await revoke_consent(db, USER, "profile_text_extract", "revoke-actual", vector.privacy)
    await grant_consent(db, USER, "profile_text_extract", AiConsentGrantRequest(
        consent_version=VERSION, policy_revision=POLICY), "regrant-actual", revoked.privacy_revision)
    await db.commit()
    restored = await continuous.build_state(db, USER)
    assert restored["consent_granted"]
    assert restored["personal"]["published_revision_id"] is None
    assert restored["personal"]["draft_id"] is None
    assert await profile.load_published_narrative(db, USER, "personal") is None
    assert await continuous.list_continuous_turns(db, USER, 50) == ([], None)
    context = await compose_continuous_context(db, user_id=USER)
    assert "喜欢安静阅读" not in context and "重视平等沟通" not in context


@pytest.mark.asyncio
async def test_grant_snapshot_uses_database_second_precision(real_db_session, monkeypatch):
    from datetime import UTC, datetime
    from app.services.ai import consents
    now = datetime.now(UTC).replace(microsecond=900000)

    class Clock:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(consents, "datetime", Clock)
    await seed(real_db_session)
    consent = await continuous._require_consent(real_db_session, USER)
    assert consent["granted_at"] == now.replace(tzinfo=None, microsecond=0).isoformat()
    assert len(await continuous._candidates(real_db_session, USER, "personal", consent)) == 3


@pytest.mark.asyncio
async def test_reviewed_memory_repeated_corrections_and_tombstone(real_db_session):
    from dataclasses import replace
    from app.services.ai.continuous_memory import forward_continuous_confirmation_to_memory
    from app.services.ai.memory.policy import MemoryPolicy
    from app.services.ai.memory.service import MemoryClaimStateDenied, MemoryService
    from app.services.ai.memory.projections import MemoryProjectionService

    db = real_db_session
    await seed(db)
    consent = await continuous._require_consent(db, USER)
    service = MemoryService(db)
    canonical = MemoryPolicy.canonical_key("personal", "lifestyle", MemoryPolicy.candidate_identity("structured", "interest_tags", None, None))
    await service.propose(owner_user_id=USER, subject="personal", canonical_key=canonical,
        dimension="lifestyle", value=["旧候选"], confidence=0.95, source_kind="user_explicit",
        fact_kind="about_user", idempotency_key="old-shadow-candidate")
    field = profile.ProfileDraftField(field_key="interest_tags", subject="personal", value=["阅读"],
        confirmation_status="confirmed")
    draft = profile.ProfileDraft(draft_id="reviewed-memory", owner_user_id=USER, subject="personal",
        schema_version=continuous.CONTINUOUS_DRAFT_SCHEMA_VERSION, fields=(field,))

    for version, value in enumerate((["阅读"], ["徒步"], ["绘画"]), 1):
        field = replace(field, value=value)
        await forward_continuous_confirmation_to_memory(db, replace(draft, fields=(field,)), (field,),
            revision_id=version, source_revision={}, consent_snapshot=consent,
            idempotency_key=f"reviewed-{version}")
        row = await service.read_claim_by_canonical(owner_user_id=USER, subject="personal", canonical_key=canonical)
        assert row["status"] == "confirmed" and json.loads(row["value_json"]) == value
        entries = await MemoryProjectionService(db)._collect_entries(USER, "personal_profile")
        assert any(entry["value"] == value for entry in entries)
    events = (await db.execute(text("SELECT event_type, source_ref FROM ai_memory_event WHERE owner_user_id=:user"), {"user": USER})).mappings().all()
    assert sum(event["event_type"] == "claim_user_corrected" for event in events) == 3
    assert sum(event["event_type"] == "claim_confirmed" for event in events) == 3
    outbox_count = (await db.execute(text("SELECT COUNT(*) FROM derivation_outbox WHERE aggregate_type='ai_memory' AND aggregate_id=:user"), {"user": str(USER)})).scalar_one()
    assert outbox_count == len(events)

    # 普通单项确认仍不能确认 corrected 状态，必须重新审阅完整稿。
    await service.correct_claim(owner_user_id=USER, claim_id=row["claim_id"], expected_revision=row["last_event_seq"], value=["手动修订"], idempotency_key="manual-correction")
    corrected = await service.read_claim_by_canonical(owner_user_id=USER, subject="personal", canonical_key=canonical)
    with pytest.raises(MemoryClaimStateDenied):
        await service.confirm_claim(owner_user_id=USER, claim_id=row["claim_id"], expected_revision=corrected["last_event_seq"], importance=0.5, idempotency_key="ordinary-denied")
    deleted = replace(field, confirmation_status="deleted")
    await forward_continuous_confirmation_to_memory(db, replace(draft, fields=(deleted,)), (),
        revision_id=4, source_revision={}, consent_snapshot=consent, idempotency_key="reviewed-delete")
    assert await MemoryProjectionService(db)._collect_entries(USER, "personal_profile") == []
    assert (await service.read_suppression(owner_user_id=USER, subject="personal", namespace="moxiang", canonical_key=canonical))["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["memory", "derived_results"])
async def test_failure_rolls_back_entire_confirmation(real_db_session, real_db_engine, monkeypatch, failure_stage):
    from sqlalchemy.exc import NoSuchTableError
    from app.services.ai.memory.service import MemoryService
    db = real_db_session
    await seed(db)
    state = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    original = MemoryService.confirm_claim

    async def fail_after_write(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("synthetic memory write failure")
    original_execute = db.execute

    async def fail_invalidation(statement, *args, **kwargs):
        if "UPDATE ai_search_result" in str(statement):
            raise NoSuchTableError("synthetic derived invalidation failure")
        return await original_execute(statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure_stage == "memory":
            patch.setattr(MemoryService, "confirm_claim", fail_after_write)
        else:
            patch.setattr(db, "execute", fail_invalidation)
        with pytest.raises((RuntimeError, NoSuchTableError), match="synthetic"):
            await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "rollback-confirm")
        await db.rollback()
    for table, owner in (("ai_profile_revision", "user_id"), ("ai_profile_summary", "user_id"), ("ai_memory_event", "owner_user_id"), ("ai_memory_claim", "owner_user_id")):
        assert (await db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {owner}=:user"), {"user": USER})).scalar_one() == 0
    assert (await db.execute(text("SELECT COUNT(*) FROM api_idempotency_record WHERE user_id=:user AND idempotency_key='rollback-confirm'"), {"user": USER})).scalar_one() == 0
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_profile_projection_status WHERE user_id=:user"), {"user": USER})).scalar_one() == 0
    assert (await continuous.get_continuous_preview(db, state["preview_id"], USER))["status"] == "active"
    result = await continuous.confirm_continuous_preview(db, state["preview_id"], USER, 0, "rollback-confirm")
    await db.commit()
    assert result["revision_id"]
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_memory_claim WHERE owner_user_id=:user AND status='confirmed'"), {"user": USER})).scalar_one() == 3


@pytest.mark.asyncio
async def test_confirmation_invalidates_projection_and_regrant_excludes_old_memory(real_db_session, real_db_engine, monkeypatch):
    from app.services.ai.memory.projections import MemoryProjectionService
    db = real_db_session
    session, turn = await seed(db)
    first = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    await continuous.confirm_continuous_preview(db, first["preview_id"], USER, 0, "projection-first")
    service = MemoryProjectionService(db)
    dimension = dict(owner_user_id=USER, function_key="search", purpose="candidate_filter", data_category="personal_profile")
    projection = await service.build(**dimension)
    assert len(projection["entries"]) == 3
    assert await service.read_active(**dimension) is not None
    await db.commit()

    await add_candidate(db, session, turn, "personal", "lifestyle", "routine", "喜欢早睡")
    second = await build(db, refresh=True)
    await generate(db, real_db_engine, monkeypatch)
    await continuous.confirm_continuous_preview(db, second["preview_id"], USER, 0, "projection-second")
    assert await service.read_active(**dimension) is None
    statuses = (await db.execute(text("SELECT status FROM ai_profile_projection_status WHERE user_id=:user"), {"user": USER})).scalars().all()
    assert statuses and set(statuses) == {"pending"}
    rebuilt = await service.build(**dimension)
    assert len(rebuilt["entries"]) == 4
    assert rebuilt["projection_version"] > projection["projection_version"]
    await db.commit()

    # 不执行 cleanup：确认旧行仍存在，重授权也不能把它们收回投影。
    vector = await profile._load_revision_vector(db, USER)
    revoked = await revoke_consent(db, USER, "profile_text_extract", "projection-revoke", vector.privacy)
    await grant_consent(db, USER, "profile_text_extract", AiConsentGrantRequest(
        consent_version=VERSION, policy_revision=POLICY), "projection-regrant", revoked.privacy_revision)
    assert (await db.execute(text("SELECT COUNT(*) FROM ai_memory_claim WHERE owner_user_id=:user AND status='confirmed'"), {"user": USER})).scalar_one() == 4
    assert await service._collect_entries(USER, "personal_profile") == []
    assert await service.read_active(**dimension) is None
    assert (await service.build(**dimension))["entries"] == []

    # 保留旧 claims，模拟撤权清理 Worker 迟到，而不是靠删除旧行让隔离通过。
    from app.services.ai.memory.purge import current_owner_sequence, purge_memory_for_owner
    from app.services.ai.memory.projections import derive_consent_snapshot_id
    fence = await current_owner_sequence(db, USER)
    await db.execute(text("UPDATE ai_task SET next_run_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE owner_user_id=:user AND status IN ('queued','retry_wait')"), {"user": USER})
    session = await profile.create_master_session(db, USER, ProfileSubject.PERSONAL, VERSION)
    turn = uuid.uuid4().hex
    await db.execute(text("INSERT INTO ai_profile_turn (turn_id,session_id,client_turn_id,user_id,turn_no,answer_text) VALUES (:turn,:session,:turn,:user,1,'重新提供并确认原来的三项信息')"), {"turn": turn, "session": session.session_id, "user": USER})
    for dim, category, content in (
        ("personality_social", "personality", "喜欢安静阅读"),
        ("emotional_expression", "values", "重视平等沟通"),
        ("future_expectations", "life_plan", "认真交往"),
    ):
        await add_candidate(db, session.session_id, turn, "personal", dim, category, content)
    await db.commit()
    renewed = await build(db)
    await generate(db, real_db_engine, monkeypatch)
    await continuous.confirm_continuous_preview(db, renewed["preview_id"], USER, 0, "regrant-same-values")
    new_events = (await db.execute(text("SELECT event_type FROM ai_memory_event WHERE owner_user_id=:user AND server_seq>:fence"), {"user": USER, "fence": fence})).scalars().all()
    assert new_events.count("claim_confirmed") == 3
    assert new_events.count("claim_user_corrected") == 3
    restored = await service.build(**dimension)
    assert len(restored["entries"]) == 3
    # 重新审阅会更新 source_kind/stability，内容与 claim 身份不变但哈希可变。
    assert {(e["claim_id"], e["value"]) for e in restored["entries"]} == {(e["claim_id"], e["value"]) for e in projection["entries"]}
    consent = await service._load_active_consent(USER)
    assert restored["consent_snapshot_id"] == derive_consent_snapshot_id(consent)
    assert restored["consent_snapshot_id"] != projection["consent_snapshot_id"]
    active = await service.read_active(**dimension)
    assert active is not None and len(active["entries"]) == 3
    await purge_memory_for_owner(db, USER, fence_seq=fence)
    assert len(await service._collect_entries(USER, "personal_profile")) == 3
    assert await service.read_active(**dimension) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "invalidated"])
async def test_same_hash_projection_rebinds_current_authorization(real_db_session, status):
    from app.services.ai.memory.projections import MemoryProjectionService
    db = real_db_session
    await seed(db)
    service = MemoryProjectionService(db)
    dimension = dict(owner_user_id=USER, function_key="search", purpose="candidate_filter", data_category="personal_profile")
    first = await service.build(**dimension)
    # 相同内容不能使旧授权/策略绑定走 active 快路径，也不能原样复活历史版本。
    await db.execute(text("UPDATE ai_memory_projection SET status=:status, consent_snapshot_id='cs_stale', policy_revision='old-policy' WHERE projection_id=:id"), {"status": status, "id": first["projection_id"]})
    assert await service.read_active(**dimension) is None
    rebound = await service.build(**dimension)
    assert rebound["projection_id"] == first["projection_id"]
    assert rebound["consent_snapshot_id"] == first["consent_snapshot_id"]
    assert rebound["policy_revision"] == POLICY
    assert await service.read_active(**dimension) is not None

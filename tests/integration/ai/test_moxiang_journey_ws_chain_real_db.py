"""批次3 #26：墨相师旅程 WS 全链路集成测试（mock LLM + 真实 MySQL）。

覆盖 legacy WebSocket 协议链路，以及 continuous_v2 的双主体候选、Worker
成稿、REST 恢复与整份确认。数据库连接全部使用 NullPool，Provider/语音均为
确定性测试 seam；测试不绕过真实 HTTP/WS 鉴权。
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import voice_moxiang
from app.core.config import settings
from app.core.security import create_token, hash_token
from app.db import session as app_db_session
from app.db.session import get_db
from app.main import app
from app.schemas.ai_common import AiConsentGrantRequest
from app.schemas.ai_profile import (
    ProfileDraftFieldPatchRequest,
    ProfileFieldPatchAction,
)
from app.services.ai.base import ExtractedField
from app.services.ai.consents import grant_consent
from app.services.ai.profile import confirm_profile_draft, publish_profile_draft
from app.services.ai.providers import MockAIProvider
from app.services.voice import auth as voice_auth
from app.services.voice import gateway as voice_gateway_mod
from app.workers import ai_worker

TEST_DATABASE_URL = os.getenv(
    "AI_TEST_DATABASE_URL",
    "mysql+aiomysql://root:@127.0.0.1:3307/xuanshiai_ai_test",
)

USER_ID = 9_876_543_460
CONSENT_VERSION = "profile-text-v1"
POLICY_REVISION = "ai-policy-2026-08-07-v1"

_TURNS = (
    "我住杭州，周末喜欢旅行和看展。",
    "我从事互联网技术工作，也喜欢户外活动。",
    "我想认真交往，以结婚为目标。",
    "我目前未婚，本科学历，身高一米七二。",
)


class _NoNetworkVoiceProvider:
    """显式 fake 语音 provider：本测试不触发任何语音调用，误调用即失败。"""

    async def transcribe(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("ws_chain 测试不应触发语音转写")

    async def synthesize(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("ws_chain 测试不应触发语音合成")

    async def stream_transcribe(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("ws_chain 测试不应触发流式语音识别")


def _fake_voice_provider_factory(*args: object, **kwargs: object):
    return _NoNetworkVoiceProvider()


_ORIGINAL_DUAL_FIXTURE = MockAIProvider.structured_extract_dual_fixture


async def _rich_dual_fixture(self: MockAIProvider, request):
    """测试 seam：一条混合表达为两个主体各提供至少三维确定性证据。"""
    base = await _ORIGINAL_DUAL_FIXTURE(self, request)
    quote = "我比较安静，周末喜欢旅行；我希望对方愿意沟通、认真回应。"
    extra_fields = (
        ExtractedField(
            field_key="age",
            subject="personal",
            value=30,
            source_quote=quote,
            confidence=0.90,
            assertion_mode="explicit",
            policy_revision=request.policy_revision,
        ),
        ExtractedField(
            field_key="relationship_goal",
            subject="personal",
            value="marriage",
            source_quote=quote,
            confidence=0.90,
            assertion_mode="explicit",
            policy_revision=request.policy_revision,
        ),
        ExtractedField(
            field_key="age",
            subject="ideal_partner",
            value={"min": 28, "max": 38},
            source_quote=quote,
            confidence=0.88,
            assertion_mode="explicit",
            policy_revision=request.policy_revision,
        ),
        ExtractedField(
            field_key="interest_tags",
            subject="ideal_partner",
            value=["阅读"],
            source_quote=quote,
            confidence=0.88,
            assertion_mode="explicit",
            policy_revision=request.policy_revision,
        ),
        ExtractedField(
            field_key="relationship_goal",
            subject="ideal_partner",
            value=["marriage"],
            source_quote=quote,
            confidence=0.88,
            assertion_mode="explicit",
            policy_revision=request.policy_revision,
        ),
    )
    return base.model_copy(update={"fields": (*base.fields, *extra_fields)})


async def _seed_auth() -> tuple[int, str]:
    """仅 seed 本测试用户的真实 users/user_session，并返回动态 token。"""
    await _cleanup_test_user()
    engine, factory = _factory()
    try:
        async with factory() as db:
            await db.execute(
                sql_text(
                    "INSERT INTO users (id, phone, nickname, status) "
                    "VALUES (:uid, :phone, :nickname, 1)"
                ),
                {"uid": USER_ID, "phone": str(USER_ID), "nickname": "ws-chain-test"},
            )
            await db.execute(
                sql_text(
                    "INSERT INTO user_session "
                    "(user_id, refresh_token_hash, device_id, platform, access_expire_at, "
                    "refresh_expire_at, status) VALUES (:uid, :refresh, :device, :platform, "
                    "UTC_TIMESTAMP() + INTERVAL 1 HOUR, UTC_TIMESTAMP() + INTERVAL 1 DAY, 1)"
                ),
                {
                    "uid": USER_ID,
                    "refresh": hash_token(uuid.uuid4().hex),
                    "device": "it-ws-chain",
                    "platform": "pytest",
                },
            )
            session_id = int(
                (
                    await db.execute(
                        sql_text(
                            "SELECT id FROM user_session WHERE user_id = :uid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"uid": USER_ID},
                    )
                ).scalar_one()
            )
            token = create_token(
                user_id=USER_ID,
                session_id=session_id,
                token_type="access",
                expires_delta=timedelta(hours=1),
            )
            await db.execute(
                sql_text("UPDATE user_session SET access_token_hash = :hash WHERE id = :sid"),
                {"hash": hash_token(token), "sid": session_id},
            )
            await db.commit()
            return session_id, token
    finally:
        await engine.dispose()


async def _cleanup_test_user() -> None:
    """只清理 USER_ID 的 AI/登录数据，不触碰其他用户。"""
    engine, factory = _factory()
    try:
        async with factory() as db:
            statements = (
                "DELETE FROM ai_profile_preview WHERE user_id = :uid",
                "DELETE FROM ai_profile_draft_field WHERE draft_id IN (SELECT draft_id FROM ai_profile_draft WHERE user_id = :uid)",
                "DELETE FROM ai_profile_draft WHERE user_id = :uid",
                "DELETE FROM ai_profile_build_invite WHERE user_id = :uid",
                "DELETE FROM ai_profile_candidate WHERE user_id = :uid",
                "DELETE FROM ai_profile_turn WHERE user_id = :uid",
                "DELETE FROM ai_profile_session WHERE user_id = :uid",
                "DELETE FROM ai_profile_revision_field WHERE revision_id IN (SELECT id FROM ai_profile_revision WHERE user_id = :uid)",
                "DELETE FROM ai_profile_summary WHERE user_id = :uid",
                "DELETE FROM ai_profile_revision WHERE user_id = :uid",
                "DELETE FROM ai_profile_projection_status WHERE user_id = :uid",
                "DELETE FROM ai_feature_projection WHERE subject_user_id = :uid",
                "DELETE FROM ai_task WHERE owner_user_id = :uid",
                "DELETE FROM ai_consent_operation WHERE user_id = :uid",
                "DELETE FROM ai_consent_grant WHERE user_id = :uid",
                "DELETE FROM api_idempotency_record WHERE user_id = :uid",
                "DELETE FROM ai_memory_suppression WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_state WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_insight WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_claim WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_observation WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_event WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_owner_sequence WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_projection_grant WHERE owner_user_id = :uid",
                "DELETE FROM ai_memory_projection WHERE owner_user_id = :uid",
                "DELETE FROM user_session WHERE user_id = :uid",
                "DELETE FROM users WHERE id = :uid",
            )
            for statement in statements:
                await db.execute(sql_text(statement), {"uid": USER_ID})
            await db.commit()
    finally:
        await engine.dispose()


def _install_db_factory(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    """HTTP、WS 鉴权和 WS 服务统一使用独立 NullPool factory。"""

    async def override_get_db():
        async with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setattr(voice_moxiang, "_db_session_factory", factory)
    monkeypatch.setattr(app_db_session, "session_factory", factory)
    monkeypatch.setattr(voice_auth, "session_factory", factory)


def _factory():
    # TestClient WS 循环与 pytest-asyncio/Worker 循环相互独立，禁止共享 QueuePool。
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _grant_consent() -> None:
    engine, factory = _factory()
    try:
        async with factory() as db:
            await grant_consent(
                db,
                USER_ID,
                "profile_text_extract",
                AiConsentGrantRequest(
                    consent_version=CONSENT_VERSION,
                    policy_revision=POLICY_REVISION,
                ),
                "moxiang-ws-chain-grant-1",
                0,
            )
            await db.commit()
    finally:
        await engine.dispose()


def _recv_until(ws, predicate, *, limit: int = 400):
    """持续读取 WS 消息直到 predicate 命中；返回命中消息与前序消息。"""
    seen: list[dict] = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(
        f"未在 {limit} 条消息内等到目标；已收到类型={[m.get('type') for m in seen]}"
    )


@pytest_asyncio.fixture
async def real_ws_auth() -> tuple[int, str]:
    session_id, token = await _seed_auth()
    try:
        yield session_id, token
    finally:
        await _cleanup_test_user()


@pytest.mark.asyncio
async def test_ws_journey_full_chain_invite_confirm_publish_project(
    ai_test_environment: dict[str, str],
    real_ws_auth: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy WS 全链路：真实登录 session 经过票据、邀请、确认和投影。"""
    _login_session_id, token = real_ws_auth
    await _grant_consent()
    monkeypatch.setattr(settings, "ai_provider", "mock")
    monkeypatch.setattr(settings, "ai_moxiang_journey_enabled", True)
    monkeypatch.setattr(settings, "ai_profile_enabled", True)
    monkeypatch.setattr(settings, "ai_master_enabled", True)

    ws_engine, ws_factory = _factory()
    worker_engine, worker_factory = _factory()
    _install_db_factory(monkeypatch, ws_factory)
    monkeypatch.setattr(ai_worker, "session_factory", worker_factory)
    monkeypatch.setattr(
        voice_gateway_mod, "get_voice_provider", _fake_voice_provider_factory
    )

    client = TestClient(app)
    ticket_response = client.post(
        "/api/v1/voice/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = ticket_response.json()["ticket"]
    try:
        with client.websocket_connect(
            f"/api/v1/voice/moxiang-master?ticket={ticket}"
        ) as ws:
            ws.send_json(
                {
                    "type": "session_start",
                    "mode": "moxiang_journey",
                    "subject": "personal",
                    "consentVersion": CONSENT_VERSION,
                }
            )
            ready, _ = _recv_until(ws, lambda m: m.get("type") == "journey_ready")
            assert ready["subject"] == "personal"
            assert ready["session_id"]
            opening, _ = _recv_until(
                ws, lambda m: m.get("type") == "ai_reply" and m.get("opening") is True
            )
            assert "我是知遇" in opening["text"]

            for idx, answer in enumerate(_TURNS):
                ws.send_json(
                    {
                        "type": "text_message",
                        "text": answer,
                        "clientTurnId": f"ws-chain-turn-{idx}",
                    }
                )
                _recv_until(
                    ws,
                    lambda m: m.get("type") == "ai_reply"
                    and m.get("opening") is False,
                )

            assert await _run_worker(4) == (4, 4, 0)
            invite, _ = _recv_until(ws, lambda m: m.get("type") == "build_invite")
            assert invite["subject"] == "personal"
            assert invite["dimension_count"] >= 3
            assert invite["summary_items"]
            assert all(item.get("profile_dimension") for item in invite["summary_items"])
            invite_id = str(invite["invite_id"])

            ws.send_json(
                {
                    "type": "build_invite_accept",
                    "subject": "personal",
                    "invite_id": invite_id,
                }
            )
            resolved, _ = _recv_until(
                ws, lambda m: m.get("type") == "build_invite_resolved"
            )
            assert resolved["resolution"] == "accepted"
            card, _ = _recv_until(ws, lambda m: m.get("type") == "confirm_card")
            assert card["draft_id"]
            assert card["items"]
            draft_id = str(card["draft_id"])
            expected_revision = int(card["expected_revision"])

        field_keys = await _draft_structured_keys(draft_id)
        assert len(field_keys) >= 7
        published = await _confirm_and_publish(field_keys, draft_id, expected_revision)
        assert published["task_id"]
        claimed, completed, failed = await _run_worker_collect(6)
        assert claimed >= 1 and completed >= 1 and failed == 0
        kinds = await _projection_kinds()
        assert {"personal_searchable", "personal_compatibility"} <= kinds
    finally:
        await _dispose_engines(ws_engine, worker_engine)


@pytest.mark.asyncio
async def test_ws_continuous_v2_dual_subject_rest_recovery_and_confirm(
    ai_test_environment: dict[str, str],
    real_ws_auth: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """continuous_v2：一条混合表达只落一轮，双主体成稿可 REST 恢复并先确认一份。"""
    _login_session_id, token = real_ws_auth
    await _grant_consent()
    monkeypatch.setattr(settings, "ai_provider", "mock")
    monkeypatch.setattr(settings, "ai_moxiang_journey_enabled", True)
    monkeypatch.setattr(settings, "ai_profile_enabled", True)
    monkeypatch.setattr(settings, "ai_master_enabled", True)
    monkeypatch.setattr(
        MockAIProvider, "structured_extract_dual_fixture", _rich_dual_fixture
    )

    ws_engine, ws_factory = _factory()
    worker_engine, worker_factory = _factory()
    _install_db_factory(monkeypatch, ws_factory)
    monkeypatch.setattr(ai_worker, "session_factory", worker_factory)
    monkeypatch.setattr(
        voice_gateway_mod, "get_voice_provider", _fake_voice_provider_factory
    )

    client = TestClient(app)
    try:
        ticket_response = client.post(
            "/api/v1/voice/ws-ticket",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ticket_response.status_code == 200, ticket_response.text
        ticket = ticket_response.json()["ticket"]
        with client.websocket_connect(
            f"/api/v1/voice/moxiang-master?ticket={ticket}"
        ) as ws:
            ws.send_json(
                {
                    "type": "session_start",
                    "mode": "moxiang_journey",
                    "subject": "personal",
                    "consentVersion": CONSENT_VERSION,
                    "flow_version": "continuous_v2",
                }
            )
            continuous_state, _ = _recv_until(
                ws, lambda m: m.get("type") == "continuous_state"
            )
            assert continuous_state["state"]["flow_version"] == "continuous_v2"
            ready, _ = _recv_until(ws, lambda m: m.get("type") == "journey_ready")
            journey_session_id = str(ready["session_id"])
            assert journey_session_id

            mixed_text = "我比较安静，周末喜欢旅行；我希望对方愿意沟通、认真回应。"
            client_turn_id = "continuous-mixed-turn-1"
            message = {
                "type": "text_message",
                "text": mixed_text,
                "clientTurnId": client_turn_id,
            }
            ws.send_json(message)
            queued, _ = _recv_until(
                ws,
                lambda m: m.get("type") == "extraction_status"
                and m.get("status") == "queued",
            )
            candidate_task_id = str(queued["task_id"])
            _recv_until(
                ws,
                lambda m: m.get("type") == "ai_reply" and m.get("opening") is False,
            )

            # 相同 clientTurnId 重放：真实 submit_journey_turn 幂等，不新增 turn/task。
            ws.send_json(message)
            replay_queued, _ = _recv_until(
                ws,
                lambda m: m.get("type") == "extraction_status"
                and m.get("status") == "queued",
            )
            assert str(replay_queued["task_id"]) == candidate_task_id
            _recv_until(
                ws,
                lambda m: m.get("type") == "ai_reply" and m.get("opening") is False,
            )

            assert await _run_worker(1) == (1, 1, 0)
            _recv_until(
                ws,
                lambda m: m.get("type") == "extraction_status"
                and m.get("task_id") == candidate_task_id
                and m.get("status") == "completed",
            )

        async with ws_factory() as verify_db:
            turn_count = await verify_db.scalar(
                sql_text(
                    "SELECT COUNT(*) FROM ai_profile_turn WHERE user_id=:uid "
                    "AND role='user' AND client_turn_id=:client_turn_id"
                ),
                {"uid": USER_ID, "client_turn_id": client_turn_id},
            )
            assert int(turn_count or 0) == 1
            candidates = (
                await verify_db.execute(
                    sql_text(
                        "SELECT subject, profile_dimension FROM ai_profile_candidate "
                        "WHERE user_id=:uid AND status='active'"
                    ),
                    {"uid": USER_ID},
                )
            ).mappings().all()
            assert {str(row["subject"]) for row in candidates} == {
                "personal",
                "ideal_partner",
            }
            for subject in ("personal", "ideal_partner"):
                assert len(
                    {
                        str(row["profile_dimension"])
                        for row in candidates
                        if str(row["subject"]) == subject
                    }
                ) >= 3

        auth_headers = {"Authorization": f"Bearer {token}"}
        for subject, key in (
            ("personal", "continuous-personal-build-1"),
            ("ideal_partner", "continuous-ideal-build-1"),
        ):
            build_response = client.post(
                f"/api/v1/ai/moxiang/continuous/build?subject={subject}",
                headers={**auth_headers, "Idempotency-Key": key},
                json={"refresh": False},
            )
            assert build_response.status_code == 200, build_response.text

        # 两个 profile_preview 任务都由真实 Worker 处理，随后 REST 状态应可恢复。
        assert await _run_worker(2) == (2, 2, 0)
        state_response = client.get(
            "/api/v1/ai/moxiang/continuous/state", headers=auth_headers
        )
        assert state_response.status_code == 200, state_response.text
        state = state_response.json()
        assert state["flow_version"] == "continuous_v2"
        assert state["session_id"] == journey_session_id
        assert state["personal"]["status"] == "awaiting_confirmation"
        assert state["ideal_partner"]["status"] == "awaiting_confirmation"
        personal_task_id = state["personal"]["task_id"]
        ideal_preview_id = state["ideal_partner"]["preview_id"]
        ideal_revision = state["ideal_partner"]["expected_revision"]
        assert personal_task_id and ideal_preview_id and ideal_revision is not None

        turns_response = client.get(
            "/api/v1/ai/moxiang/continuous/turns", headers=auth_headers
        )
        assert turns_response.status_code == 200, turns_response.text
        user_turns = [
            row for row in turns_response.json()["turns"] if row["role"] == "user"
        ]
        assert len(user_turns) == 1
        assert user_turns[0]["client_turn_id"] == client_turn_id

        preview_response = client.get(
            f"/api/v1/ai/profile-previews/{ideal_preview_id}", headers=auth_headers
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["generation_status"] == "completed"
        assert preview["content"]

        recovered = client.get(
            "/api/v1/ai/moxiang/continuous/state", headers=auth_headers
        ).json()
        assert recovered["personal"]["task_id"] == personal_task_id

        confirm_response = client.post(
            f"/api/v1/ai/profile-previews/{ideal_preview_id}/confirm",
            headers={**auth_headers, "Idempotency-Key": "continuous-ideal-confirm-1"},
            json={"expected_revision": ideal_revision},
        )
        assert confirm_response.status_code == 202, confirm_response.text
        after_confirm = client.get(
            "/api/v1/ai/moxiang/continuous/state", headers=auth_headers
        ).json()
        assert after_confirm["ideal_partner"]["status"] == "confirmed"
        assert after_confirm["personal"]["status"] == "awaiting_confirmation"
        assert after_confirm["personal"]["task_id"] == personal_task_id
    finally:
        await _dispose_engines(ws_engine, worker_engine)


async def _dispose_engines(*engines) -> None:
    for engine in engines:
        await engine.dispose()


async def _run_worker(batch: int):
    return await ai_worker._run_round("it-ws-chain-candidates", batch)


async def _run_worker_collect(batch: int):
    return await ai_worker._run_round("it-ws-chain-projection", batch)


async def _draft_structured_keys(draft_id: str) -> list[str]:
    engine, factory = _factory()
    try:
        async with factory() as db:
            rows = (
                await db.execute(
                    sql_text(
                        "SELECT field_key FROM ai_profile_draft_field "
                        "WHERE draft_id = :draft_id AND field_kind = 'structured' "
                        "ORDER BY field_key"
                    ),
                    {"draft_id": draft_id},
                )
            ).scalars().all()
        return [str(key) for key in rows]
    finally:
        await engine.dispose()


async def _confirm_and_publish(field_keys, draft_id: str, expected_revision: int):
    engine, factory = _factory()
    try:
        async with factory() as db:
            confirmed = await confirm_profile_draft(
                db,
                draft_id,
                USER_ID,
                [
                    ProfileDraftFieldPatchRequest(
                        field_key=key,
                        action=ProfileFieldPatchAction.CONFIRM,
                        expected_revision=expected_revision,
                    )
                    for key in field_keys
                ],
                expected_revision=expected_revision,
                idempotency_key="ws-chain-confirm-1",
            )
            published = await publish_profile_draft(
                db,
                draft_id,
                USER_ID,
                expected_revision=confirmed.revision,
                idempotency_key="ws-chain-publish-1",
            )
            await db.commit()
            return {"task_id": published.task_id}
    finally:
        await engine.dispose()


async def _projection_kinds():
    engine, factory = _factory()
    try:
        async with factory() as db:
            rows = (
                await db.execute(
                    sql_text(
                        "SELECT DISTINCT projection_kind FROM ai_feature_projection "
                        "WHERE subject_user_id = :uid AND status = 'active'"
                    ),
                    {"uid": USER_ID},
                )
            ).scalars().all()
        return {str(kind) for kind in rows}
    finally:
        await engine.dispose()

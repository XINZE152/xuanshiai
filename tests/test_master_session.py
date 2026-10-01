"""master 会话：创建/复用/助手回复落库（fake store）。

形态与 tests/test_ai_profile_sessions.py 一致（ProfileStore 内存假库，不依赖
真实数据库）。覆盖简报三断言：新会话 kind='master' 且 status=draft；旧 build
会话保留并标记 stale，重复进入只复用新 master；助手回复以 role='assistant'
turn 落库后可读回。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.schemas.ai_profile import ProfileSubject
from app.services.ai.profile import (
    ProfileSessionNotFound,
    create_master_session,
    persist_master_assistant_reply,
)
from tests.test_ai_profile_sessions import ProfileStore, _now


@pytest.mark.asyncio
async def test_create_master_session_inserts_kind_master() -> None:
    store = ProfileStore()  # 构造时自动为 user 10 种子 consent
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    assert session.session_kind == "master"
    assert session.status.value == "draft"
    # 落库行本身就是 kind='master'（不只返回对象上如此）。
    assert store.sessions[session.session_id]["session_kind"] == "master"


@pytest.mark.asyncio
async def test_create_master_session_replaces_legacy_then_reuses_master() -> None:
    store = ProfileStore()
    # 已有活动 build 会话（seed 行缺 session_kind，读取时默认 build）：
    # master 不得静默复用；旧行保留为 stale，后续重连复用新 master。
    await store.seed_session(
        owner_user_id=10, subject="personal", session_id="sess1"
    )
    first = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    second = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    assert first.session_id == second.session_id
    assert first.session_id != "sess1"
    assert first.session_kind == "master"
    assert store.sessions["sess1"]["status"] == "stale"
    assert store.sessions["sess1"]["active_status"] == 0
    assert len(store.sessions) == 2


@pytest.mark.asyncio
async def test_create_master_session_recovers_expired_master() -> None:
    """R1/D01：过期 master 复用失败（ProfileSessionStale）后落入新建分支。

    修复前：捕获 stale 置空 existing 后仍掉进 build/update 关槽分支读取
    ``existing["session_id"]``，抛 TypeError('NoneType' object is not
    subscriptable)，用户过期重进必失败。
    """
    store = ProfileStore()
    await store.seed_session(
        owner_user_id=10,
        subject="personal",
        session_id="stale1",
        expires_at=_now() - timedelta(days=1),
    )
    store.sessions["stale1"]["session_kind"] = "master"
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    assert session.session_id != "stale1"
    assert session.session_kind == "master"
    # 旧槽已在 _reuse_active_session 内关槽；新会话可直接使用。
    assert store.sessions["stale1"]["status"] == "stale"
    assert store.sessions["stale1"]["active_status"] == 0


@pytest.mark.asyncio
async def test_create_master_session_recovers_revision_drifted_master() -> None:
    """R1/D01：版本漂移的 master 同样走"关槽→新建"，不透传 stale 会话。"""
    store = ProfileStore()
    await store.seed_session(owner_user_id=10, subject="personal", session_id="drift1")
    store.sessions["drift1"]["session_kind"] = "master"
    # 会话快照 profile_revision=1，用户当前向量已推进到 2 → 复用必 stale。
    store.revision_rows[10]["profile_revision"] = 2
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    assert session.session_id != "drift1"
    assert store.sessions["drift1"]["status"] == "stale"
    assert store.sessions["drift1"]["active_status"] == 0
    assert store.sessions[session.session_id]["profile_revision"] == 2


@pytest.mark.asyncio
async def test_assistant_reply_persisted() -> None:
    store = ProfileStore()
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    await persist_master_assistant_reply(
        store.db, session.session_id, 10, "聊得不错，继续～"
    )
    turns = [t for t in store.turns if t["session_id"] == session.session_id]
    assert any(
        t["role"] == "assistant" and t["answer_text"] == "聊得不错，继续～"
        for t in turns
    )


@pytest.mark.asyncio
async def test_assistant_reply_skips_blank() -> None:
    store = ProfileStore()
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    await persist_master_assistant_reply(store.db, session.session_id, 10, "   ")
    assert store.turns == []


@pytest.mark.asyncio
async def test_assistant_reply_rejects_foreign_user() -> None:
    store = ProfileStore()
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    with pytest.raises(ProfileSessionNotFound):
        await persist_master_assistant_reply(
            store.db, session.session_id, 11, "你好"
        )

"""master 会话：创建/复用/助手回复落库（fake store）。

形态与 tests/test_ai_profile_sessions.py 一致（ProfileStore 内存假库，不依赖
真实数据库）。覆盖简报三断言：新会话 kind='master' 且 status=draft；旧 build
会话保留并标记 stale，重复进入只复用新 master；助手回复以 role='assistant'
turn 落库后可读回。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

import app.services.ai.profile as profile_mod
from app.schemas.ai_profile import ProfileSubject
from app.services.ai.profile import (
    ProfileSessionNotFound,
    ProfileSessionStale,
    _preempt_active_slot,
    _update_session_status,
    create_master_session,
    create_profile_session,
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
    # P3 依赖的语义：只有本次插入的行 created=True，复用为 False。
    assert first.created is True
    assert second.created is False


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


@pytest.mark.asyncio
async def test_master_create_preempts_build_row_found_only_on_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 回归：唯一键冲突后回读到 build 行时，绝不把它当 master 复用。

    竞态链路：本请求的前置检查没看到活动槽（对手请求恰在此时插入）→ INSERT 撞
    ``uk_ai_profile_session_active`` → rollback 连带撤销本事务尚未提交的抢占写入
    → 回读（不带 session_kind 过滤）命中那个 build 行。修复前直接复用：墨相师轮次
    被接进题库/更新抽取通道，且 ``created=False`` 让 WS 跳过开场白（空白对话）。
    """
    store = ProfileStore()
    await store.seed_session(owner_user_id=10, subject="personal", session_id="build1")
    original_find = profile_mod._find_active_session
    find_calls = 0

    async def gated_find(db: Any, user_id: int, subject: str) -> Any:
        # 第 1 次（前置检查）返回「无活动会话」，让请求走到 INSERT 撞唯一键；
        # 冲突后的回读委托原实现，返回那个 build 行。
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            return None
        return await original_find(db, user_id, subject)

    monkeypatch.setattr(profile_mod, "_find_active_session", gated_find)
    session = await create_master_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
    )
    assert session.session_kind == "master"
    # 只有本次真正插入的行才算创建成功——不是复用来路不明的会话。
    assert session.created is True
    assert session.session_id != "build1"
    assert store.sessions["build1"]["status"] == "stale"
    assert store.sessions["build1"]["active_status"] == 0
    assert store.sessions[session.session_id]["session_kind"] == "master"
    assert find_calls == 2


@pytest.mark.asyncio
async def test_master_create_raises_stale_when_retry_still_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 回归：重试时活动槽仍被占住 → PROFILE_SESSION_STALE，不猜赢家。

    以「抢占不生效」模拟重试瞬间槽位又被别的请求占住（真库里表现为重试 INSERT
    再次撞唯一键）。上层把它映射为可重试错误，而不是静默走错抽取通道。
    """
    store = ProfileStore()
    await store.seed_session(owner_user_id=10, subject="personal", session_id="build2")
    original_find = profile_mod._find_active_session
    find_calls = 0

    async def gated_find(db: Any, user_id: int, subject: str) -> Any:
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            return None
        return await original_find(db, user_id, subject)

    async def preempt_did_not_help(db: Any, row: Any) -> None:
        # 抢占瞬间槽位又被别人占了：这次抢占对重试毫无帮助。
        return None

    monkeypatch.setattr(profile_mod, "_find_active_session", gated_find)
    monkeypatch.setattr(profile_mod, "_preempt_active_slot", preempt_did_not_help)
    with pytest.raises(ProfileSessionStale):
        await create_master_session(
            store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1"
        )
    # 既没把 build 会话当 master 交出去，也没造出第二个会话。
    assert len(store.sessions) == 1
    assert store.sessions["build2"]["active_status"] == 1


@pytest.mark.asyncio
async def test_status_update_does_not_revive_preempted_session() -> None:
    """P2a 回归：已释放活动槽（``active_status=0``）的会话不得再被推进状态。

    在飞的 ``profile_extract`` 用 ``load_owned_session``（不校验活动）加载会话，
    完成后按**加载时**的 extracting 快照推进状态。若状态 UPDATE 不带
    ``AND active_status = 1``，就会把已被墨相师抢占成 stale 的行改回
    ``awaiting_confirmation``——槽已释放却显示待确认的自相矛盾行。
    """
    store = ProfileStore()
    await store.seed_session(
        owner_user_id=10, subject="personal", session_id="s3", status="extracting"
    )
    await _update_session_status(
        store.db, "s3", profile_mod.ProfileSessionStatus.AWAITING_CONFIRMATION
    )
    assert store.sessions["s3"]["status"] == "awaiting_confirmation"

    await _preempt_active_slot(store.db, store.sessions["s3"])
    await _update_session_status(
        store.db, "s3", profile_mod.ProfileSessionStatus.EXTRACTING
    )
    assert store.sessions["s3"]["status"] == "stale"
    assert store.sessions["s3"]["active_status"] == 0


@pytest.mark.asyncio
async def test_build_create_reuses_active_session_regardless_of_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """钉住 build 侧的既有契约：复用现成的那个活动会话，且与是否发生竞态无关。

    master 侧对回读做 kind 校验，build 侧刻意不做——但若两侧行为随「有没有撞上
    唯一键」而不同，就是分裂契约。本用例同时钉住前置复用与冲突回读复用两条路径
    都返回同一个（master）会话。要收紧 build 的口径，必须连这条一起改并先回
    PRODUCT.md 确认。
    """
    store = ProfileStore()
    await store.seed_session(owner_user_id=10, subject="personal", session_id="master1")
    store.sessions["master1"]["session_kind"] = "master"
    direct = await create_profile_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1", "key-direct"
    )
    assert direct.session_id == "master1"

    original_find = profile_mod._find_active_session
    find_calls = 0

    async def gated_find(db: Any, user_id: int, subject: str) -> Any:
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            return None
        return await original_find(db, user_id, subject)

    monkeypatch.setattr(profile_mod, "_find_active_session", gated_find)
    raced = await create_profile_session(
        store.db, 10, ProfileSubject.PERSONAL, "profile-text-v1", "key-raced"
    )
    assert raced.session_id == "master1"
    assert len(store.sessions) == 1

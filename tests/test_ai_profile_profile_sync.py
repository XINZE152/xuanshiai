"""Unit tests for AI confirmed profile fields dual-sync to base user profile.

三层覆盖：
1. compute_ai_sync_writes 纯计算——映射口径 / 认证保护 / 只补空 / tags 列保护；
2. sync_ai_confirmed_fields_to_profile IO 契约——SAVEPOINT、绝不 commit、失败不污染外层事务；
3. 挂点契约——feature flag 门禁与 ideal_partner 主体隔离。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.profile_tags import ALL_TAG_OPTIONS
from app.schemas.ai_profile import (
    ProfileDraftFieldPatchRequest,
    ProfileDraftRead,
    ProfileFieldPatchAction,
    ProfilePublishAccepted,
    ProfileSubject,
)
from app.services.profile import (
    compute_ai_sync_writes,
    sync_ai_confirmed_fields_to_profile,
)


EMPTY_CURRENT = {
    "is_married": None,
    "height": None,
    "occupation": None,
    "education_level": None,
    "tags": None,
    "residence_province_code": None,
    "residence_city_code": None,
    "interest_tags": None,
    "self_intro": None,
    "education_verified": 0,
}


def _current(**overrides) -> dict:
    row = dict(EMPTY_CURRENT)
    row.update(overrides)
    return row


# ========== 1. compute_ai_sync_writes：映射口径 / 认证保护 / 只补空 ==========


def test_education_level_semantic_mapping_prevents_mismatch() -> None:
    """AI 学历 (1..6) 映射到基础资料 (1..5)，避免本科被错写为硕士。

    AI: 1=初中及以下, 2=高中/中专, 3=大专, 4=本科, 5=硕士, 6=博士
    Profile: 1=高中及以下, 2=大专, 3=本科, 4=硕士, 5=博士
    """
    ai_to_expected = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    for ai_val, expected_profile_val in ai_to_expected.items():
        _users, profile_writes, synced = compute_ai_sync_writes(
            _current(), {"education_level": ai_val}
        )
        assert profile_writes["education_level"] == expected_profile_val
        assert "education_level" in synced


def test_education_verified_protection_skips_sync() -> None:
    """已通过人工/权威学历审核 (education_verified == 2) 时，绝不自动覆盖学历。"""
    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(education_verified=2), {"education_level": 4}
    )
    assert "education_level" not in profile_writes
    assert "education_level" not in synced


def test_only_fills_empty_never_overwrites_existing() -> None:
    """核心契约：只补空、绝不覆盖已有值。"""
    users_writes, profile_writes, synced = compute_ai_sync_writes(
        _current(
            is_married=1,
            height=178,
            occupation="软件工程师",
            education_level=3,
            residence_province_code="310000",
            residence_city_code="310100",
            interest_tags='["摄影", "旅行", "美食"]',
            self_intro="原有的自我介绍",
        ),
        {
            "city_code": "320100",
            "height_cm": 185,
            "marriage_status": "divorced",
            "education_level": 5,
            "occupation_group": "finance",
            "interest_tags": ["徒步", "游泳", "看电影"],
            "self_intro": "AI 画像洞察",
        },
    )
    assert users_writes == {}
    assert profile_writes == {}
    assert synced == []


def test_city_code_split_into_province_and_city() -> None:
    """6位城市编码拆分：前两位+0000为省，前四位+00为市。"""
    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(), {"city_code": "320102"}
    )
    assert profile_writes["residence_province_code"] == "320000"
    assert profile_writes["residence_city_code"] == "320100"
    assert "residence_city_code" in synced


def test_height_cm_bounds_check() -> None:
    """140 <= height <= 220 边界校验，越界直接跳过。"""
    for invalid_h in [139, 221, 0, -10]:
        _users, profile_writes, synced = compute_ai_sync_writes(
            _current(), {"height_cm": invalid_h}
        )
        assert "height" not in profile_writes
        assert "height" not in synced

    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(), {"height_cm": 175}
    )
    assert profile_writes["height"] == 175
    assert "height" in synced


def test_marriage_status_mapping() -> None:
    """婚姻状态无损映射：single->1, divorced->2, widowed->3。"""
    for m_str, expected_int in {"single": 1, "divorced": 2, "widowed": 3}.items():
        users_writes, _profile, synced = compute_ai_sync_writes(
            _current(), {"marriage_status": m_str}
        )
        assert users_writes["is_married"] == expected_int
        assert "is_married" in synced


def test_marriage_zero_is_treated_as_unselected() -> None:
    """users.is_married=0（前端「未选择」档）视为可补空的空值。"""
    users_writes, _profile, synced = compute_ai_sync_writes(
        _current(is_married=0), {"marriage_status": "single"}
    )
    assert users_writes["is_married"] == 1
    assert "is_married" in synced


def test_interest_tags_intersection_and_min_constraint() -> None:
    """与系统标签求交集，交集 < 3 跳过，>= 3 时写入 3~5 个（JSON 编码）。"""
    sample_tags = list(ALL_TAG_OPTIONS)[:5]

    # 交集只有 2 个系统标签 + 1 个非法标签 -> 跳过
    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(), {"interest_tags": [sample_tags[0], sample_tags[1], "非法野标签_xyz"]}
    )
    assert "interest_tags" not in profile_writes
    assert "interest_tags" not in synced

    # 交集有 3 个系统标签 -> 以 JSON 字符串写入
    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(),
        {"interest_tags": [sample_tags[0], sample_tags[1], sample_tags[2], "非法野标签_xyz"]},
    )
    assert "interest_tags" in profile_writes
    assert set(json.loads(profile_writes["interest_tags"])) == {
        sample_tags[0],
        sample_tags[1],
        sample_tags[2],
    }


def test_interest_tags_skipped_when_tags_column_occupied() -> None:
    """回归（对抗审查 B）：tags 列（tag_selections 镜像）非空时绝不写 interest_tags。

    update_profile 的历史行为会把 interest_tags 镜像覆盖进 tags 列，毁掉用户
    手动配置的扩展标签分类；反哺写入必须绕开 tags 列且在其非空时跳过。
    """
    sample_tags = list(ALL_TAG_OPTIONS)[:3]

    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(tags='{"relationship_expectation": ["认真考虑结婚"]}'),
        {"interest_tags": sample_tags},
    )
    assert "interest_tags" not in profile_writes
    assert "interest_tags" not in synced

    # tags 列为空（None/空串/空列表/空对象）时允许写入
    for blank in (None, "", "[]", "{}"):
        _users, profile_writes, synced = compute_ai_sync_writes(
            _current(tags=blank), {"interest_tags": sample_tags}
        )
        assert "interest_tags" in profile_writes, f"tags={blank!r} 应视为空"


def test_occupation_group_label_mapping() -> None:
    """occupation_group 枚举映射为与前端口径一致的中文组名。"""
    for occ_key, expected_label in {
        "technology": "互联网/技术",
        "education": "教育",
        "healthcare": "医疗",
        "finance": "金融",
        "public_service": "公职",
        "other": "其他",
    }.items():
        _users, profile_writes, synced = compute_ai_sync_writes(
            _current(), {"occupation_group": occ_key}
        )
        assert profile_writes["occupation"] == expected_label
        assert "occupation" in synced


def test_self_intro_backfill_and_truncation() -> None:
    """self_intro 仅补空且截断至 500 字。"""
    _users, profile_writes, synced = compute_ai_sync_writes(
        _current(), {"self_intro": "热爱生活，认真对待每一段关系。"}
    )
    assert profile_writes["self_intro"] == "热爱生活，认真对待每一段关系。"
    assert "self_intro" in synced

    _users, profile_writes, _synced = compute_ai_sync_writes(
        _current(), {"self_intro": "很" * 600}
    )
    assert len(profile_writes["self_intro"]) == 500


# ========== 2. sync_ai_confirmed_fields_to_profile：SAVEPOINT IO 契约 ==========


class _MockMappingResult:
    def __init__(self, data: dict | None):
        self._data = data

    def mappings(self):
        return self

    def first(self):
        return self._data


def _moxiang_profile_row(**overrides) -> dict:
    """get_profile 查询行的最小可用样本（含公开裁剪依赖的隐私列）。"""
    row = {
        "user_id": 1001,
        "nickname": "测试用户",
        "gender": 1,
        "birthday": None,
        "is_married": 1,
        "avatar": "/avatar.jpg",
        "height": 178,
        "weight": 70,
        "occupation": "工程师",
        "industry": "IT",
        "education_level": 3,
        "income": 300000.0,
        "hometown_province_code": None,
        "hometown_city_code": None,
        "hometown_district_code": None,
        "residence_province_code": "310000",
        "residence_city_code": "310100",
        "residence_district_code": None,
        "self_intro": "自我介绍",
        "interest_tags": '["摄影", "户外"]',
        "personality_tags": '["沉稳", "真诚"]',
        "mbti": "INTJ",
        "tags": "{}",
        "completion_score": 85.0,
        "hide_school": 0,
        "hide_company": 0,
        "only_vip_can_see_detail": 0,
    }
    row.update(overrides)
    return row


def _enable_moxiang_badge(*, public: bool = False) -> None:
    """打开墨相特质装配所需的门禁：master + profile，可选公开外显。"""
    from app.core.config import settings

    settings.ai_master_enabled = True
    settings.ai_profile_enabled = True
    settings.ai_profile_public_badge_enabled = public


class _FakeNested:
    """记录 savepoint 用法的假 begin_nested 上下文。"""

    def __init__(self):
        self.entered = False
        self.exited = False
        self.exc_type = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        self.exc_type = exc_type
        return False


def _mock_db(current: dict) -> tuple[MagicMock, _FakeNested]:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(current))
    nested = _FakeNested()
    db.begin_nested = MagicMock(return_value=nested)
    db.commit = AsyncMock()
    return db, nested


@pytest.mark.asyncio
async def test_sync_uses_savepoint_and_never_commits() -> None:
    """回归（对抗审查 A）：同步走 SAVEPOINT 且【绝不 commit】——与调用方事务同生共死。"""
    db, nested = _mock_db(_current())
    with patch("app.services.profile.recalculate_completion", new_callable=AsyncMock), \
         patch("app.services.profile.increment_revision_and_enqueue", new_callable=AsyncMock):
        synced = await sync_ai_confirmed_fields_to_profile(db, 1001, {"height_cm": 180})

    assert synced == ["height"]
    assert nested.entered and nested.exited and nested.exc_type is None
    db.commit.assert_not_awaited()
    sql_texts = [str(call[0][0]) for call in db.execute.call_args_list]
    assert any("INSERT INTO user_profile" in s for s in sql_texts)
    assert not any("UPDATE users SET" in s for s in sql_texts)  # height 只写 user_profile
    assert any("UPDATE ai_profile_session" in s for s in sql_texts)


@pytest.mark.asyncio
async def test_sync_realigns_active_session_revision() -> None:
    """反哺抬高资料版本号后，必须把活跃 AI 会话基线抬到新值，防止 stale 自杀。"""
    db, _nested = _mock_db(_current())
    with patch("app.services.profile.recalculate_completion", new_callable=AsyncMock), \
         patch("app.services.profile.increment_revision_and_enqueue", new_callable=AsyncMock):
        synced = await sync_ai_confirmed_fields_to_profile(db, 1001, {"marriage_status": "single"})

    assert synced == ["is_married"]
    sql_texts = [str(call[0][0]) for call in db.execute.call_args_list]
    assert any("UPDATE users SET" in s for s in sql_texts)
    assert any("UPDATE ai_profile_session" in s for s in sql_texts)


@pytest.mark.asyncio
async def test_sync_failure_returns_empty_without_poisoning_outer_txn() -> None:
    """savepoint 内失败：返回空列表、不抛异常，外层事务不被污染（主调用链不受影响）。"""
    db, nested = _mock_db(_current())
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.services.profile.recalculate_completion", new_callable=AsyncMock), \
         patch("app.services.profile.increment_revision_and_enqueue", new_callable=AsyncMock):
        synced = await sync_ai_confirmed_fields_to_profile(db, 1001, {"height_cm": 180})

    assert synced == []
    assert nested.entered and nested.exited
    assert nested.exc_type is RuntimeError


# ========== 3. 响应契约 / 门禁 / 主体隔离 ==========


def test_schema_synced_profile_fields_contract() -> None:
    """两处 API 响应 schema 均包含 synced_profile_fields 字段且默认空列表。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    draft_read = ProfileDraftRead(
        draft_id="d_test",
        subject=ProfileSubject.PERSONAL,
        status="draft",
        expected_revision=1,
        policy_revision="pol_v1",
        created_at=now,
        updated_at=now,
    )
    assert hasattr(draft_read, "synced_profile_fields")
    assert draft_read.synced_profile_fields == []

    publish_accepted = ProfilePublishAccepted(
        task_id="task_test",
        status="succeeded",
    )
    assert hasattr(publish_accepted, "synced_profile_fields")
    assert publish_accepted.synced_profile_fields == []


def _personal_draft() -> object:
    from app.services.ai.profile import ProfileDraft, ProfileDraftField

    return ProfileDraft(
        draft_id="d_gate",
        owner_user_id=1001,
        subject="personal",
        revision=1,
        fields=(
            ProfileDraftField(
                field_key="height_cm",
                subject="personal",
                field_kind="structured",
                value=180,
                confirmation_status="confirmed",
            ),
        ),
    )


def _confirm_height_action() -> list[ProfileDraftFieldPatchRequest]:
    return [
        ProfileDraftFieldPatchRequest(
            field_key="height_cm",
            action=ProfileFieldPatchAction.CONFIRM,
            expected_revision=1,
        )
    ]


@pytest.mark.asyncio
async def test_confirm_sync_gate_respects_feature_flag() -> None:
    """门禁（对抗审查 E）：ai_profile_sync_enabled=false 时 personal 确认不触发反哺。"""
    from app.services.ai.profile import confirm_profile_draft

    draft = _personal_draft()
    db = MagicMock()
    db.execute = AsyncMock()
    with patch("app.services.profile.sync_ai_confirmed_fields_to_profile", new_callable=AsyncMock) as mock_sync, \
         patch("app.services.ai.profile.load_owned_draft_for_update", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile.load_owned_draft", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile._forward_draft_actions_to_memory", new_callable=AsyncMock), \
         patch("app.services.ai.profile.settings") as mock_settings:
        mock_settings.ai_profile_sync_enabled = False
        await confirm_profile_draft(db, "d_gate", 1001, _confirm_height_action(), 1)
        mock_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_sync_runs_when_flag_enabled() -> None:
    """门禁开启时 personal 确认触发反哺，且仅同步本次确认的 structured 字段。"""
    from app.services.ai.profile import confirm_profile_draft

    draft = _personal_draft()
    db = MagicMock()
    db.execute = AsyncMock()
    with patch("app.services.profile.sync_ai_confirmed_fields_to_profile", new_callable=AsyncMock) as mock_sync, \
         patch("app.services.ai.profile.load_owned_draft_for_update", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile.load_owned_draft", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile._forward_draft_actions_to_memory", new_callable=AsyncMock), \
         patch("app.services.ai.profile.settings") as mock_settings:
        mock_settings.ai_profile_sync_enabled = True
        await confirm_profile_draft(db, "d_gate", 1001, _confirm_height_action(), 1)
        mock_sync.assert_awaited_once()
        assert mock_sync.call_args[0][2] == {"height_cm": 180}


@pytest.mark.asyncio
async def test_ideal_partner_isolation_never_syncs_to_user_profile() -> None:
    """伴侣画像 (ideal_partner) 的确认与发布绝不反哺用户个人资料。"""
    from app.services.ai.profile import ProfileDraft, ProfileDraftField, confirm_profile_draft

    draft = ProfileDraft(
        draft_id="d_ideal",
        owner_user_id=1001,
        subject="ideal_partner",
        revision=1,
        fields=(
            ProfileDraftField(
                field_key="height_cm",
                subject="ideal_partner",
                field_kind="structured",
                value=180,
                confirmation_status="confirmed",
            ),
        ),
    )
    with patch("app.services.profile.sync_ai_confirmed_fields_to_profile", new_callable=AsyncMock) as mock_sync, \
         patch("app.services.ai.profile.load_owned_draft_for_update", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile.load_owned_draft", new_callable=AsyncMock, return_value=draft), \
         patch("app.services.ai.profile._forward_draft_actions_to_memory", new_callable=AsyncMock):
        db = MagicMock()
        db.execute = AsyncMock()
        updated = await confirm_profile_draft(
            db,
            "d_ideal",
            1001,
            _confirm_height_action(),
            1,
        )
        mock_sync.assert_not_awaited()
        assert updated.synced_profile_fields == ()


@pytest.mark.asyncio
async def test_get_profile_includes_moxiang_portrait_tags() -> None:
    """验证 get_profile 能够装配已确认的墨相心性称号、标签与依恋风格。"""
    from app.services.profile import get_profile

    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(_moxiang_profile_row()))

    mock_narrative = {
        "status": "published",
        "data": {
            "persona_title": "真诚求索的长期主义者",
            "persona_tags": ["慢热长情", "边界清晰", "生活有序"],
            "emotional_insight": {
                "attachment_style": "secure"
            }
        }
    }
    _enable_moxiang_badge()
    with patch("app.services.ai.profile.load_published_narrative", new_callable=AsyncMock, return_value=mock_narrative), \
         patch("app.services.profile._get_media", new_callable=AsyncMock, return_value=[]):
        profile_data = await get_profile(db, 1001)
        assert profile_data["moxiang_persona_title"] == "真诚求索的长期主义者"
        assert profile_data["moxiang_persona_tags"] == ["慢热长情", "边界清晰", "生活有序"]
        assert profile_data["moxiang_attachment_style"] == "secure"


@pytest.mark.asyncio
async def test_get_profile_hides_moxiang_badge_when_feature_disabled() -> None:
    """AI 画像功能关闭时，即使库中存在历史成稿也不得露出墨相特质。"""
    from app.services.profile import get_profile

    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(_moxiang_profile_row()))
    mock_narrative = {
        "status": "confirmed",
        "data": {"persona_title": "真诚求索的长期主义者", "persona_tags": ["慢热长情"]},
    }
    with patch("app.services.ai.profile.load_published_narrative", new_callable=AsyncMock, return_value=mock_narrative) as mock_load, \
         patch("app.services.profile._get_media", new_callable=AsyncMock, return_value=[]):
        profile_data = await get_profile(db, 1001)
        assert profile_data["moxiang_persona_title"] is None
        assert profile_data["moxiang_persona_tags"] == []
        assert profile_data["moxiang_attachment_style"] is None
        mock_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_profile_hides_unconfirmed_moxiang_narrative() -> None:
    """用户尚未确认的 pending_confirmation 叙事不得进入个人资料。

    PRODUCT.md：AI 生成的总结先以待确认呈现，用户确认后才作为正式画像叙事。
    """
    from app.services.profile import get_profile

    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(_moxiang_profile_row()))
    mock_narrative = {
        "status": "pending_confirmation",
        "data": {
            "persona_title": "尚未确认的称号",
            "persona_tags": ["未确认标签"],
            "emotional_insight": {"attachment_style": "anxious"},
        },
    }
    _enable_moxiang_badge()
    with patch("app.services.ai.profile.load_published_narrative", new_callable=AsyncMock, return_value=mock_narrative), \
         patch("app.services.profile._get_media", new_callable=AsyncMock, return_value=[]):
        profile_data = await get_profile(db, 1001)
        assert profile_data["moxiang_persona_title"] is None
        assert profile_data["moxiang_persona_tags"] == []
        assert profile_data["moxiang_attachment_style"] is None
@pytest.mark.asyncio
async def test_public_profile_defaults_to_no_moxiang_badge() -> None:
    """公开路径默认不展示墨相特质，且依恋透视永不进入他人可见响应。"""
    from app.services.profile import get_profile

    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(_moxiang_profile_row()))
    mock_narrative = {
        "status": "confirmed",
        "data": {
            "persona_title": "真诚求索的长期主义者",
            "persona_tags": ["慢热长情"],
            "emotional_insight": {"attachment_style": "secure"},
        },
    }
    _enable_moxiang_badge(public=False)
    with patch("app.services.ai.profile.load_published_narrative", new_callable=AsyncMock, return_value=mock_narrative), \
         patch("app.services.profile._get_media", new_callable=AsyncMock, return_value=[]):
        profile_data = await get_profile(db, 1001, public=True)
        assert profile_data["moxiang_persona_title"] is None
        assert profile_data["moxiang_persona_tags"] == []
        assert profile_data["moxiang_attachment_style"] is None

@pytest.mark.asyncio
async def test_public_profile_badge_excludes_attachment_style_when_enabled() -> None:
    """公开外显显式开启后只出称号与标签；依恋风格属心理推断，仍不下发。"""
    from app.services.profile import get_profile

    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockMappingResult(_moxiang_profile_row()))
    mock_narrative = {
        "status": "confirmed",
        "data": {
            "persona_title": "真诚求索的长期主义者",
            "persona_tags": ["慢热长情"],
            "emotional_insight": {"attachment_style": "secure"},
        },
    }
    _enable_moxiang_badge(public=True)
    with patch("app.services.ai.profile.load_published_narrative", new_callable=AsyncMock, return_value=mock_narrative), \
         patch("app.services.profile._get_media", new_callable=AsyncMock, return_value=[]):
        profile_data = await get_profile(db, 1001, public=True)
        assert profile_data["moxiang_persona_title"] == "真诚求索的长期主义者"
        assert profile_data["moxiang_persona_tags"] == ["慢热长情"]
        assert profile_data["moxiang_attachment_style"] is None


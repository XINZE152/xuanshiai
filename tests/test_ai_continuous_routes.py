"""continuous_v2 HTTP 路由行为回归：只使用 fake DB，不连接开发/生产库。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentUser
from app.api.routes import ai_profile
from app.schemas.ai_profile import (
    ContinuousConfirmRequest,
    ProfileNarrativeRead,
    ProfilePreviewRequest,
    ProfilePublishAccepted,
    ProfileSubject,
)
from app.services.ai.preview import PreviewRecord
from app.services.ai.tasks import TaskError


@dataclass
class _FakeDB:
    commits: int = 0
    marker: dict[str, Any] | None = None

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        marker = self.marker

        class _Result:
            def mappings(self) -> _Result:
                return self

            def first(self) -> dict[str, Any] | None:
                return marker

        return _Result()


CURRENT = CurrentUser(
    id=42, session_id=7, phone="13800000000", status=1, realname_status=1
)


def _http_code(exc: HTTPException) -> str:
    assert isinstance(exc.detail, dict)
    return str(exc.detail["code"])


@pytest.mark.asyncio
async def test_confirm_route_is_atomic_and_does_not_call_legacy_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    calls: list[tuple[Any, ...]] = []

    async def confirm(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return {
            "task_id": "task-1",
            "status": "queued",
            "replayed": False,
            "revision_id": 11,
            "revision_no": 3,
            "subject": "personal",
            "field_count": 4,
            "narrative_task_id": None,
        }

    async def legacy_publish(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("continuous confirm must not call legacy publish")

    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)
    monkeypatch.setattr(ai_profile, "confirm_continuous_preview", confirm)
    monkeypatch.setattr(ai_profile, "publish_profile_draft", legacy_publish)

    result = await ai_profile.confirm_continuous_preview_route(
        "preview-1", ContinuousConfirmRequest(expected_revision=2), CURRENT, db, "confirm-0001"
    )

    assert isinstance(result, ProfilePublishAccepted)
    assert result.revision_id == 11
    assert calls == [(db, "preview-1", 42, 2, "confirm-0001")]
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (PermissionError("AI_CONSENT_REQUIRED"), 403, "AI_CONSENT_REQUIRED"),
        (LookupError("PREVIEW_NOT_FOUND"), 404, "PREVIEW_NOT_FOUND"),
        (LookupError("PREVIEW_NOT_READY"), 409, "PREVIEW_NOT_READY"),
        (ValueError("DRAFT_VERSION_CONFLICT"), 409, "DRAFT_VERSION_CONFLICT"),
        (TaskError("AI_QUOTA_EXCEEDED", "额度不足", 429, retryable=True), 429, "AI_QUOTA_EXCEEDED"),
    ],
)
async def test_confirm_route_maps_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def confirm(*_args: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)
    monkeypatch.setattr(ai_profile, "confirm_continuous_preview", confirm)

    with pytest.raises(HTTPException) as caught:
        await ai_profile.confirm_continuous_preview_route(
            "preview-1", ContinuousConfirmRequest(expected_revision=2), CURRENT, _FakeDB(), "confirm-0002"
        )
    assert caught.value.status_code == status_code
    assert _http_code(caught.value) == code


@pytest.mark.asyncio
async def test_continuous_preview_route_commits_once_and_legacy_preview_gets_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB(marker={"schema_version": "profile-continuous-v2"})
    ensure_calls: list[str] = []

    async def ensure(*args: Any) -> dict[str, Any]:
        ensure_calls.append(args[-1])
        return {
            "preview_id": "preview-1",
            "draft_id": "draft-1",
            "expected_revision": 0,
            "subject": "personal",
            "status": "active",
            "content": "",
            "task_id": "task-1",
            "flow_version": "continuous_v2",
            "generation_status": "queued",
            "fields": [],
            "boundary_changed": False,
        }

    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)
    monkeypatch.setattr(ai_profile, "ensure_continuous_preview", ensure)
    result = await ai_profile.create_profile_preview_route(
        "draft-1", ProfilePreviewRequest(expected_revision=0), CURRENT, db, "preview-0001"
    )
    assert result.flow_version == "continuous_v2"
    assert ensure_calls == ["preview-0001"]
    assert db.commits == 1

    legacy_db = _FakeDB(marker=None)
    captured_repo: list[Any] = []

    async def generate_preview(*, repo: Any, **_kwargs: Any) -> PreviewRecord:
        captured_repo.append(repo)
        return PreviewRecord(
            preview_id="legacy-preview",
            draft_id="legacy-draft",
            expected_revision=1,
            user_id=42,
            subject="personal",
            content="legacy",
            status="active",
            task_id="legacy-task",
            last_error=None,
            created_at=None,
            updated_at=None,
        )

    monkeypatch.setattr("app.services.ai.preview.generate_preview", generate_preview)
    await ai_profile.create_profile_preview_route(
        "legacy-draft", ProfilePreviewRequest(expected_revision=1), CURRENT, legacy_db, "legacy-0001"
    )
    assert captured_repo and captured_repo[0] is not None
    assert legacy_db.commits == 1


@pytest.mark.asyncio
async def test_get_continuous_preview_maps_revoked_consent_to_403(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(marker={"schema_version": "profile-continuous-v2"})
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)

    async def revoked(*_args: Any) -> None:
        raise PermissionError("AI_CONSENT_REQUIRED")

    monkeypatch.setattr(ai_profile, "get_continuous_preview", revoked)
    with pytest.raises(HTTPException) as caught:
        await ai_profile.get_profile_preview_route("preview-1", CURRENT, db)
    assert caught.value.status_code == 403
    assert _http_code(caught.value) == "AI_CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_narrative_route_transmits_revision_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)

    async def load(*_args: Any) -> dict[str, Any]:
        return {"revision_id": 19, "status": "confirmed", "data": {}}

    monkeypatch.setattr(ai_profile, "load_published_narrative", load)
    result = await ai_profile.get_profile_narrative_route(
        ProfileSubject.PERSONAL, CURRENT, _FakeDB()
    )
    assert isinstance(result, ProfileNarrativeRead)
    assert result.revision_id == 19


_EMOTIONAL_INSIGHT = {
    "attachment_style": "secure",
    "attachment_summary": "情绪沉着，期待细水长流的坦诚回应",
    "highlights": ["温和包容", "尊重边界"],
    "boundaries": ["需要独处空间"],
    "master_message": "心安处方能久长。",
}


def _narrative_with_emotional_insight() -> dict[str, Any]:
    return {
        "revision_id": 19,
        "status": "confirmed",
        "data": {"persona_title": "在杭州写代码的男生", "emotional_insight": _EMOTIONAL_INSIGHT},
    }


@pytest.mark.asyncio
async def test_narrative_route_returns_emotional_insight_for_personal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """个人画像成稿必须下发情感气质透视，前端三处 UI 都读这个字段。"""
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)

    async def load(*_args: Any) -> dict[str, Any]:
        return _narrative_with_emotional_insight()

    monkeypatch.setattr(ai_profile, "load_published_narrative", load)
    result = await ai_profile.get_profile_narrative_route(
        ProfileSubject.PERSONAL, CURRENT, _FakeDB()
    )
    assert result.emotional_insight is not None
    assert result.emotional_insight.attachment_style == "secure"
    assert result.emotional_insight.highlights == ["温和包容", "尊重边界"]
    # 落库形态是 JSON 列，序列化后字段名必须与前端读取口径一致
    dumped = result.model_dump()["emotional_insight"]
    assert sorted(dumped) == [
        "attachment_style",
        "attachment_summary",
        "boundaries",
        "highlights",
        "master_message",
    ]


@pytest.mark.asyncio
async def test_narrative_route_drops_emotional_insight_for_ideal_partner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """理想型画像即使库里残留该键也不下发：依恋风格是个人心理推断。"""
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)

    async def load(*_args: Any) -> dict[str, Any]:
        return _narrative_with_emotional_insight()

    monkeypatch.setattr(ai_profile, "load_published_narrative", load)
    result = await ai_profile.get_profile_narrative_route(
        ProfileSubject.IDEAL_PARTNER, CURRENT, _FakeDB()
    )
    assert result.emotional_insight is None


@pytest.mark.asyncio
async def test_narrative_route_tolerates_dirty_emotional_insight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺字段的脏数据按无值处理，展示接口不得因画像内容 500。"""
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)

    async def load(*_args: Any) -> dict[str, Any]:
        return {
            "revision_id": 19,
            "status": "confirmed",
            "data": {"emotional_insight": {"attachment_style": "secure"}},
        }

    monkeypatch.setattr(ai_profile, "load_published_narrative", load)
    result = await ai_profile.get_profile_narrative_route(
        ProfileSubject.PERSONAL, CURRENT, _FakeDB()
    )
    assert result.emotional_insight is None


@pytest.mark.asyncio
async def test_continuous_history_and_build_keep_error_contract(monkeypatch):
    from app.api.routes import ai_moxiang
    from app.schemas.ai_profile import ContinuousBuildRequest
    monkeypatch.setattr(ai_moxiang, "_require_journey_feature", lambda: None)
    db = _FakeDB()

    async def revoked(*args, **kwargs):
        raise PermissionError("AI_CONSENT_REQUIRED")

    monkeypatch.setattr(ai_moxiang, "list_continuous_turns", revoked)
    with pytest.raises(HTTPException) as caught:
        await ai_moxiang.get_continuous_turns(50, None, CURRENT, db)
    assert caught.value.status_code == 403
    assert _http_code(caught.value) == "AI_CONSENT_REQUIRED"

    async def conflict(*args, **kwargs):
        raise TaskError("TASK_IDEMPOTENCY_CONFLICT", "请求内容不同", 409)

    monkeypatch.setattr(ai_moxiang, "build_continuous_draft", conflict)
    with pytest.raises(HTTPException) as caught:
        await ai_moxiang.build_continuous_route(ProfileSubject.PERSONAL, ContinuousBuildRequest(), CURRENT, db, "build-key-01")
    assert caught.value.status_code == 409
    assert _http_code(caught.value) == "TASK_IDEMPOTENCY_CONFLICT"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_preview_invalid_idempotency_key_remains_400(monkeypatch):
    monkeypatch.setattr(ai_profile, "_require_profile_feature", lambda: None)
    with pytest.raises(HTTPException) as caught:
        await ai_profile.create_profile_preview_route("draft1", ProfilePreviewRequest(expected_revision=0), CURRENT, _FakeDB(), "bad")
    assert caught.value.status_code == 400
    assert _http_code(caught.value) == "AI_INPUT_INVALID"

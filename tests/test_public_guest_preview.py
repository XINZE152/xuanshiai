"""匿名游客预览契约（GUEST-PREVIEW-20261115）。

纯内存/假库单元契约，不依赖 MySQL/Redis：
1. guest 视角可见性排除非公开/不完整/封禁候选；
2. 脱敏白名单与投影（昵称掩码、标签截断、恒锁定）；
3. 平台开关 403、限流 429；
4. 发现预览以 viewer_id=0 且关闭身份/偏好门禁取数；
5. 动态预览 SQL guest-safe、活动预览剥离个人字段；
6. 路由匿名可达（无 token 200）。
"""

from __future__ import annotations

import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import get_db
from app.api.routes import public_preview as route
from app.schemas.community import ActivityPage, ActivityResponse
from app.schemas.discovery import DiscoveryCard
from app.schemas.public_preview import (
    PublicActivityPreview,
    PublicActivityPreviewPage,
    PublicCommunityPreviewPage,
    PublicDiscoveryPreviewPage,
    PublicPostPreview,
    PublicPreviewCard,
)
from app.services import public_preview as service
from app.services.candidate_query import CandidatePage
from app.services.candidate_visibility import (
    CandidateContext,
    ViewerContext,
    evaluate_visibility,
)

GUEST = ViewerContext(user_id=0, realname_status=0, is_vip=False)


def _candidate(**overrides: object) -> CandidateContext:
    base: dict[str, object] = {"user_id": 7, "who_can_see_me": 1}
    base.update(overrides)
    return CandidateContext(**base)  # type: ignore[arg-type]


def test_guest_sees_public_complete_candidate() -> None:
    assert evaluate_visibility(GUEST, _candidate()).allowed is True


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"who_can_see_me": 2}, "REALNAME_REQUIRED"),
        ({"who_can_see_me": 3}, "VIP_REQUIRED"),
        ({"who_can_see_me": 4}, "PRIVATE_PROFILE"),
        ({"profile_visibility": "only_me"}, "PROFILE_VISIBILITY_ONLY_ME"),
        ({"profile_visibility": "friends"}, "PROFILE_VISIBILITY_FRIENDS_ONLY"),
        ({"profile_complete": False}, "PROFILE_INCOMPLETE"),
        ({"account_active": False}, "ACCOUNT_INACTIVE"),
        ({"not_restricted": False}, "ACCOUNT_RESTRICTED"),
        ({"media_approved": False}, "MEDIA_REVIEW_PENDING"),
        ({"blocked": True}, "BLOCKED_RELATIONSHIP"),
    ],
)
def test_guest_exclusions(overrides: dict[str, object], code: str) -> None:
    decision = evaluate_visibility(GUEST, _candidate(**overrides))
    assert decision.allowed is False
    assert decision.denial_code == code


@pytest.mark.parametrize(
    "raw,expected",
    [(None, None), ("", None), ("  ", None), ("苏晚晴", "苏**"), ("A", "A**")],
)
def test_mask_nickname(raw: str | None, expected: str | None) -> None:
    assert service.mask_nickname(raw) == expected


def test_preview_models_exclude_sensitive_fields() -> None:
    card_fields = set(PublicPreviewCard.model_fields)
    for banned in (
        "income", "height", "weight", "phone", "wechat", "self_intro", "qa_answers",
        "distance_km", "hometown_display", "match_score", "match_reason", "online_status",
    ):
        assert banned not in card_fields
    post_fields = set(PublicPostPreview.model_fields)
    for banned in (
        "video", "location", "school", "hometown", "realname_status",
        "is_liked", "is_followed", "user_id",
    ):
        assert banned not in post_fields
    activity_fields = set(PublicActivityPreview.model_fields)
    for banned in ("address", "my_status", "my_status_text"):
        assert banned not in activity_fields


def test_project_card_masks_truncates_and_locks() -> None:
    card = DiscoveryCard(
        user_id=7, nickname="苏晚晴", avatar="/storage/a.webp", gender=2, age=28,
        height=165, education_level=4, occupation="教师", city_code="110100",
        income=300000.0, distance_km=3.0, is_married=1, online_status=1, mbti="INFJ",
        interest_tags=["a", "b", "c", "d", "e"], certification_tags=["实名认证"],
        match_score=90.0, match_reason="x", is_favorite=True, is_liked=True,
        is_vip=True, is_pure_free=False, is_boosted=False, detail_locked=False,
        self_intro="hello", residence_display="北京市",
    )
    projected = service.project_preview_card(card)
    assert projected.nickname == "苏**"
    assert projected.interest_tags == ["a", "b", "c"]
    assert projected.detail_locked is True
    assert projected.is_liked is False
    assert projected.is_favorite is False
    dumped = projected.model_dump()
    assert "income" not in dumped
    assert "self_intro" not in dumped


async def _open(db: object) -> bool:
    return True


async def _allow(key: str, limit: int, window: int) -> bool:
    return True


@pytest.mark.asyncio
async def test_platform_closed_blocks_all_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    async def closed(db: object) -> bool:
        return False

    async def message(db: object) -> str:
        return "平台正在维护中，请稍后再试。"

    monkeypatch.setattr(service, "platform_browse_open", closed)
    monkeypatch.setattr(service, "platform_maintenance_message", message)
    for func in (
        service.get_public_discovery_preview,
        service.get_public_community_preview,
        service.get_public_activities_preview,
    ):
        with pytest.raises(HTTPException) as exc:
            await func(None, ip="1.2.3.4", page=1, page_size=6)  # type: ignore[arg-type]
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_rate_limit_exhausted_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    async def denied(key: str, limit: int, window: int) -> bool:
        return False

    monkeypatch.setattr(service, "platform_browse_open", _open)
    monkeypatch.setattr(service, "consume_rate_limit", denied)
    with pytest.raises(HTTPException) as exc:
        await service.get_public_discovery_preview(None, ip="1.2.3.4", page=1, page_size=6)  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "PUBLIC_PREVIEW_RATE_LIMITED"


def _card_row() -> dict[str, object]:
    return {
        "user_id": 7, "nickname": "苏晚晴", "avatar": "/storage/a.webp", "gender": 2,
        "birthday": datetime(1998, 1, 1), "residence_province_code": "110000",
        "residence_city_code": "110100", "residence_district_code": "110101",
        "education_level": 4, "occupation": "教师", "mbti": "INFJ",
        "interest_tags": '["阅读","旅行","音乐","电影"]', "realname_status": 2,
        "is_vip": 0, "is_boosted": 0, "online_status": 1,
    }


@pytest.mark.asyncio
async def test_discovery_preview_uses_guest_snapshot_and_masks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "platform_browse_open", _open)
    monkeypatch.setattr(service, "consume_rate_limit", _allow)
    captured: dict[str, object] = {}

    def fake_snapshot(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace()

    async def fake_fetch(db: object, snapshot: object, *, cursor: object, page_size: int):
        return CandidatePage(
            items=[_card_row()], page=1, page_size=page_size, total=1,
            has_more=False, next_cursor=None,
        )

    monkeypatch.setattr(service.discovery_service, "_candidate_snapshot", fake_snapshot)
    monkeypatch.setattr(
        service.discovery_service.candidate_query_service, "fetch_page", fake_fetch
    )
    page = await service.get_public_discovery_preview(None, ip="1.2.3.4", page=1, page_size=6)  # type: ignore[arg-type]
    assert captured["viewer_id"] == 0
    assert captured["apply_intent_gender_gate"] is False
    assert captured["respect_preferences"] is False
    assert page.is_preview is True
    assert page.items[0].nickname == "苏**"
    assert page.items[0].detail_locked is True


class _FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeDb:
    def __init__(self, rows: list[dict[str, object]], total: int) -> None:
        self._rows = rows
        self._total = total
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, sql: object, params: dict[str, object] | None = None):
        self.calls.append((str(sql), params or {}))
        if "COUNT(*)" in str(sql):
            return _FakeResult([{"total": self._total}])
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_community_preview_guest_safe_sql_and_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "platform_browse_open", _open)
    monkeypatch.setattr(service, "consume_rate_limit", _allow)
    db = _FakeDb(
        [
            {
                "id": 1, "nickname": "苏晚晴", "avatar": None, "content": "你好",
                "images": '["a","b","c","d"]', "topic_name": None, "like_count": 2,
                "comment_count": 1, "created_at": datetime(2026, 11, 15),
            }
        ],
        1,
    )
    page = await service.get_public_community_preview(db, ip="1.2.3.4", page=1, page_size=6)  # type: ignore[arg-type]
    sql, params = db.calls[0]
    assert "p.visibility = 0" in sql
    assert "moderation_status" in sql
    assert params["visibility_realname_status"] == 0
    assert params["visibility_viewer_id"] == 0
    assert page.items[0].images == ["a", "b", "c"]
    assert page.items[0].author_nickname == "苏**"
    assert page.total == 1
    assert page.has_more is False


@pytest.mark.asyncio
async def test_activities_preview_recruiting_only_and_strips_personal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "platform_browse_open", _open)
    monkeypatch.setattr(service, "consume_rate_limit", _allow)
    captured: dict[str, object] = {}
    moment = datetime(2026, 11, 15)

    async def fake_list(db: object, user_id: int, *, filter_key: str, page: int, page_size: int):
        captured.update(user_id=user_id, filter_key=filter_key)
        return ActivityPage(
            items=[
                ActivityResponse(
                    id=1, title="t", cover=None, type=None, city="北京",
                    address="精确地址", start_time=moment, end_time=moment,
                    signup_deadline=None, max_people=10, current_people=1, price=0.0,
                    status=1, status_text="recruiting", description=None,
                    my_status=1, my_status_text="joined", created_at=moment,
                )
            ],
            page=1, page_size=6, total=1,
        )

    monkeypatch.setattr(service.community_service, "list_activities", fake_list)
    page = await service.get_public_activities_preview(None, ip="1.2.3.4", page=1, page_size=6)  # type: ignore[arg-type]
    assert captured == {"user_id": 0, "filter_key": "recruiting"}
    item = page.items[0]
    assert not hasattr(item, "address")
    assert not hasattr(item, "my_status")


def test_route_signatures_have_no_auth_dependency() -> None:
    for endpoint in (route.discovery_preview, route.community_preview, route.activities_preview):
        assert "current" not in inspect.signature(endpoint).parameters


def test_routes_are_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discovery(db: object, *, ip: str, page: int, page_size: int):
        return PublicDiscoveryPreviewPage(
            items=[], page=page, page_size=page_size, total=0, has_more=False
        )

    async def fake_community(db: object, *, ip: str, page: int, page_size: int):
        return PublicCommunityPreviewPage(
            items=[], page=page, page_size=page_size, total=0, has_more=False
        )

    async def fake_activities(db: object, *, ip: str, page: int, page_size: int):
        return PublicActivityPreviewPage(
            items=[], page=page, page_size=page_size, total=0, has_more=False
        )

    monkeypatch.setattr(route.public_preview, "get_public_discovery_preview", fake_discovery)
    monkeypatch.setattr(route.public_preview, "get_public_community_preview", fake_community)
    monkeypatch.setattr(route.public_preview, "get_public_activities_preview", fake_activities)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: None
    client = TestClient(app)
    for path in (
        "/public/discovery/preview",
        "/public/community/preview",
        "/public/activities/preview",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["is_preview"] is True
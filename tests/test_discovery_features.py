import inspect
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.redis import daily_quota_key
from app.main import app
from app.schemas.discovery import (
    ApplicationCreateRequest,
    DiscoveryCard,
    DiscoveryFilters,
    DiscoverySearch,
)
from app.services import discovery as discovery_service
from app.services import quotas as quotas_service
from app.services.candidate_query import (
    SORT_VERSION,
    CandidateCursor,
    CandidatePage,
    CandidateQueryService,
    InvalidCandidateCursor,
)
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
)
from app.services.discovery import _card, _consume_browse
discovery = discovery_service

client = TestClient(app)


class _BrowseSession:
    """假会话：``_record_quota_usage`` 只需要 ``execute`` 返回 None。"""

    def __init__(self) -> None:
        self.inserts: list[str] = []

    async def execute(self, statement, params=None):  # noqa: ANN001, ANN201
        self.inserts.append(str(statement))
        return None


async def _browse_quota(
    session: _BrowseSession,
    user_id: int,
    target_user_id: int,
    *,
    match_score: float = 50,
    is_vip: bool = False,
) -> int:
    return await _consume_browse(
        session,
        user_id=user_id,
        match_score=match_score,
        is_vip=is_vip,
        target_user_id=target_user_id,
    )


def _fixed_browse_limit(limit: int):
    async def quota_limit(*_args, **_kwargs) -> int:
        return limit

    return quota_limit


@pytest.mark.asyncio
async def test_profile_browse_quota_uses_redis_fallback_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def consume(*_args, **_kwargs) -> int:
        return -1

    async def daily_used(*_args, **_kwargs) -> int:
        return 1

    async def record(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    monkeypatch.setattr(discovery_service, "consume_daily_unique", consume)
    monkeypatch.setattr(discovery_service, "get_daily_used", daily_used)
    monkeypatch.setattr(discovery_service, "_record_quota_usage", record)

    remaining = await discovery_service._consume_browse(
        object(), user_id=10, match_score=50, is_vip=False, target_user_id=11
    )

    assert remaining == 7


@pytest.mark.asyncio
async def test_repeat_view_of_same_target_consumes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一用户看多次只消耗一次机会、只写一条台账。"""
    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    session = _BrowseSession()

    remaining = [await _browse_quota(session, 910001, 11) for _ in range(3)]

    assert remaining == [7, 7, 7]
    assert len(session.inserts) == 1


@pytest.mark.asyncio
async def test_ninth_distinct_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_extra(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    monkeypatch.setattr(discovery_service, "consume_extra", no_extra)
    session = _BrowseSession()

    remaining = [await _browse_quota(session, 910002, 100 + index) for index in range(8)]
    assert remaining == [7, 6, 5, 4, 3, 2, 1, 0]

    with pytest.raises(HTTPException) as excinfo:
        await _browse_quota(session, 910002, 109)
    assert excinfo.value.status_code == 429


@pytest.mark.asyncio
async def test_high_match_bonus_serves_extra_distinct_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    session = _BrowseSession()

    for index in range(8):
        await _browse_quota(session, 910003, 200 + index)

    assert await _browse_quota(session, 910003, 209, match_score=90) == 4


@pytest.mark.asyncio
async def test_bonus_pool_exhausted_then_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_extra(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    monkeypatch.setattr(discovery_service, "consume_extra", no_extra)
    session = _BrowseSession()

    for index in range(13):
        await _browse_quota(session, 910004, 300 + index, match_score=90)

    with pytest.raises(HTTPException) as excinfo:
        await _browse_quota(session, 910004, 399, match_score=90)
    assert excinfo.value.status_code == 429


@pytest.mark.asyncio
async def test_exhausted_quota_still_opens_already_viewed_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_extra(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    monkeypatch.setattr(discovery_service, "consume_extra", no_extra)
    session = _BrowseSession()

    for index in range(8):
        await _browse_quota(session, 910005, 400 + index)

    with pytest.raises(HTTPException):
        await _browse_quota(session, 910005, 499)

    assert await _browse_quota(session, 910005, 400) == 0


@pytest.mark.asyncio
async def test_points_pool_marks_target_to_avoid_double_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def extra(*_args, **_kwargs) -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(discovery_service, "_quota_limit", _fixed_browse_limit(8))
    monkeypatch.setattr(discovery_service, "consume_extra", extra)
    session = _BrowseSession()

    for index in range(8):
        await _browse_quota(session, 910006, 500 + index)

    assert await _browse_quota(session, 910006, 599) == 0
    assert calls["n"] == 1

    assert await _browse_quota(session, 910006, 599) == 0
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_browse_quota_keys_use_utc_daily_key() -> None:
    counter_key = await discovery_service._quota_key("browse", 7)
    seen_key = await discovery_service._quota_key("browse_seen", 7)

    assert counter_key == daily_quota_key("discovery:browse", 7)
    assert seen_key == daily_quota_key("discovery:browse_seen", 7)
    assert seen_key != counter_key


@pytest.mark.asyncio
async def test_resolve_browse_limit_reads_snapshot_for_non_vip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def runtime(*_args, **_kwargs) -> int:
        return 8

    async def membership(*_args, **_kwargs):
        return False, {}

    monkeypatch.setattr(quotas_service, "_membership_rights", membership)
    monkeypatch.setattr(quotas_service, "get_runtime_value", runtime)

    assert await quotas_service.resolve_browse_limit(object(), user_id=1) == 8


@pytest.mark.asyncio
async def test_resolve_browse_limit_reads_package_rights_for_vip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def membership(*_args, **_kwargs):
        return True, {"browse_daily_limit": 20}

    monkeypatch.setattr(quotas_service, "_membership_rights", membership)

    assert await quotas_service.resolve_browse_limit(object(), user_id=1) == 20


@pytest.mark.asyncio
async def test_quota_summary_uses_same_browse_limit_as_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def runtime(*_args, **_kwargs) -> int:
        return 8

    async def used(*_args, **_kwargs) -> int:
        return 3

    async def no_extra(*_args, **_kwargs) -> int:
        return 0

    async def membership(*_args, **_kwargs):
        return False, {}

    monkeypatch.setattr(quotas_service, "_membership_rights", membership)
    monkeypatch.setattr(quotas_service, "get_runtime_value", runtime)
    monkeypatch.setattr(quotas_service, "get_daily_used", used)
    monkeypatch.setattr(quotas_service, "_extra_remaining", no_extra)

    result = await quotas_service.summary(object(), 1)
    browse = next(item for item in result.items if item.quota_code == "browse")

    assert browse.daily_limit == 8
    assert browse.daily_remaining == 5
    assert await discovery_service._quota_limit(object(), 1, False) == 8


def test_discovery_filters_validate_ranges_and_page_size() -> None:
    with pytest.raises(ValidationError):
        DiscoveryFilters(age_min=40, age_max=20)
    with pytest.raises(ValidationError):
        DiscoveryFilters(page_size=21)
    assert DiscoveryFilters(gender="2", marriage_status="3").gender == 2
    assert DiscoveryFilters(gender="2", marriage_status="3").marriage_status == 3
    with pytest.raises(ValidationError):
        DiscoveryFilters(gender=3)
    with pytest.raises(ValidationError):
        DiscoveryFilters(marriage_status=4)


def test_application_message_has_a_bounded_length() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreateRequest(message="x" * 256)


def test_discovery_search_requires_nickname_or_tag() -> None:
    with pytest.raises(ValidationError):
        DiscoverySearch()
    query = DiscoverySearch(nickname="  小明  ")
    assert query.nickname == "小明"


def test_discovery_cursor_is_accepted_but_cannot_be_combined_with_page_offset() -> None:
    query = DiscoveryFilters(cursor="signed-cursor")
    assert query.cursor == "signed-cursor"
    with pytest.raises(ValidationError):
        DiscoveryFilters(page=2, cursor="signed-cursor")

    search = DiscoverySearch(nickname="小明", cursor="signed-cursor")
    assert search.cursor == "signed-cursor"


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [("age_min", 25), ("gender", 2)],
)
def test_candidate_cursor_is_invalidated_when_viewer_predicate_facts_change(
    changed_field: str,
    changed_value: int,
) -> None:
    viewer = {
        "gender": 1,
        "realname_status": 2,
        "age_min": 24,
        "age_max": 32,
        "height_min": 160,
        "height_max": 180,
        "education_min": 3,
        "income_min": 10000,
        "marriage_status": 1,
        "preferred_province_code": "310000",
        "preferred_city_codes": '["310100"]',
    }
    original = discovery._candidate_snapshot(
        viewer_id=1,
        viewer=viewer,
        viewer_is_vip=False,
        filters=DiscoveryFilters(),
        plaza=False,
        nickname=None,
        tag=None,
        respect_preferences=True,
    )
    changed = discovery._candidate_snapshot(
        viewer_id=1,
        viewer={**viewer, changed_field: changed_value},
        viewer_is_vip=False,
        filters=DiscoveryFilters(),
        plaza=False,
        nickname=None,
        tag=None,
        respect_preferences=True,
    )
    service = CandidateQueryService(secret_key="test-secret")
    token = service.encode_cursor(
        CandidateCursor(
            sort_version=SORT_VERSION,
            query_fingerprint=original.query_fingerprint,
            is_boosted=False,
            last_active_at=None,
            user_id=2,
        )
    )

    assert original.query_fingerprint != changed.query_fingerprint
    with pytest.raises(InvalidCandidateCursor):
        service.decode_cursor(token, expected_fingerprint=changed.query_fingerprint)


def test_discovery_routes_are_registered_and_require_authentication() -> None:
    openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    assert "/api/v1/discovery/recommendations" in paths
    assert "/api/v1/discovery/search" in paths
    assert "/api/v1/discovery/filters/saved" in paths
    assert "/api/v1/users/{user_id}/profile" in paths

    response = client.get("/api/v1/discovery/recommendations")
    assert response.status_code == 401

    response = client.get("/api/v1/discovery/search?tag=旅行")
    assert response.status_code == 401

    response = client.get("/api/v1/discovery/recommendations?gender=2&marriage_status=1")
    assert response.status_code == 401


def test_filter_options_is_public() -> None:
    response = client.get("/api/v1/discovery/filter-options")
    assert response.status_code == 200
    assert response.json()["genders"]


def test_filter_options_education_direction_matches_storage_domain() -> None:
    """education_levels 必须与 user_profile.education_level 存储域同向：
    1=高中及以下 … 5=博士（SQL 语义为 education_level >= :education_min）。"""
    response = client.get("/api/v1/discovery/filter-options")
    levels = {item["value"]: item["label"] for item in response.json()["education_levels"]}
    assert levels == {1: "高中及以下", 2: "大专", 3: "本科", 4: "硕士", 5: "博士"}


def test_my_overview_is_registered_and_requires_authentication() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/users/me/overview" in paths
    response = client.get("/api/v1/users/me/overview")
    assert response.status_code == 401


def test_superlike_requires_idempotency_key_in_openapi() -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/discovery/superlikes/{target_id}"]["post"]
    parameters = {item["name"].lower(): item for item in operation["parameters"]}
    assert parameters["idempotency-key"]["required"] is True


def test_record_lists_expose_scroll_pagination_contract() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/v1/discovery/visitors",
        "/api/v1/discovery/favorites",
        "/api/v1/discovery/applications/incoming",
        "/api/v1/discovery/applications/outgoing",
        "/api/v1/discovery/favorites/received",
        "/api/v1/discovery/superlikes/sent",
        "/api/v1/discovery/superlikes/received",
    ):
        assert "page" in str(paths[path]["get"])


def test_test_payment_and_paid_discovery_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/payments/test/pay" in paths
    assert "/api/v1/boost/packages" in paths
    assert "/api/v1/boost/orders" in paths
    assert "/api/v1/spotlights/payments" in paths


def test_discovery_card_respects_privacy_and_detail_lock() -> None:
    row = {
        "user_id": 1,
        "nickname": "用户",
        "birthday": None,
        "height": 175,
        "education_level": 3,
        "occupation": "工程师",
        "residence_city_code": "310100",
        "income": 20000,
        "same_city": True,
        "is_married": 1,
        "online_status": 1,
        "mbti": "INTJ",
        "interest_tags": '["旅行"]',
        "realname_status": 0,
        "hide_school": 1,
        "hide_company": 1,
        "hide_distance": 1,
        "hide_online_status": 0,
        "is_favorite": 0,
        "is_vip": 0,
        "is_boosted": 0,
    }

    card = _card(row, 50, "资料匹配")
    assert card.education_level is None
    assert card.occupation is None
    assert card.distance_km is None
    assert card.is_vip is False

    locked = _card({**row, "hide_school": 0, "hide_company": 0, "hide_distance": 0, "is_vip": 1}, 50, "资料匹配", detail_locked=True)
    assert locked.education_level is None
    assert locked.occupation is None
    assert locked.interest_tags == []


@pytest.mark.asyncio
async def test_candidate_fetch_uses_the_realname_visibility_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyRows:
        def scalar_one(self) -> int:
            return 0

        def mappings(self) -> "EmptyRows":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class RecordingDb:
        statement: object | None = None
        params: dict[str, object] | None = None

        async def execute(
            self, statement: object, params: dict[str, object]
        ) -> EmptyRows:
            self.statement = statement
            self.params = params
            return EmptyRows()

    async def viewer_context(_: object, __: int) -> dict[str, object]:
        return {"gender": None}

    async def is_vip(_: object, __: int) -> bool:
        return False

    monkeypatch.setattr(discovery, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery, "_is_vip", is_vip)
    db = RecordingDb()

    await discovery._fetch_rows(db, 1, DiscoveryFilters(), plaza=True)

    assert db.params is not None
    assert db.params["visibility_realname_status"] == 0
    assert "COALESCE(pr.who_can_see_me, 1) <> 2" in str(db.statement)


@pytest.mark.asyncio
async def test_target_rows_uses_the_realname_visibility_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyRows:
        def mappings(self) -> "EmptyRows":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class RecordingDb:
        statement: object | None = None
        params: dict[str, object] | None = None

        async def execute(
            self, statement: object, params: dict[str, object]
        ) -> EmptyRows:
            self.statement = statement
            self.params = params
            return EmptyRows()

    async def viewer_context(_: object, __: int) -> dict[str, object]:
        return {"realname_status": 0}

    async def is_vip(_: object, __: int) -> bool:
        return False

    monkeypatch.setattr(discovery, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery, "_is_vip", is_vip)
    db = RecordingDb()

    assert await discovery._target_rows(db, 1, [2]) == {}

    assert db.params is not None
    assert db.params["visibility_realname_status"] == 0
    assert "COALESCE(pr.who_can_see_me, 1) <> 2" in str(db.statement)


@pytest.mark.asyncio
async def test_ensure_target_follows_discovery_skip_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交互链路必须和展示链路共用同一开关，否则测试号会「看得到却申请不到」。"""
    recorded: dict[str, bool] = {}

    async def fake_target_rows(
        _db: object, _viewer_id: int, _target_ids: list[int], *, skip_completion_gate: bool = False
    ) -> dict[int, dict[str, object]]:
        recorded["skip"] = skip_completion_gate
        return {}

    monkeypatch.setattr(discovery, "_target_rows", fake_target_rows)

    monkeypatch.setattr(discovery, "_skip_discovery_gates", lambda: True)
    with pytest.raises(HTTPException) as skipped:
        await discovery._ensure_target(object(), 1, 22)
    assert skipped.value.status_code == 404
    assert recorded["skip"] is True

    monkeypatch.setattr(discovery, "_skip_discovery_gates", lambda: False)
    with pytest.raises(HTTPException):
        await discovery._ensure_target(object(), 1, 22)
    assert recorded["skip"] is False


@pytest.mark.asyncio
async def test_application_list_keeps_ids_alongside_nested_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A populated application page must serialise the top-level id fields.

    ``ApplicationResponse`` requires ``from_user_id``/``to_user_id`` in addition
    to the nested ``from_user``/``to_user`` summaries, so the service must not
    consume the id columns while it builds the nested objects.
    """
    row = {
        "id": 5,
        "from_user_id": 11,
        "to_user_id": 22,
        "message": "想认识一下",
        "status": 0,
        "expire_at": None,
        "created_at": datetime(2026, 9, 1, 10, 0, 0),
        "from_nickname": "甲",
        "from_avatar": None,
        "from_birthday": None,
        "from_city_code": "110100",
        "to_nickname": "乙",
        "to_avatar": None,
        "to_birthday": None,
        "to_city_code": "320100",
    }

    class Rows:
        def __init__(self, *, scalar_value: int, rows: list[dict[str, object]]) -> None:
            self._scalar = scalar_value
            self._rows = rows

        def scalar(self) -> int:
            return self._scalar

        def mappings(self) -> "Rows":
            return self

        def all(self) -> list[dict[str, object]]:
            return self._rows

    class RecordingDb:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, statement: object, params: dict[str, object] | None = None) -> Rows:
            self.calls += 1
            # 第一次是 COUNT，第二次是明细查询。
            if self.calls == 1:
                return Rows(scalar_value=1, rows=[])
            return Rows(scalar_value=1, rows=[dict(row)])

    async def passthrough_scene(*_args: object, **_kwargs: object):
        predicate = CandidateVisibilityService().predicate(
            ViewerContext(user_id=1, realname_status=2, is_vip=True),
            VisibilityScene.INTERACTION,
            candidate_alias="fu",
            privacy_alias="fpr",
            completion_alias="fc",
        )
        return {}, False, predicate

    monkeypatch.setattr(discovery, "_scene_visibility", passthrough_scene)

    page = await discovery.list_applications(RecordingDb(), 1, True, page=1, page_size=20)

    assert page.total == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.from_user_id == 11
    assert item.to_user_id == 22
    assert item.from_user is not None and item.from_user.user_id == 11
    assert item.to_user is not None and item.to_user.user_id == 22


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incoming", "candidate_alias"),
    [
        (True, "fu"),
        (False, "tu"),
    ],
)
async def test_application_lists_filter_only_blocked_counterpart(
    monkeypatch: pytest.MonkeyPatch,
    incoming: bool,
    candidate_alias: str,
) -> None:
    """收件箱只过滤拉黑，不再复用发现链路的可见性谓词（2026-10-27）。

    旧实现调用 ``_scene_visibility(VisibilityScene.INTERACTION)``，谓词里含
    ``who_can_see_me`` / ``match_status``，且未传 ``skip_completion_gate``（默认 False），
    后果是：对方脱单/暂停/设「完全私密」后历史申请从列表凭空消失；测试号
    （完整度 <100）收件箱恒空，``DISCOVERY_SKIP_GATES`` 也救不了。
    收件箱只回答「这条申请还在不在」，因此只保留双向拉黑过滤。
    """

    class EmptyRows:
        def scalar(self) -> int:
            return 0

        def mappings(self) -> "EmptyRows":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class RecordingDb:
        statements: list[str]

        def __init__(self) -> None:
            self.statements = []

        async def execute(
            self,
            statement: object,
            params: dict[str, object] | None = None,
        ) -> EmptyRows:
            self.statements.append(str(statement))
            return EmptyRows()

    seen: list[str] = []

    async def scene_visibility(*_args: object, **_kwargs: object):
        seen.append("called")
        raise AssertionError("收件箱不应再调用 _scene_visibility")

    monkeypatch.setattr(discovery, "_scene_visibility", scene_visibility)
    db = RecordingDb()

    page = await discovery.list_applications(db, 1, incoming, page=1, page_size=20)

    assert page.total == 0
    assert seen == [], "收件箱必须脱离发现链路的可见性谓词"
    application_queries = [
        statement for statement in db.statements if "FROM match_apply" in statement
    ]
    assert len(application_queries) == 2
    for statement in application_queries:
        assert "FROM user_block bl" in statement
        assert f"{candidate_alias}.id" in statement
        assert "who_can_see_me" not in statement
        assert "match_status" not in statement
        assert "score, 0) >= 100" not in statement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "args", "query_marker", "expected_scene"),
    [
        ("browse_history", (1, 1, 20), "FROM user_browse_history", "HISTORY"),
        ("visitors", (1, 1, 20), "FROM user_browse_history", "VISITORS"),
        ("list_favorites", (1, 1, 20), "FROM user_favorite", "FAVORITES"),
        (
            "list_received_favorites",
            (1, 1, 20),
            "FROM user_favorite",
            "FAVORITES",
        ),
        ("list_superlikes", (1, "sent", 1, 20), "FROM user_boost", "FAVORITES"),
    ],
)
async def test_record_lists_use_the_shared_visibility_predicate(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    args: tuple[object, ...],
    query_marker: str,
    expected_scene: str,
) -> None:
    class EmptyRows:
        def scalar(self) -> int:
            return 0

        def mappings(self) -> "EmptyRows":
            return self

        def all(self) -> list[dict[str, object]]:
            return []

        def __iter__(self) -> object:
            return iter(())

    class RecordingDb:
        statements: list[str]

        def __init__(self) -> None:
            self.statements = []

        async def execute(
            self,
            statement: object,
            params: dict[str, object] | None = None,
        ) -> EmptyRows:
            self.statements.append(str(statement))
            return EmptyRows()

    seen_scenes: list[str] = []

    async def scene_visibility(
        _: object,
        __: int,
        scene: object,
        *,
        candidate_alias: str = "u",
        privacy_alias: str = "pr",
        completion_alias: str = "c",
    ) -> tuple[dict[str, object], bool, object]:
        seen_scenes.append(str(scene))
        predicate = CandidateVisibilityService().predicate(
            ViewerContext(user_id=1, realname_status=2, is_vip=True),
            scene,
            candidate_alias=candidate_alias,
            privacy_alias=privacy_alias,
            completion_alias=completion_alias,
        )
        return {}, True, predicate

    monkeypatch.setattr(discovery, "_scene_visibility", scene_visibility)
    db = RecordingDb()

    await getattr(discovery, handler_name)(db, *args)

    assert seen_scenes == [f"VisibilityScene.{expected_scene}"]
    record_queries = [
        statement for statement in db.statements if query_marker in statement
    ]
    assert record_queries
    assert all(
        "COALESCE(pr.who_can_see_me, 1) IN (1, 2, 3)" in statement
        for statement in record_queries
    )


@pytest.mark.asyncio
async def test_respond_application_rechecks_the_applicant_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PendingApplication:
        def mappings(self) -> "PendingApplication":
            return self

        def first(self) -> dict[str, object]:
            return {
                "id": 7,
                "from_user_id": 2,
                "to_user_id": 1,
                "message": None,
                "status": 0,
                "expire_at": None,
                "created_at": None,
            }

    class EmptyRows:
        pass

    class RecordingDb:
        statements: list[str]

        def __init__(self) -> None:
            self.statements = []

        async def execute(
            self,
            statement: object,
            params: dict[str, object] | None = None,
        ) -> object:
            sql = str(statement)
            self.statements.append(sql)
            if "SELECT id, from_user_id, to_user_id" in sql:
                return PendingApplication()
            if "UPDATE match_apply SET status = :status" in sql:
                raise AssertionError("a hidden applicant must not be processed")
            return EmptyRows()

    checked_targets: list[tuple[int, int]] = []

    async def hidden_target(_: object, viewer_id: int, target_id: int) -> None:
        checked_targets.append((viewer_id, target_id))
        raise discovery.HTTPException(404, detail="目标用户不存在或当前不可见")

    monkeypatch.setattr(discovery, "_ensure_target", hidden_target)
    db = RecordingDb()

    with pytest.raises(discovery.HTTPException) as error:
        await discovery.respond_application(db, 1, 7, accepted=True)

    assert error.value.status_code == 404
    assert checked_targets == [(1, 2)]
    assert not any("UPDATE match_apply SET status = :status" in sql for sql in db.statements)


@pytest.mark.asyncio
async def test_discovery_page_uses_exact_candidate_query_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    viewer = {
        "completion_score": 100,
        "birthday": None,
        "residence_city_code": None,
        "interest_tags": None,
        "personality_tags": None,
        "tags": None,
        "mbti": None,
    }
    row = {"user_id": 2, "birthday": None}
    expected_card = DiscoveryCard(
        user_id=2,
        nickname="候选",
        avatar=None,
        age=None,
        height=None,
        education_level=None,
        occupation=None,
        city_code=None,
        income=None,
        distance_km=None,
        is_married=None,
        online_status=0,
        mbti=None,
        interest_tags=[],
        certification_tags=[],
        match_score=1,
        match_reason="资料匹配",
        is_favorite=False,
        is_pure_free=True,
        is_boosted=False,
    )

    async def viewer_context(_: object, __: int) -> dict[str, object]:
        return viewer

    async def is_vip(_: object, __: int) -> bool:
        return False

    async def candidate_page(*_: object, **__: object) -> tuple[
        dict[str, object], bool, CandidatePage
    ]:
        return viewer, False, CandidatePage(
            items=[row],
            page=1,
            page_size=20,
            total=1001,
            has_more=True,
            next_cursor="next-signed-cursor",
            sort_version="feed-rank-baseline-v1",
            total_is_estimate=False,
        )

    async def unexpected_fetch(*_: object, **__: object) -> list[dict[str, object]]:
        raise AssertionError("legacy sampled candidate fetch must not be used")

    monkeypatch.setattr(discovery, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery, "_is_vip", is_vip)
    monkeypatch.setattr(discovery, "_candidate_query_page", candidate_page, raising=False)
    monkeypatch.setattr(discovery, "_fetch_rows", unexpected_fetch)
    monkeypatch.setattr(discovery, "_candidate_score", lambda *_: (1.0, "资料匹配"))
    monkeypatch.setattr(discovery, "_card", lambda *_args, **_kwargs: expected_card)

    page = await discovery.get_discovery_page(
        object(), 1, DiscoveryFilters(cursor="signed-cursor"), plaza=False
    )

    assert page.total == 1001
    assert page.has_more is True
    assert page.next_cursor == "next-signed-cursor"
    assert page.sort_version == "feed-rank-baseline-v1"
    assert page.total_is_estimate is False


@pytest.mark.asyncio
async def test_search_forwards_cursor_to_candidate_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    viewer = {"completion_score": 100}
    received_cursor: str | None = None

    async def viewer_context(_: object, __: int) -> dict[str, object]:
        return viewer

    async def candidate_page(
        _: object,
        __: int,
        filters: DiscoveryFilters,
        **___: object,
    ) -> tuple[dict[str, object], bool, CandidatePage]:
        nonlocal received_cursor
        received_cursor = filters.cursor
        return viewer, False, CandidatePage(
            items=[],
            page=1,
            page_size=20,
            total=0,
            has_more=False,
            next_cursor=None,
        )

    monkeypatch.setattr(discovery, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery, "_candidate_query_page", candidate_page)

    await discovery.search_discovery(
        object(), 1, DiscoverySearch(nickname="候选", cursor="signed-cursor")
    )

    assert received_cursor == "signed-cursor"


def test_media_review_status_does_not_hide_an_otherwise_visible_user() -> None:
    """Public media filtering belongs to profile serialization, not target visibility."""
    for function in (
        discovery._fetch_rows,
        discovery._target_rows,
        discovery.browse_history,
        discovery.visitors,
        discovery.list_favorites,
    ):
        assert "pending_media" not in inspect.getsource(function)


def test_recommendations_can_repeat_viewed_users() -> None:
    source = inspect.getsource(discovery._candidate_snapshot)
    assert "user_browse_history bh" not in source
    assert "user_swipe_record sw" in source


def test_recommendations_keep_users_with_submitted_applications_visible() -> None:
    source = inspect.getsource(discovery._candidate_snapshot)
    assert "match_apply ma" not in source


@pytest.mark.asyncio
async def test_visitor_count_returns_only_a_deduplicated_number(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def scalar(self) -> int:
            return 3

    class Session:
        async def execute(self, *_args: object, **_kwargs: object) -> Result:
            return Result()

    async def visible_target(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(discovery, "_ensure_target", visible_target)

    assert await discovery.visitor_count(Session(), viewer_id=1, target_id=2) == {"user_id": 2, "visitor_count": 3}


@pytest.mark.asyncio
async def test_browse_quota_uses_redis_fallback_for_remaining_count(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def consume_from_fallback(*_args: object, **_kwargs: object) -> int:
        return 1

    async def fallback_used(*_args: object, **_kwargs: object) -> int:
        return 1

    limit = discovery.settings.browse_daily_limit
    monkeypatch.setattr(discovery, "_quota_limit", _fixed_browse_limit(limit))
    monkeypatch.setattr(discovery, "consume_daily_unique", consume_from_fallback)
    monkeypatch.setattr(discovery, "get_daily_used", fallback_used)

    remaining = await _consume_browse(Session(), user_id=1, match_score=50, is_vip=False, target_user_id=2)

    assert remaining == limit - 1

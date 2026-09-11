from __future__ import annotations

import pytest
from fastapi import HTTPException

from datetime import UTC, datetime, timedelta

from app.services import ai_advisor, ai_assistant, ai_avatar, community, discovery, membership, quotas
from app.services.ai import search as ai_search
from app.services.candidate_visibility import (
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar(self) -> object:
        return self.value


class _MembershipDb:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)
        self.queries: list[str] = []

    async def execute(self, statement, _params):
        self.queries.append(str(statement))
        return _ScalarResult(next(self.values))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_value", "expected"),
    [
        (0, False),  # ordinary user
        (1, True),  # active VIP
        (0, False),  # expired VIP
    ],
)
async def test_active_membership_is_the_single_source_for_regular_active_and_expired_users(
    database_value: object, expected: bool
) -> None:
    db = _MembershipDb([database_value])

    assert await membership.has_active_membership(db, 7) is expected
    query = db.queries[0]
    assert "status = 1" in query
    assert "start_at <= UTC_TIMESTAMP()" in query
    assert "end_at > UTC_TIMESTAMP()" in query


@pytest.mark.asyncio
async def test_active_membership_rechecks_boundary_state_without_a_stale_cache() -> None:
    db = _MembershipDb([1, 0])

    assert await membership.has_active_membership(db, 7) is True
    assert await membership.has_active_membership(db, 7) is False
    assert len(db.queries) == 2


def test_shared_membership_sql_keeps_start_boundary_inclusive_and_end_boundary_exclusive() -> None:
    predicate = membership.active_membership_exists_sql(
        membership_alias="viewer_membership",
        user_id_param="visibility_viewer_id",
    )

    assert predicate == membership.ACTIVE_MEMBERSHIP_EXISTS_SQL.format(
        membership_alias="viewer_membership",
        user_id_param="visibility_viewer_id",
    )
    assert "viewer_membership.start_at <= UTC_TIMESTAMP()" in predicate
    assert "viewer_membership.end_at > UTC_TIMESTAMP()" in predicate


@pytest.mark.parametrize(
    ("membership_alias", "user_id_param"),
    [
        ("viewer-membership", "viewer_id"),
        ("viewer_membership", "viewer-id"),
        ("viewer_membership; DROP TABLE users", "viewer_id"),
    ],
)
def test_shared_membership_sql_rejects_non_identifier_aliases_and_parameters(
    membership_alias: str, user_id_param: str
) -> None:
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        membership.active_membership_exists_sql(
            membership_alias=membership_alias,
            user_id_param=user_id_param,
        )


def test_bulk_visibility_predicate_evaluates_vip_in_the_database_snapshot() -> None:
    predicate = CandidateVisibilityService().predicate(
        ViewerContext(user_id=7, realname_status=2, is_vip=False),
        VisibilityScene.DISCOVERY,
    )

    expected = membership.active_membership_exists_sql(
        membership_alias="visibility_membership",
        user_id_param="visibility_viewer_id",
    )
    assert expected in predicate.clause
    assert "visibility_viewer_is_vip" not in predicate.clause
    assert "visibility_viewer_is_vip" not in predicate.params


@pytest.mark.asyncio
async def test_effective_membership_row_is_shared_by_status_and_rights_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"package_type": "month", "start_at": None, "end_at": None, "rights": {}}
    calls: list[tuple[object, int]] = []

    async def active_row(db: object, user_id: int):
        calls.append((db, user_id))
        return row

    monkeypatch.setattr(membership, "get_active_membership_row", active_row)
    monkeypatch.setattr(quotas, "get_active_membership_row", active_row)
    monkeypatch.setattr(discovery, "get_active_membership_row", active_row)

    class _RowResult:
        def first(self):
            return None

    class _Db:
        async def execute(self, *_args):
            return _RowResult()

    db = _Db()
    status = await membership.get_status(db, 7)
    vip, rights = await quotas._membership_rights(db, 7)
    limit = await discovery._quota_limit(db, 7, True)

    assert status.is_vip is True
    assert (vip, rights) == (True, {})
    assert limit == 20
    assert calls == [(db, 7), (db, 7), (db, 7)]


@pytest.mark.asyncio
async def test_community_quota_delegates_vip_state_to_membership_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def active(_db: object, user_id: int) -> bool:
        calls.append(user_id)
        return False

    async def runtime_value(*_args: object) -> int:
        return 1

    async def daily_used(*_args: object) -> int:
        return 0

    monkeypatch.setattr(community, "has_active_membership", active)
    monkeypatch.setattr(community, "get_runtime_value", runtime_value)
    monkeypatch.setattr(community, "get_daily_used", daily_used)

    class _Db:
        pass

    result = await community.get_community_quotas(_Db(), 7)

    assert result.apply_daily.total == 1
    assert calls == [7]


@pytest.mark.asyncio
async def test_membership_history_does_not_mark_a_future_membership_as_vip() -> None:
    future_start = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)

    class _Count:
        def scalar(self) -> int:
            return 1

    class _Rows:
        def mappings(self):
            return self

        def __iter__(self):
            return iter(
                [
                    {
                        "id": 1,
                        "package_type": "month",
                        "amount": 1,
                        "order_no": "VIP-1",
                        "start_at": future_start,
                        "end_at": None,
                        "status": 1,
                    }
                ]
            )

    class _Db:
        def __init__(self) -> None:
            self.results = iter([_Count(), _Rows()])

        async def execute(self, *_args):
            return next(self.results)

    result = await membership.history(_Db(), 7, page=1, page_size=20)

    assert result.items[0].is_vip is False
    assert result.items[0].rights["visitor_detail"] is False


@pytest.mark.asyncio
async def test_final_materialized_card_read_reapplies_the_bulk_visibility_predicate() -> None:
    visibility = CandidateVisibilityService().predicate(
        ViewerContext(user_id=7, realname_status=2, is_vip=False),
        VisibilityScene.SEARCH,
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class _Db:
        statement = None
        params = None

        async def execute(self, statement, params):
            self.statement = statement
            self.params = params
            return _Rows()

    db = _Db()
    cards = await ai_search._load_materialized_candidate_cards(
        db,
        7,
        [8],
        visibility=visibility,
    )

    assert cards == {}
    assert visibility.clause in str(db.statement)
    assert db.params["visibility_viewer_id"] == 7


@pytest.mark.asyncio
async def test_candidate_visibility_uses_the_shared_membership_predicate_in_its_single_snapshot() -> None:
    class _Rows:
        def mappings(self):
            return self

        def first(self):
            return None

    class _Db:
        statement = None

        async def execute(self, statement, _params):
            self.statement = statement
            return _Rows()

    db = _Db()
    await CandidateVisibilityService().decide(db, 7, 8, VisibilityScene.PROFILE)

    expected = membership.active_membership_exists_sql(
        membership_alias="viewer_membership",
        user_id_param="visibility_viewer_id",
    )
    assert expected in str(db.statement)


@pytest.mark.asyncio
async def test_ai_search_vip_check_delegates_to_membership_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def active(_db, _user_id: int) -> bool:
        return True

    monkeypatch.setattr(ai_search, "has_active_membership", active)

    assert await ai_search._is_vip(object(), 7) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "expected_detail"),
    [
        (ai_advisor, "AI功能仅限有效会员使用"),
        (ai_assistant, "AI功能仅限会员使用"),
    ],
)
async def test_ai_vip_guards_preserve_errors_but_delegate_to_membership_source(
    monkeypatch: pytest.MonkeyPatch, service, expected_detail: str
) -> None:
    calls: list[int] = []

    async def expired(_db, user_id: int) -> bool:
        calls.append(user_id)
        return False

    monkeypatch.setattr(service, "has_active_membership", expired)

    with pytest.raises(HTTPException, match=expected_detail) as error:
        await service._require_vip(object(), 7)

    assert error.value.status_code == 403
    assert calls == [7]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", [discovery, ai_avatar])
async def test_discovery_and_avatar_vip_contexts_delegate_to_membership_source(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    async def active(_db, _user_id: int) -> bool:
        return True

    monkeypatch.setattr(service, "has_active_membership", active)

    assert await service._is_vip(object(), 7) is True


@pytest.mark.asyncio
async def test_ai_search_denies_expired_membership_before_viewer_display_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def expired(_db, _user_id: int) -> bool:
        return False

    async def must_not_consume(*_args, **_kwargs):
        raise AssertionError("expired member must be denied before quota or viewer display state")

    monkeypatch.setattr(ai_assistant, "has_active_membership", expired)
    monkeypatch.setattr(ai_assistant, "_consume_ai_quota", must_not_consume)

    with pytest.raises(HTTPException, match="AI功能仅限会员使用"):
        await ai_assistant.parse_search(object(), 7, ai_assistant.AISearchRequest(query="找对象"))

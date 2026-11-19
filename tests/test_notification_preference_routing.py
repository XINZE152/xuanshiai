"""事件偏好路由：match 与「认识/约见申请」必须由两个独立开关控制。

背景（2026-11-11）：设置页只有一个「接收匹配通知」开关，绑的是
``notify_match``，文案却写着「有人申请认识你时通知」；而申请类事件
（``match_application`` / ``match_application_accepted`` /
``match_application_rejected`` / ``apply``）归 ``notify_apply``。
``notify_apply`` 在前端没有任何入口，于是这类通知永远关不掉。
前端因此拆成两个开关——本文件锁死后端侧「两个开关确实各管一段」。
"""

from __future__ import annotations

import re

import pytest

from app.services import notifications as notif

_PREFERENCE_PATTERN = re.compile(
    r"SELECT COALESCE\(\(SELECT (\w+) FROM user_privacy", re.IGNORECASE
)


class _Result:
    def __init__(self, value: object = None) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return []


class _PreferenceDb:
    """按 SQL 形态回答：拉黑查询→无、隐私查询→指定列、写入→记录。"""

    def __init__(self, **privacy: bool) -> None:
        self.privacy = privacy
        self.preference_columns: list[str] = []
        self.inserted: list[dict[str, object]] = []

    async def execute(self, statement: object, params: dict[str, object]) -> _Result:
        sql = str(statement)
        if "FROM user_block" in sql:
            return _Result(None)
        if "FROM user_privacy" in sql:
            match = _PREFERENCE_PATTERN.search(sql)
            assert match is not None, f"隐私查询缺少列名: {sql}"
            column = match.group(1)
            self.preference_columns.append(column)
            return _Result(1 if self.privacy.get(column, True) else 0)
        if "INSERT INTO user_notification" in sql:
            self.inserted.append(dict(params))
            return _Result(None)
        raise AssertionError(f"未预期的 SQL: {sql}")


async def _emit(db: _PreferenceDb, event_type: str) -> bool:
    return await notif.emit_notification(
        db,  # type: ignore[arg-type]
        recipient_user_id=1,
        actor_user_id=2,
        event_type=event_type,
        title="t",
        content="c",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    ["match_application", "match_application_accepted", "match_application_rejected", "apply"],
)
async def test_apply_events_are_gated_by_notify_apply(event_type: str) -> None:
    db = _PreferenceDb(notify_apply=False, notify_match=True)
    assert await _emit(db, event_type) is False
    assert db.preference_columns == ["notify_apply"]
    assert db.inserted == []


@pytest.mark.asyncio
async def test_match_event_is_gated_by_notify_match() -> None:
    db = _PreferenceDb(notify_match=False, notify_apply=True)
    assert await _emit(db, "match") is False
    assert db.preference_columns == ["notify_match"]


@pytest.mark.asyncio
async def test_two_switches_are_independent() -> None:
    """关掉「认识申请」不影响「匹配成功」，反之亦然——这是拆开关的意义。"""

    only_match_on = _PreferenceDb(notify_match=True, notify_apply=False)
    assert await _emit(only_match_on, "match") is True
    assert await _emit(only_match_on, "match_application") is False

    only_apply_on = _PreferenceDb(notify_match=False, notify_apply=True)
    assert await _emit(only_apply_on, "match_application") is True
    assert await _emit(only_apply_on, "match") is False

    assert [row["notification_type"] for row in only_apply_on.inserted] == ["match_application"]


@pytest.mark.asyncio
async def test_mandatory_events_bypass_preference() -> None:
    for event_type in sorted(notif._MANDATORY_EVENT_TYPES):
        db = _PreferenceDb(
            notify_match=False,
            notify_apply=False,
            notify_system=False,
            notify_message=False,
        )
        assert await _emit(db, event_type) is True
        assert db.preference_columns == [], f"{event_type} 属强制事件，不该查偏好"
        assert len(db.inserted) == 1


@pytest.mark.asyncio
async def test_unknown_event_type_falls_back_to_notify_system() -> None:
    db = _PreferenceDb(notify_system=False)
    assert await _emit(db, "some_future_event") is False
    assert db.preference_columns == ["notify_system"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    ["system", "community_post_remind", "matchmaker_service_updated"],
)
async def test_system_events_are_gated_by_notify_system(event_type: str) -> None:
    """设置页「接收系统通知」开关要真的能挡住这一类；举报/审核不受影响由上一条保证。"""

    db = _PreferenceDb(notify_system=False)
    assert await _emit(db, event_type) is False
    assert db.preference_columns == ["notify_system"]

    on = _PreferenceDb(notify_system=True)
    assert await _emit(on, event_type) is True
    assert [row["notification_type"] for row in on.inserted] == [event_type]


@pytest.mark.asyncio
async def test_self_triggered_event_is_skipped_without_queries() -> None:
    db = _PreferenceDb()
    assert (
        await notif.emit_notification(
            db,  # type: ignore[arg-type]
            recipient_user_id=7,
            actor_user_id=7,
            event_type="match_application",
            title="t",
            content="c",
        )
        is False
    )
    assert db.preference_columns == []
    assert db.inserted == []


def test_preference_columns_are_distinct_for_match_and_apply() -> None:
    """映射本身：两个偏好名都在 PrivacyUpdateRequest/PrivacyResponse 上有字段。"""

    from app.schemas.social import PrivacyResponse, PrivacyUpdateRequest

    fields = set(PrivacyResponse.model_fields) | set(PrivacyUpdateRequest.model_fields)
    for column in {*notif._PREFERENCE_BY_EVENT.values(), "notify_system"}:
        assert column in fields, f"偏好 {column} 未在隐私 schema 上建模"

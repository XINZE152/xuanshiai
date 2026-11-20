"""隐身浏览：自己「我看过谁」仍记，对方「谁看过我」不记。"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import discovery


class _Result:
    def __init__(self, scalar: Any = 0) -> None:
        self._scalar = scalar

    def scalar(self) -> Any:
        return self._scalar


class _CaptureDb:
    def __init__(self, anonymous: int) -> None:
        self.anonymous = anonymous
        self.inserts: list[tuple[str, dict[str, Any]]] = []
        self.calls = 0

    async def execute(self, sql: object, params: dict[str, Any] | None = None) -> _Result:
        text_sql = str(sql)
        self.calls += 1
        if "anonymous_browse_enabled" in text_sql:
            return _Result(scalar=self.anonymous)
        if "INSERT INTO user_browse_history" in text_sql:
            self.inserts.append((text_sql, dict(params or {})))
            return _Result()
        return _Result()


@pytest.mark.asyncio
async def test_anonymous_browse_still_inserts_own_history() -> None:
    db = _CaptureDb(anonymous=1)
    await discovery._record_browse(db, viewer_id=9, target_id=42)
    assert len(db.inserts) == 1
    _, params = db.inserts[0]
    assert params["user_id"] == 9
    assert params["target_id"] == 42
    assert params["is_anonymous"] == 1


@pytest.mark.asyncio
async def test_normal_browse_inserts_visible_history() -> None:
    db = _CaptureDb(anonymous=0)
    await discovery._record_browse(db, viewer_id=9, target_id=42)
    assert len(db.inserts) == 1
    _, params = db.inserts[0]
    assert params["is_anonymous"] == 0


def test_view_profile_records_browse_before_quota_consume() -> None:
    """额度 429 不得发生在写足迹之前，否则「进了详情、我看过谁仍空」。"""
    import inspect

    source = inspect.getsource(discovery.view_profile)
    record_at = source.find("await _record_browse")
    consume_at = source.find("await _consume_browse")
    assert record_at >= 0 and consume_at >= 0
    assert record_at < consume_at
    assert "await db.commit()" in source
    assert "status_code == 429" in source

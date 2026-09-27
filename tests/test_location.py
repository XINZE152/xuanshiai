import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.schemas.location import LocationSharingRequest, LocationUpdateRequest
from app.services import location as location_service
from app.services.location import remove_online_location, set_location_sharing


def test_location_request_validates_coordinate_ranges() -> None:
    request = LocationUpdateRequest(latitude=31.2304, longitude=121.4737, accuracy_m=25)
    assert request.source == "device"


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 91, "longitude": 121},
        {"latitude": 31, "longitude": 181},
        {"latitude": 31, "longitude": 121, "accuracy_m": -1},
    ],
)
def test_location_request_rejects_invalid_values(payload: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        LocationUpdateRequest(**payload)


def test_location_sharing_requires_boolean() -> None:
    assert LocationSharingRequest(enabled=True).enabled is True


# ---------------- 停用共享时的 GEO 索引清理（吞错修复回归） ----------------


class _FakeRedis:
    """按注入的失败次数抛 RedisError，之后成功；记录 zrem 调用。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.zrem_calls: list[tuple[str, str]] = []

    async def zrem(self, key: str, member: str) -> int:
        self.zrem_calls.append((key, member))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RedisError("redis jitter")
        return 1


class _FakeDb:
    def __init__(self, row: dict) -> None:
        self._row = row
        self.executed: list[str] = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.executed.append(str(statement))
        result = SimpleNamespace(
            mappings=lambda: SimpleNamespace(first=lambda: dict(self._row), all=lambda: []),
            scalar=lambda: None,
        )
        return result

    async def commit(self) -> None:
        self.committed = True


def _profile_row() -> dict:
    return {
        "latitude": None,
        "longitude": None,
        "location_precision": None,
        "location_updated_at": None,
        "location_consent": 0,
        "location_visible": 0,
    }


def test_remove_online_location_retries_once_and_succeeds(monkeypatch, caplog) -> None:
    fake = _FakeRedis(fail_times=1)
    monkeypatch.setattr(location_service, "redis_client", fake)

    asyncio.run(remove_online_location(42))

    assert len(fake.zrem_calls) == 2, "首次失败后应恰好重试一次"
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, "首次失败应记录一次 WARNING，重试成功不再告警"


def test_remove_online_location_never_raises_on_persistent_failure(
    monkeypatch, caplog
) -> None:
    fake = _FakeRedis(fail_times=99)
    monkeypatch.setattr(location_service, "redis_client", fake)

    asyncio.run(remove_online_location(42))  # 不抛即保持停用成功语义

    assert len(fake.zrem_calls) == 2, "应恰好重试一次"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 3, "两次失败各一条 WARNING + 最终放弃一条"
    assert all("user_id=42" in message for message in warnings)
    assert any("location_online_removal_gave_up" in message for message in warnings)


def test_disabled_sharing_removes_online_member_and_keeps_success(
    monkeypatch,
) -> None:
    fake_redis = _FakeRedis(fail_times=0)
    monkeypatch.setattr(location_service, "redis_client", fake_redis)
    db = _FakeDb(_profile_row())

    response = asyncio.run(
        set_location_sharing(db, 42, LocationSharingRequest(enabled=False))
    )

    assert response.enabled is False
    assert db.committed is True
    assert fake_redis.zrem_calls == [(location_service.LOCATION_GEO_KEY, "42")]


def test_enabled_sharing_does_not_touch_geo_index(monkeypatch) -> None:
    fake_redis = _FakeRedis(fail_times=0)
    monkeypatch.setattr(location_service, "redis_client", fake_redis)
    db = _FakeDb(_profile_row())

    asyncio.run(set_location_sharing(db, 42, LocationSharingRequest(enabled=True)))

    assert fake_redis.zrem_calls == [], "启用共享不清理 GEO 索引"

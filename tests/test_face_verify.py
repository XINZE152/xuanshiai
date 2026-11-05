"""头像人脸自助核验（Q32，2026-11-02）。

锁定：只能核验本人头像；每日 3 次；provider 未配置 fail-closed；通过后
写 ``user_auth.face_verified=1`` 并审计。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.services.face import verify as service
from app.services.face.base import FaceVerifyResult
from app.services.face.providers import DisabledFaceProvider


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None


class _Db:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()

    async def rollback(self) -> None:
        self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True


class _Provider:
    name = "mock"

    def __init__(self, result: FaceVerifyResult | Exception) -> None:
        self.result = result

    async def verify(
        self,
        *,
        user_id: int,
        image_ref: str,
        scene: str = "realname",
        real_name: str | None = None,
        id_card_number: str | None = None,
    ) -> FaceVerifyResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def _quota(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"used": 1}

    async def allow(_key: str, _limit: int) -> bool:
        return True

    async def used(_key: str) -> int:
        return int(state["used"])

    monkeypatch.setattr(service, "consume_daily", allow)
    monkeypatch.setattr(service, "get_daily_used", used)
    return state


def _use_provider(monkeypatch: pytest.MonkeyPatch, result: FaceVerifyResult | Exception) -> None:
    monkeypatch.setattr(service, "get_face_provider", lambda: _Provider(result))


@pytest.mark.asyncio
async def test_rejects_avatar_not_owned_by_user() -> None:
    db = _Db(_Result(rows=[{"avatar": "/uploads/mine.webp"}]))

    with pytest.raises(HTTPException) as error:
        await service.verify_avatar(db, 7, "/uploads/someone-else.webp")

    assert error.value.status_code == 403
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_returns_503_when_provider_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, HTTPException(status_code=503, detail="人脸认证服务未配置"))
    db = _Db(_Result(rows=[{"avatar": "/uploads/a.webp"}]))

    with pytest.raises(HTTPException) as error:
        await service.verify_avatar(db, 7, "/uploads/a.webp")

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_disabled_provider_does_not_consume_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "get_face_provider", lambda: DisabledFaceProvider())
    consumed = False

    async def track(_key: str, _limit: int) -> bool:
        nonlocal consumed
        consumed = True
        return True

    monkeypatch.setattr(service, "consume_daily", track)
    db = _Db(_Result(rows=[{"avatar": "/uploads/a.webp"}]))

    with pytest.raises(HTTPException) as error:
        await service.verify_avatar(db, 7, "/uploads/a.webp")

    assert error.value.status_code == 503
    assert consumed is False


@pytest.mark.asyncio
async def test_returns_422_with_remaining_when_not_matched(
    monkeypatch: pytest.MonkeyPatch, _quota: dict[str, Any]
) -> None:
    _quota["used"] = 2
    _use_provider(monkeypatch, FaceVerifyResult(verified=False, score=20.0, provider="mock"))
    db = _Db(_Result(rows=[{"avatar": "/uploads/a.webp"}]))

    with pytest.raises(HTTPException) as error:
        await service.verify_avatar(db, 7, "/uploads/a.webp")

    assert error.value.status_code == 422
    assert error.value.detail == {"code": "FACE_NOT_MATCHED", "remaining_attempts": 1}


@pytest.mark.asyncio
async def test_success_writes_face_verified_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, FaceVerifyResult(verified=True, score=98.0, provider="mock"))
    db = _Db(
        _Result(rows=[{"avatar": "/uploads/a.webp"}]),
        _Result(),  # INSERT ... ON DUPLICATE KEY UPDATE
        _Result(),  # business_audit_log
    )

    response = await service.verify_avatar(db, 7, "/uploads/a.webp")

    assert response.verified is True
    assert response.provider == "mock"
    assert response.remaining_attempts == 2
    assert db.committed is True
    assert any("face_verified = 1" in sql for sql, _ in db.calls)
    assert any("user.face_verify" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_daily_limit_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deny(_key: str, _limit: int) -> bool:
        return False

    _use_provider(monkeypatch, FaceVerifyResult(verified=True, score=98.0, provider="mock"))
    monkeypatch.setattr(service, "consume_daily", deny)
    db = _Db(_Result(rows=[{"avatar": "/uploads/a.webp"}]))

    with pytest.raises(HTTPException) as error:
        await service.verify_avatar(db, 7, "/uploads/a.webp")

    assert error.value.status_code == 429

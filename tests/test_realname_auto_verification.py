"""实名提交接入在线核验后的状态流转与人工兜底。"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.auth import RealNameRequest
from app.services import auth as auth_service
from app.services.identity_verify.base import IdentityVerifyResult
from app.services.identity_verify.providers import DisabledIdentityProvider


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        if not self._rows:
            return None
        row = self._rows[0]
        return row.get("realname_status") if isinstance(row, dict) else row


class _Tx:
    async def __aenter__(self) -> "_Tx":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _Db:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rolled_back = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()

    async def rollback(self) -> None:
        self.rolled_back = True

    def begin(self) -> _Tx:
        return _Tx()


class _Provider:
    name = "mock"

    def __init__(self, result: IdentityVerifyResult | Exception) -> None:
        self.result = result

    async def verify(self, request: Any) -> IdentityVerifyResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


_REQUEST = RealNameRequest(real_name="张三", id_card="110101199001011234")


@pytest.fixture(autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    async def enabled(_db: Any) -> bool:
        return True

    async def allow_same_id(_db: Any) -> bool:
        return True

    monkeypatch.setattr(auth_service, "mini_realname_enabled", enabled)
    monkeypatch.setattr(auth_service, "same_id_multiple_accounts_allowed", allow_same_id)


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(auth_service, "get_identity_provider", lambda: provider)


def _db_for_write() -> _Db:
    return _Db(
        _Result([{"realname_status": 0}]),  # pre-check
        _Result([{"realname_status": 0}]),  # write re-check
        _Result(),  # INSERT ... ON DUPLICATE KEY UPDATE
        _Result(),  # UPDATE reviewed_by/reviewed_at
        _Result(),  # UPDATE users.birthday
    )


@pytest.mark.asyncio
async def test_disabled_provider_keeps_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, DisabledIdentityProvider())
    db = _Db(
        _Result([{"realname_status": 0}]),
        _Result(),
        _Result(),
        _Result(),
    )

    response = await auth_service.submit_realname(db, 7, _REQUEST)

    assert response["status"] == 1
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 1
    assert insert_params["reason"] is None


@pytest.mark.asyncio
async def test_verified_provider_marks_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, _Provider(IdentityVerifyResult(verified=True, provider="mock")))
    db = _db_for_write()

    response = await auth_service.submit_realname(db, 7, _REQUEST)

    assert response["status"] == 2
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 2
    assert insert_params["step"] == 4
    assert insert_params["reason"] is None
    assert any("reviewed_at = UTC_TIMESTAMP()" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_rejected_provider_marks_failed_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(
        monkeypatch,
        _Provider(IdentityVerifyResult(verified=False, provider="mock", reason="doc_mismatch")),
    )
    db = _db_for_write()

    response = await auth_service.submit_realname(db, 7, _REQUEST)

    assert response["status"] == 3
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 3
    assert insert_params["step"] == 5
    assert insert_params["reason"] == "doc_mismatch"


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, _Provider(RuntimeError("vendor down")))
    db = _db_for_write()

    response = await auth_service.submit_realname(db, 7, _REQUEST)

    assert response["status"] == 1
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 1

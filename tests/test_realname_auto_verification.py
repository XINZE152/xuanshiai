"""实名提交接入在线核验后的状态流转与人工兜底。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.schemas.auth import RealNameRequest
from app.services import auth as auth_service
from app.services.identity_verify.base import IdentityVerifyResult
from app.services.identity_verify.providers import DisabledIdentityProvider


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def mappings(self) -> "_Result":
        return self


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


def _snapshot(status: int, version: int) -> tuple[_Result, _Result]:
    return _Result([{"id": 7, "status": 1}]), _Result([{"realname_status": status, "retry_count": version}])


def _db_for_write() -> _Db:
    return _Db(*_snapshot(0, 0), *_snapshot(0, 0))


@pytest.mark.asyncio
async def test_disabled_provider_keeps_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, DisabledIdentityProvider())
    db = _Db(*_snapshot(0, 0))

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
    assert insert_params["reason"] == response["realname_fail_reason"]
    assert "doc_mismatch" not in str(response)
    assert "doc_mismatch" not in str(db.calls)


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, _Provider(RuntimeError("vendor down")))
    db = _db_for_write()

    response = await auth_service.submit_realname(db, 7, _REQUEST)

    assert response["status"] == 1
    assert response["realname_fail_reason"] is None
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [1, 2, 4])
async def test_submit_cannot_replace_pending_review_or_pass(monkeypatch, status):
    _use_provider(monkeypatch, DisabledIdentityProvider())
    db = _Db(*_snapshot(status, 1))
    with pytest.raises(HTTPException) as error:
        await auth_service.submit_realname(db, 7, _REQUEST)
    assert error.value.status_code == 409
    assert len(db.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,version", [(3, 0), (5, 1)])
async def test_resubmission_clears_previous_rejection_and_face_material(monkeypatch, status, version):
    _use_provider(monkeypatch, DisabledIdentityProvider())
    db = _Db(*_snapshot(status, version))
    response = await auth_service.submit_realname(db, 7, _REQUEST)
    sql, params = next((sql, p) for sql, p in db.calls if "INSERT INTO user_auth" in sql)
    assert response["status"] == 1 and response["realname_fail_reason"] is None
    assert params["next_version"] == 2
    assert "face_verified = 0" in sql and "face_photo = NULL" in sql
    assert "id_card_front = NULL" in sql


@pytest.mark.asyncio
async def test_stale_provider_result_does_not_replace_new_attempt(monkeypatch):
    _use_provider(monkeypatch, _Provider(IdentityVerifyResult(verified=True, provider="mock")))
    db = _Db(*_snapshot(3, 1), *_snapshot(3, 2))
    with pytest.raises(HTTPException) as error:
        await auth_service.submit_realname(db, 7, _REQUEST)
    assert error.value.status_code == 409
    assert all("INSERT" not in sql for sql, _ in db.calls)


@pytest.mark.parametrize("impersonated_by", [None, 88])
def test_realname_routes_do_not_echo_request_inputs_or_historical_vendor_text(impersonated_by):
    app = FastAPI()
    app.include_router(auth_routes.router)
    db = _Db(_Result([{
        "phone": None, "nickname": None, "avatar": None, "status": 1,
        "phone_verified_at": None, "realname_status": 3, "face_verified": 0,
        "fail_reason": "VENDOR_ERROR with private data",
    }]))
    app.dependency_overrides[auth_routes.get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
    assert not db.calls
    app.dependency_overrides[auth_routes.get_current_user] = lambda: auth_routes.CurrentUser(
        id=7, session_id=1, phone=None, status=1, realname_status=1, impersonated_by=impersonated_by,
    )
    with TestClient(app) as client:
        response = client.post("/auth/realname", json={"real_name": "PRIVATE_NAME", "id_card": "PRIVATE_ID"})
        assert response.status_code == 422
        assert "PRIVATE" not in response.text
        response = client.get("/auth/me", params={"user_id": 8})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["id"] == 7
        assert response.json()["realname_status"] == 3
        expected_reason = auth_service.REALNAME_GENERIC_FAILURE if impersonated_by is None else None
        assert response.json()["realname_fail_reason"] == expected_reason
        assert "VENDOR_ERROR" not in response.text
        assert db.calls[0][1] == {"id": 7}

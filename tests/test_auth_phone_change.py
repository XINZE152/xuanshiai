"""换绑手机号：旧号证明 + 新号验证码 + 会话失效（2026-11-02，Q47 / 安全加固）。

修复前的两个问题：
1. ``POST /auth/bind-phone`` 对**已绑定账号**直接覆盖 phone，只校验新号验证码 ——
   任何持有有效 token 的人都能用自己控制的号码改绑并接管账号；
2. 用户没有任何自助换绑入口（前端只能提示「接口暂未提供」）。

本文件锁定：bind 只服务未绑定账号（已绑定 409）；换绑必须旧号（验证码或密码）
+ 新号双证明，成功后除当前设备外全部会话失效。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.auth import BindPhoneRequest, ChangePhoneRequest
from app.services import auth as service


class _Result:
    def __init__(self, row: dict[str, Any] | None = None, scalar: Any = None) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None

    def scalar(self) -> Any:
        return self._scalar


class _Tx:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def __aenter__(self) -> _Db:
        return self._db

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Db:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> _Tx:
        return _Tx(self)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()

    async def commit(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_sms(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """短信网关全部短路，并记录被验证的 (phone, purpose)。"""
    verified: list[tuple[str, str]] = []

    async def fake_verify(phone: str, purpose: str, _code: str) -> None:
        verified.append((phone, purpose))

    monkeypatch.setattr(service.sms_store, "verify", fake_verify)
    return verified


def _bind(phone: str = "13900000001") -> BindPhoneRequest:
    return BindPhoneRequest.model_validate({"phone": phone, "code": "123456", "purpose": "bind_phone"})


def _change(**overrides: Any) -> ChangePhoneRequest:
    payload: dict[str, Any] = {
        "new_phone": "13900000002",
        "new_code": "222222",
        "old_code": "111111",
    }
    payload.update(overrides)
    return ChangePhoneRequest.model_validate(payload)


def test_change_requires_one_proof() -> None:
    with pytest.raises(ValidationError):
        ChangePhoneRequest.model_validate({"new_phone": "13900000002", "new_code": "222222"})


@pytest.mark.asyncio
async def test_bind_rejects_already_bound_account() -> None:
    # bind_phone 先 FOR UPDATE 取当前用户行再判断绑定态，
    # 因此首个结果必须是 {id, phone} 行（不是 scalar 预检）。
    db = _Db(_Result({"id": 7, "phone": "13900000000"}))

    with pytest.raises(HTTPException) as error:
        await service.bind_phone(db, 7, _bind(), None)

    assert error.value.status_code == 409
    assert "换绑" in str(error.value.detail)


@pytest.mark.asyncio
async def test_bind_still_works_for_unbound_account(_no_real_sms: list[tuple[str, str]]) -> None:
    db = _Db(
        _Result({"id": 7, "phone": None}),           # FOR UPDATE 当前用户：未绑定
        _Result(None),                              # 新号未被占用
        _Result(None),                              # phone_region 列探测
    )

    await service.bind_phone(db, 7, _bind(), None)

    assert _no_real_sms == [("13900000001", "bind_phone")]
    assert any("UPDATE users SET phone" in statement for statement, _ in db.calls)


@pytest.mark.asyncio
async def test_change_rejects_same_phone() -> None:
    db = _Db(_Result({"id": 7, "phone": "13900000002", "password": None}))

    with pytest.raises(HTTPException) as error:
        await service.change_phone(db, 7, 1, _change(), None)

    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_change_requires_bound_phone() -> None:
    db = _Db(_Result({"id": 7, "phone": None, "password": None}))

    with pytest.raises(HTTPException) as error:
        await service.change_phone(db, 7, 1, _change(), None)

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_change_rejects_wrong_password() -> None:
    db = _Db(_Result({"id": 7, "phone": "13900000001", "password": "$2b$12$invalid"}))

    with pytest.raises(HTTPException) as error:
        await service.change_phone(db, 7, 1, _change(old_code=None, password="wrong-pass"), None)

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_change_verifies_both_ends_and_revokes_other_sessions(
    _no_real_sms: list[tuple[str, str]],
) -> None:
    db = _Db(
        _Result({"id": 7, "phone": "13900000001", "password": None}),
        _Result(None),  # 新号未被占用
    )

    await service.change_phone(db, 7, 42, _change(), None)

    assert _no_real_sms == [
        ("13900000001", "change_phone_old"),
        ("13900000002", "change_phone_new"),
    ]
    updates = {statement: params for statement, params in db.calls if "UPDATE" in statement}
    phone_update = next(
        params for statement, params in updates.items() if "UPDATE users SET phone" in statement
    )
    assert phone_update["phone"] == "13900000002"
    session_update = next(
        (statement, params)
        for statement, params in db.calls
        if "UPDATE user_session" in statement
    )
    statement, params = session_update
    assert params["sid"] == 42, "必须保留当前设备会话"
    assert "id <> :sid" in statement


@pytest.mark.asyncio
async def test_change_rejects_occupied_new_phone() -> None:
    db = _Db(
        _Result({"id": 7, "phone": "13900000001", "password": None}),
        _Result({"id": 9}),
    )

    with pytest.raises(HTTPException) as error:
        await service.change_phone(db, 7, 42, _change(), None)

    assert error.value.status_code == 409


def test_route_is_registered() -> None:
    import warnings

    warnings.filterwarnings("ignore")
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/auth/phone" in paths
    assert "put" in paths["/api/v1/auth/phone"]

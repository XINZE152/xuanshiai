"""微信一键手机号登录/绑定回归（2026-11-18）。

覆盖：
- getPhoneNumber code 的 Mock 解析；
- 老微信账号无手机号时用授权手机号补绑，need_bind_phone 变 false；
- 新用户直接带 openid + 已实名手机号建号；
- 手机号被其他账号占用、微信号已绑其他手机号时拒绝，不静默改绑；
- 已登录账号用授权手机号补绑。

无真实 DB / 无真实微信调用：全部走记录型桩。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas.auth import WechatPhoneBindRequest, WechatPhoneLoginRequest
from app.services import auth as auth_service


class _Result:
    def __init__(self, *, first=None, lastrowid: int = 0, all_rows=None) -> None:
        self._first = first
        self.lastrowid = lastrowid
        self._all = all_rows or []

    def mappings(self) -> "_Result":
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _Ctx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc) -> bool:
        return False


class _StubDB:
    """按顺序吐出预置结果，并记录压平后的 (sql, params)。"""

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])

    async def execute(self, statement, params=None):  # noqa: ANN001
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        return self._results.pop(0) if self._results else _Result()

    def begin(self) -> _Ctx:
        return _Ctx()


def _statements_starting(db: _StubDB, prefix: str) -> list[tuple[str, dict]]:
    return [item for item in db.statements if item[0].startswith(prefix)]


def _stub_login_dependencies(monkeypatch: pytest.MonkeyPatch, phone: str = "13800138000") -> None:
    async def _identity(_code: str) -> dict[str, str]:
        return {"openid": "openid-phone", "unionid": "unionid-phone"}

    async def _phone_code(_code: str) -> str:
        return phone

    async def _noop(*_args, **_kwargs) -> None:
        return None

    async def _columns(_db) -> frozenset[str]:
        return frozenset({"source", "phone", "phone_verified_at"})

    async def _registration_open(_db) -> bool:
        return True

    async def _session(*_args, **_kwargs) -> dict:
        return {"access_token": "access", "refresh_token": "refresh", "expires_in": 60, "user_id": 7}

    monkeypatch.setattr(auth_service, "exchange_wechat_code", _identity)
    monkeypatch.setattr(auth_service, "exchange_wechat_phone_code", _phone_code)
    monkeypatch.setattr(auth_service, "ensure_default_user_role", _noop)
    monkeypatch.setattr(auth_service, "touch_login", _noop)
    monkeypatch.setattr(auth_service, "_users_columns", _columns)
    monkeypatch.setattr(auth_service, "platform_registration_open", _registration_open)
    monkeypatch.setattr(auth_service, "create_session", _session)


@pytest.mark.asyncio
async def test_mock_phone_code_parses_only_mainland_mobile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service.settings, "wechat_provider", "mock")
    monkeypatch.setattr(auth_service.settings, "is_test_mode", True)

    assert await auth_service.exchange_wechat_phone_code("mock-phone-13800138000") == "13800138000"
    with pytest.raises(HTTPException) as bad:
        await auth_service.exchange_wechat_phone_code("mock-phone-12345")
    assert bad.value.status_code == 400


@pytest.mark.asyncio
async def test_phone_login_existing_user_gets_phone_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_login_dependencies(monkeypatch)
    db = _StubDB([
        _Result(first={"id": 7, "phone": None, "status": 1}),
        _Result(first=None),
    ])

    tokens = await auth_service.login_wechat_with_phone(
        db,
        WechatPhoneLoginRequest(code="stub", phone_code="stub-phone"),
        None,
        None,
    )

    updates = _statements_starting(db, "UPDATE users")
    assert updates, db.statements
    assert updates[0][1] == {"phone": "13800138000", "id": 7}
    assert "phone_verified_at" in updates[0][0]
    assert tokens["need_bind_phone"] is False


@pytest.mark.asyncio
async def test_phone_login_new_user_created_with_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_login_dependencies(monkeypatch)
    db = _StubDB([
        _Result(first=None),
        _Result(first=None),
        _Result(lastrowid=7),
    ])

    tokens = await auth_service.login_wechat_with_phone(
        db,
        WechatPhoneLoginRequest(code="stub", phone_code="stub-phone"),
        "1.2.3.4",
        "pytest",
    )

    inserts = _statements_starting(db, "INSERT INTO users")
    assert inserts, db.statements
    sql, params = inserts[0]
    assert "phone" in sql and "phone_verified_at" in sql and "openid" in sql
    assert params["phone"] == "13800138000"
    assert params["openid"] == "openid-phone"
    assert params["source"] == auth_service.SOURCE_SELF_REGISTER
    assert tokens["need_bind_phone"] is False


@pytest.mark.asyncio
async def test_phone_login_rejects_wechat_account_bound_to_another_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_login_dependencies(monkeypatch, phone="13900139000")
    db = _StubDB([_Result(first={"id": 7, "phone": "13800138000", "status": 1})])

    with pytest.raises(HTTPException) as error:
        await auth_service.login_wechat_with_phone(
            db,
            WechatPhoneLoginRequest(code="stub", phone_code="stub-phone"),
            None,
            None,
        )
    assert error.value.status_code == 409
    assert not _statements_starting(db, "UPDATE users")


@pytest.mark.asyncio
async def test_phone_login_rejects_phone_bound_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_login_dependencies(monkeypatch)
    db = _StubDB([
        _Result(first={"id": 7, "phone": None, "status": 1}),
        _Result(first={"id": 99}),
    ])

    with pytest.raises(HTTPException) as error:
        await auth_service.login_wechat_with_phone(
            db,
            WechatPhoneLoginRequest(code="stub", phone_code="stub-phone"),
            None,
            None,
        )
    assert error.value.status_code == 409
    assert not _statements_starting(db, "UPDATE users")


@pytest.mark.asyncio
async def test_bind_wechat_phone_binds_and_rejects_second_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _phone_code(_code: str) -> str:
        return "13800138000"

    monkeypatch.setattr(auth_service, "exchange_wechat_phone_code", _phone_code)

    db = _StubDB([
        _Result(first={"phone": None, "status": 1}),
        _Result(first=None),
    ])
    await auth_service.bind_wechat_phone(db, 7, WechatPhoneBindRequest(phone_code="stub"))
    update = _statements_starting(db, "UPDATE users")[0]
    assert update[1] == {"phone": "13800138000", "id": 7}

    conflict = _StubDB([_Result(first={"phone": "13900139000", "status": 1})])
    with pytest.raises(HTTPException) as error:
        await auth_service.bind_wechat_phone(conflict, 7, WechatPhoneBindRequest(phone_code="stub"))
    assert error.value.status_code == 409


def test_phone_code_request_contract() -> None:
    assert re.fullmatch(r"1[3-9]\d{9}", "13800138000")
    request = WechatPhoneLoginRequest(code="login-code", phone_code="phone-code")
    assert request.platform is None
    assert WechatPhoneBindRequest(phone_code="phone-code").phone_code == "phone-code"

"""手机号变更后属地失效（A12-属地，2026-11-04）。

覆盖四个 users.phone 写入点中的行为契约：
- 微信补绑、用户换绑、后台编辑会员、推广红娘后台改号；
- 只有号码真的变化且 users.phone_region 列存在时才清空；
- 列未迁移时静默跳过，不能让换绑/编辑因 Unknown column 失败。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas.auth import BindPhoneRequest, ChangePhoneRequest
from app.schemas.matchmaker_member_admin import MatchmakerMemberUpdate
from app.services import auth as auth_service
from app.services import matchmaker_member_admin as member_admin
from app.services.promoter_staff_admin import _update_promoter_phone
from app.services.user_columns import should_reset_phone_region, users_has_column

NOW = datetime(2026, 11, 4, 12, 0, 0)


class _Result:
    def __init__(self, *, row: dict[str, Any] | None = None, scalar: Any = None) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None

    def scalar(self) -> Any:
        return self._scalar

    def all(self) -> list[Any]:
        return []


class _Tx:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def __aenter__(self) -> _Db:
        return self._db

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Db:
    """按 SQL 内容返回结果的轻量桩，避免依赖真实 MySQL。"""

    def __init__(self, *, phone: str | None, column_exists: bool = True) -> None:
        self.phone = phone
        self.column_exists = column_exists
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.committed = False

    def begin(self) -> _Tx:
        return _Tx(self)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(params or {})))
        if "information_schema.COLUMNS" in sql and "TABLE_NAME = 'users'" in sql:
            return _Result(row={"1": 1} if self.column_exists else None)
        if "SELECT phone FROM users WHERE id" in sql:
            return _Result(scalar=self.phone)
        if "SELECT id, phone, password FROM users" in sql:
            return _Result(row={"id": 7, "phone": self.phone, "password": None})
        if "SELECT id, phone FROM users" in sql:
            return _Result(row={"id": 7, "phone": self.phone})
        if "SELECT id FROM users WHERE phone" in sql:
            return _Result()
        if sql.startswith("SELECT u.id"):
            return _Result(row=self._member_row())
        return _Result()

    async def commit(self) -> None:
        self.committed = True

    def _member_row(self) -> dict[str, Any]:
        return {
            "id": 9,
            "nickname": "小宣",
            "phone": self.phone,
            "gender": 1,
            "status": 1,
            "created_at": NOW,
            "updated_at": NOW,
            "matchmaker_id": 7,
            "vip_end_at": None,
            "is_vip": 0,
        }

    def phone_update(self) -> tuple[str, dict[str, Any]]:
        return next(
            (sql, params) for sql, params in self.calls if sql.startswith("UPDATE users SET phone")
        )


@pytest.fixture(autouse=True)
def _no_real_sms(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_verify(_phone: str, _purpose: str, _code: str) -> None:
        return None

    monkeypatch.setattr(auth_service.sms_store, "verify", fake_verify)


def _bind(phone: str = "13900000002") -> BindPhoneRequest:
    return BindPhoneRequest.model_validate({"phone": phone, "code": "123456", "purpose": "bind_phone"})


def _change() -> ChangePhoneRequest:
    return ChangePhoneRequest.model_validate(
        {"new_phone": "13900000002", "new_code": "222222", "old_code": "111111"}
    )


@pytest.mark.asyncio
async def test_column_probe_and_reset_decision() -> None:
    db = _Db(phone=None, column_exists=True)
    assert await users_has_column(db, "phone_region") is True
    assert await should_reset_phone_region(db, phone_changed=True) is True
    assert await should_reset_phone_region(db, phone_changed=False) is False

    missing = _Db(phone=None, column_exists=False)
    assert await users_has_column(missing, "phone_region") is False
    assert await should_reset_phone_region(missing, phone_changed=True) is False


@pytest.mark.asyncio
async def test_bind_phone_clears_phone_region() -> None:
    db = _Db(phone=None, column_exists=True)
    await auth_service.bind_phone(db, 7, _bind(), None)

    sql, params = db.phone_update()
    assert "phone_region = NULL" in sql
    assert params["phone"] == "13900000002"


@pytest.mark.asyncio
async def test_bind_phone_skips_reset_when_column_missing() -> None:
    db = _Db(phone=None, column_exists=False)
    await auth_service.bind_phone(db, 7, _bind(), None)

    sql, _ = db.phone_update()
    assert "phone_region" not in sql


@pytest.mark.asyncio
async def test_change_phone_clears_phone_region() -> None:
    db = _Db(phone="13900000001", column_exists=True)
    await auth_service.change_phone(db, 7, 42, _change(), None)

    sql, params = db.phone_update()
    assert "phone_region = NULL" in sql
    assert params["phone"] == "13900000002"


@pytest.mark.asyncio
async def test_member_update_clears_region_only_when_phone_changes() -> None:
    changed = _Db(phone="13900000001", column_exists=True)
    await member_admin.update_member(
        changed, 9, MatchmakerMemberUpdate(phone="13900000002"), 1,
        scope="1 = 1", scope_params={},
    )
    sql, params = changed.phone_update()
    assert "phone_region = :phone_region" in sql
    assert params["phone_region"] is None

    unchanged = _Db(phone="13900000001", column_exists=True)
    await member_admin.update_member(
        unchanged, 9, MatchmakerMemberUpdate(phone="13900000001"), 1,
        scope="1 = 1", scope_params={},
    )
    sql, params = unchanged.phone_update()
    assert "phone_region" not in sql
    assert "phone_region" not in params


@pytest.mark.asyncio
async def test_promoter_phone_update_clears_region_only_when_changed() -> None:
    changed = _Db(phone="13900000001", column_exists=True)
    await _update_promoter_phone(changed, 7, "13900000002")
    sql, _ = changed.phone_update()
    assert "phone_region = NULL" in sql

    unchanged = _Db(phone="13900000001", column_exists=True)
    await _update_promoter_phone(unchanged, 7, "13900000001")
    sql, _ = unchanged.phone_update()
    assert "phone_region" not in sql

    missing = _Db(phone="13900000001", column_exists=False)
    await _update_promoter_phone(missing, 7, "13900000002")
    sql, _ = missing.phone_update()
    assert "phone_region" not in sql


@pytest.mark.asyncio
async def test_member_update_rejects_taken_phone_without_writing() -> None:
    class _TakenDb(_Db):
        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
            sql = " ".join(str(statement).split())
            if "SELECT id FROM users WHERE phone" in sql:
                return _Result(scalar=99)
            return await super().execute(statement, params)

    db = _TakenDb(phone="13900000001", column_exists=True)
    with pytest.raises(HTTPException) as error:
        await member_admin.update_member(
            db, 9, MatchmakerMemberUpdate(phone="13900000002"), 1,
            scope="1 = 1", scope_params={},
        )
    assert error.value.status_code == 409
    assert not any(sql.startswith("UPDATE users SET") for sql, _ in db.calls)

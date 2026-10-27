"""申请认识链路的实名口径（2026-10-27 需求 1）。

覆盖四点：
1. 对方未实名**不再**拦截发起，回包带 ``target_realname_required``；
2. 发起方自己未实名仍是 403，但 detail 变成带业务码的对象；
3. 接收方未实名点同意/拒绝返回 403 ``REALNAME_REQUIRED``；
4. 后台 ``line.realname=yes`` 时接收方闸口放行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.services import discovery


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        first: dict[str, Any] | None = None,
        scalar: Any = None,
        lastrowid: int = 0,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._one = one
        self._first = first
        self._scalar = scalar
        self._lastrowid = lastrowid
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, Any]:
        assert self._one is not None
        return self._one

    def first(self) -> dict[str, Any] | None:
        return self._first

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar

    @property
    def lastrowid(self) -> int:
        return self._lastrowid


def _viewer(realname_status: int = 2) -> dict[str, Any]:
    return {
        "completion_score": 100,
        "phone": "13800000000",
        "realname_status": realname_status,
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, viewer_realname: int, line_perm: dict[str, Any]) -> None:
    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def allow(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def viewer_context(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _viewer(viewer_realname)

    async def perms(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return line_perm

    async def quota_key(*_args: Any, **_kwargs: Any) -> str:
        return "apply:quota"

    async def not_vip(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def consume(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(discovery, "ensure_user_allowed", noop)
    monkeypatch.setattr(discovery, "_lock_user_pair", noop)
    monkeypatch.setattr(discovery, "_ensure_target", noop)
    monkeypatch.setattr(discovery, "_expire_pending_applications", noop)
    monkeypatch.setattr(discovery, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery, "match_line_permissions", perms)
    monkeypatch.setattr(discovery, "_quota_key", quota_key)
    monkeypatch.setattr(discovery, "_is_vip", not_vip)
    monkeypatch.setattr(discovery, "_consume_apply_quota", consume)
    monkeypatch.setattr(discovery, "_record_quota_usage", noop)
    monkeypatch.setattr(discovery, "_notify", noop)
    monkeypatch.setattr(discovery, "refund_daily", noop)
    assert allow  # 保留以便后续扩展额度断言


class _CreateDb:
    """够 create_application 跑完的最小假库。"""

    def __init__(self, *, target_realname_status: int) -> None:
        self.target_realname_status = target_realname_status
        self.statements: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM user_auth WHERE user_id = :target_id" in sql:
            return _Result(first={"realname_status": self.target_realname_status})
        if "SELECT id, status FROM match_apply" in sql:
            return _Result(first=None)
        if sql.strip().startswith("INSERT INTO match_apply"):
            return _Result(lastrowid=7)
        if "FROM match_apply WHERE id = :id" in sql:
            return _Result(
                one={
                    "id": 7,
                    "from_user_id": 1,
                    "to_user_id": 2,
                    "message": "hi",
                    "status": 0,
                    "expire_at": None,
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                }
            )
        return _Result()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_application_to_unverified_target_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """对方未实名不再抛 403，只回填 target_realname_required。"""
    _patch_common(
        monkeypatch,
        viewer_realname=2,
        line_perm={"realname_allowed": False, "be_linked_allowed": False, "apply_daily": None, "active": True},
    )
    db = _CreateDb(target_realname_status=0)

    created = await discovery.create_application(
        db, 1, 2, discovery.ApplicationCreateRequest(message="hi")
    )

    assert created.status == 0
    assert created.target_realname_required is True
    assert not any("暂时无法发起认识申请" in statement for statement in db.statements)


@pytest.mark.asyncio
async def test_application_to_verified_target_does_not_require_realname(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(
        monkeypatch,
        viewer_realname=2,
        line_perm={"realname_allowed": False, "be_linked_allowed": False, "apply_daily": None, "active": True},
    )
    db = _CreateDb(target_realname_status=2)

    created = await discovery.create_application(
        db, 1, 2, discovery.ApplicationCreateRequest(message="hi")
    )

    assert created.target_realname_required is False


@pytest.mark.asyncio
async def test_viewer_without_realname_gets_business_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """发起方未实名仍是 403，但 detail 带 code，前端可分流到实名页。"""
    _patch_common(
        monkeypatch,
        viewer_realname=0,
        line_perm={"realname_allowed": False, "be_linked_allowed": None, "apply_daily": None, "active": True},
    )

    with pytest.raises(discovery.HTTPException) as error:
        await discovery.create_application(
            _CreateDb(target_realname_status=2), 1, 2, discovery.ApplicationCreateRequest(message="hi")
        )

    assert error.value.status_code == 403
    assert isinstance(error.value.detail, dict)
    assert error.value.detail["code"] == "REALNAME_REQUIRED"


class _RespondDb:
    def __init__(self, *, responder_realname_status: int) -> None:
        self.responder_realname_status = responder_realname_status
        self.statements: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM match_apply" in sql and "FOR UPDATE" in sql:
            return _Result(
                first={
                    "id": 9,
                    "from_user_id": 2,
                    "to_user_id": 1,
                    "message": None,
                    "status": 0,
                    "expire_at": None,
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                }
            )
        if "FROM match_apply WHERE id = :id" in sql:
            return _Result(
                one={
                    "id": 9,
                    "from_user_id": 2,
                    "to_user_id": 1,
                    "message": None,
                    "status": 2,
                    "expire_at": None,
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                }
            )
        if "FROM user_auth WHERE user_id = :user_id" in sql:
            return _Result(first={"realname_status": self.responder_realname_status})
        return _Result()

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_responder_without_realname_cannot_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(
        monkeypatch,
        viewer_realname=0,
        line_perm={"realname_allowed": False, "be_linked_allowed": None, "apply_daily": None, "active": True},
    )
    db = _RespondDb(responder_realname_status=0)

    with pytest.raises(discovery.HTTPException) as error:
        await discovery.respond_application(db, 1, 9, accepted=True)

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "REALNAME_REQUIRED"
    assert not any("UPDATE match_apply SET status" in statement for statement in db.statements)


@pytest.mark.asyncio
async def test_responder_gate_follows_admin_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """后台卡片4 line.realname=yes 时，未实名也可回应（保持后台可控）。"""
    _patch_common(
        monkeypatch,
        viewer_realname=0,
        line_perm={"realname_allowed": True, "be_linked_allowed": None, "apply_daily": None, "active": True},
    )
    db = _RespondDb(responder_realname_status=0)

    await discovery.respond_application(db, 1, 9, accepted=False)

    assert any("UPDATE match_apply SET status" in statement for statement in db.statements)
